"""
Fazle Core — Ollama AI
Role-aware prompts for human-quality Bengali replies.

Key fixes vs v1:
- Semaphore limits Ollama to 1 concurrent request (prevents timeout cascade
  when 2+ messages arrive at same time; tested: 2 concurrent = 1 times out)
- Timeout increased: classify→15s, generate→45s
- Intent-aware system prompts give focused context per message type
- num_predict reduced 200→120 for faster responses
"""
import asyncio
import logging
import httpx
from app.config import get_settings

log = logging.getLogger("fazle.ollama")

# Serializes all Ollama calls — prevents concurrent-request timeout failures.
# Ollama on this hardware takes ~17s per generate; two simultaneous = one times out.
_ollama_sem = asyncio.Semaphore(1)


# ── Base system prompt ─────────────────────────────────────────────────────────
_BASE = """\
তুমি ফজলে — আল-আকসা সিকিউরিটি সার্ভিস অ্যান্ড ট্রেডিং সেন্টার, চট্টগ্রাম-এর ফ্রন্ট ডেস্ক সহকারী।

মূল নিয়ম:
১. সবসময় বাংলায় জবাব দাও, যদি না ইংরেজিতে জিজ্ঞেস করা হয়।
২. জবাব সংক্ষিপ্ত রাখো — সর্বোচ্চ ৩-৪ বাক্য।
৩. শুধুমাত্র দেওয়া তথ্য ব্যবহার করো। নিজে থেকে বেতন বা পরিমাণ বানিও না।
৪. তথ্য না জানলে বলো: "অফিসে যোগাযোগ করুন অথবা পরে আবার জিজ্ঞেস করুন।"
৫. রোবোটিক বা ইংরেজি phrase ব্যবহার করো না।
৬. সম্মানজনক ও আন্তরিক ভাষায় কথা বলো।
"""

# ── Role-specific tone ─────────────────────────────────────────────────────────
_ROLE_PROMPTS: dict[str, str] = {
    "employee": "এই ব্যক্তি আমাদের কর্মী। তাদের সাথে ভাই/বোনের মতো আন্তরিকভাবে কথা বলো। শুধুমাত্র দেওয়া তথ্য দিয়ে জবাব দাও।",
    "client":   "এই ব্যক্তি আমাদের ক্লায়েন্ট বা কোম্পানি। পেশাদার ও ব্যবসায়িক টোনে কথা বলো। তাদের চাহিদা বুঝে প্রয়োজনীয় প্রশ্ন করো।",
    "new_lead": "এই ব্যক্তি নতুন — হয়তো চাকরি চান বা সেবা জানতে চান। উষ্ণ ও স্বাগতজনক টোনে কথা বলো। চাকরির ক্ষেত্রে নাম, বয়স, এলাকা জিজ্ঞেস করো।",
    "admin":    "এই ব্যক্তি অফিস অ্যাডমিন। সরাসরি ও কার্যকরভাবে তথ্য দাও।",
    "vendor":   "এই ব্যক্তি আমাদের ভেন্ডর বা সরবরাহকারী। পেশাদার টোনে কথা বলো।",
    "partner":  "এই ব্যক্তি আমাদের ব্যবসায়িক অংশীদার। সম্মানজনক ও সহযোগিতামূলক টোনে কথা বলো।",
    "known_contact": "এই ব্যক্তি আমাদের পরিচিত যোগাযোগ। সৌজন্যমূলক টোনে কথা বলো।",
}

# ── Intent-specific instructions (injected into prompt) ───────────────────────
_INTENT_HINTS: dict[str, str] = {
    "salary_query":  "কর্মী বেতন জিজ্ঞেস করছে। শুধুমাত্র নিচে দেওয়া ডেটাবেস তথ্য থেকে উত্তর দাও। বানিয়ে বলো না।",
    "payment_due":   "পেমেন্ট সংক্রান্ত প্রশ্ন। শুধুমাত্র দেওয়া তথ্য ব্যবহার করো।",
    "recruitment":   "চাকরির জন্য আগ্রহী। তাদের নাম, বয়স, অভিজ্ঞতা ও যোগাযোগ নম্বর জিজ্ঞেস করো।",
    "client_order":  "ক্লায়েন্ট এস্কর্ট সেবা চাইছে। ধন্যবাদ জানাও এবং মাদার ভেসেল, লাইটার ভেসেল, তারিখ ও লোকসংখ্যা নিশ্চিত করো।",
    "escort_duty":   "ডিউটি বা প্রোগ্রাম সম্পর্কিত। বিস্তারিত জানতে চাও।",
    "greeting":      "সালাম বা হ্যালো বলছে। ফজলে নামে পরিচয় দাও এবং কী সাহায্য দরকার জিজ্ঞেস করো।",
    "complaint":     "অভিযোগ জানাচ্ছে। সহানুভূতি দেখাও এবং অফিসে যোগাযোগ করতে বলো।",
    "leave":         "ছুটির আবেদন। রেকর্ড করা হয়েছে বলো এবং কারণ জিজ্ঞেস করো।",
    "join":          "যোগদান বা জয়েনিং সংক্রান্ত। তারিখ ও রিপোর্টিং অফিস নিশ্চিত করো।",
    "attendance":    "উপস্থিতি সংক্রান্ত। তথ্য পাওয়া গেছে বলো।",
    "slip_submission": "স্লিপ পাঠাচ্ছে। যাচাই করা হবে বলো।",
}


async def classify_intent_llm(text: str) -> str:
    """
    Use Ollama to classify intent when rule-based engine returns 'unknown'.
    Serialized through semaphore — waits in queue if Ollama is busy.
    """
    settings = get_settings()
    prompt = (
        "Classify this WhatsApp message into one category. "
        "Reply with ONLY the category name, nothing else.\n\n"
        "Categories: recruitment, salary_query, payment_due, escort_duty, complaint, "
        "client_order, leave, join, attendance, slip_submission, voice_note, greeting, unknown\n\n"
        f"Message: {text[:300]}\n\nCategory:"
    )
    async with _ollama_sem:
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                r = await client.post(
                    f"{settings.ollama_url}/api/generate",
                    json={
                        "model": settings.ollama_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"temperature": 0.1, "num_predict": 10},
                    },
                )
                if r.status_code == 200:
                    result = r.json().get("response", "").strip().lower().split()[0]
                    valid = {
                        "recruitment", "salary_query", "payment_due", "escort_duty",
                        "complaint", "client_order", "leave", "join", "attendance",
                        "slip_submission", "voice_note", "greeting", "unknown",
                    }
                    return result if result in valid else "unknown"
        except Exception as e:
            log.warning(f"Ollama classify error: {type(e).__name__}: {e}")
    return "unknown"


async def generate_reply(
    user_message: str,
    intent: str,
    db_context: str = "",
    history: str = "",
    role: str = "new_lead",
) -> str:
    """
    Generate a short Bengali reply using local Ollama.
    Serialized through semaphore to prevent concurrent timeout failures.
    """
    settings = get_settings()
    role_hint = _ROLE_PROMPTS.get(role, _ROLE_PROMPTS["new_lead"])
    intent_hint = _INTENT_HINTS.get(intent, "")
    context_block = f"\nডেটাবেস থেকে তথ্য:\n{db_context}\n" if db_context else ""
    history_block = f"\nআগের বার্তা:\n{history}\n" if history else ""
    intent_block = f"\nবর্তমান বিষয়: {intent_hint}\n" if intent_hint else ""

    prompt = (
        f"{_BASE}\n"
        f"{role_hint}\n"
        f"{intent_block}"
        f"{context_block}"
        f"{history_block}"
        f"\nপাঠানো বার্তা: {user_message[:400]}\n"
        f"জবাব (বাংলা, সর্বোচ্চ ৩-৪ বাক্য):"
    )

    async with _ollama_sem:
        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                r = await client.post(
                    f"{settings.ollama_url}/api/generate",
                    json={
                        "model": settings.ollama_model,
                        "prompt": prompt,
                        "stream": False,
                        "options": {"temperature": 0.4, "num_predict": 120},
                    },
                )
                if r.status_code == 200:
                    return r.json().get("response", "").strip()
        except Exception as e:
            log.error(f"Ollama generate error: {type(e).__name__}: {e}")

    # B25 hotfix: softer fallback. Quality gate (modules/draft_quality) matches
    # this string EXACTLY and stores the draft as 'rejected_fallback' rather
    # than queueing it for admin approval.
    try:
        from modules import observability as _obs
        _obs.inc("llm_fallback_total")
    except Exception:
        pass
    return "আপনার বার্তা পেয়েছি। একটু পরে বিস্তারিত জানাচ্ছি।"


async def check_ollama_health() -> dict:
    """Check if Ollama is reachable and return available models."""
    settings = get_settings()
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{settings.ollama_url}/api/tags")
            if r.status_code == 200:
                models = [m["name"] for m in r.json().get("models", [])]
                return {
                    "status": "ok",
                    "models": models,
                    "active_model": settings.ollama_model,
                    "queue_depth": _ollama_sem._value,
                }
    except Exception as e:
        return {"status": "error", "error": str(e)}
    return {"status": "error"}
