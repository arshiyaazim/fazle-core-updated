"""
Fazle Core — Knowledge Base
Provides instant auto-replies for common intents without LLM overhead.

Priority:
  1. DB lookup (fazle_knowledge_base table) — dynamic, admin-editable
  2. Hardcoded fallback constants — always works even if table missing

Source: extracted from resources/ txt files.
"""
import logging
import re
from typing import Optional

from app.database import fetch_one, fetch_all

log = logging.getLogger("fazle.knowledge_base")

# ── Hardcoded fallback templates ───────────────────────────────────────────────
# Each entry: (trigger_keywords, reply_text)
_FALLBACK: list[tuple[list[str], str]] = [
    (
        ["চাকরি কি", "কাজ কী", "কাজ কি", "details", "ডিউটি কত", "কত ঘণ্টা", "বিস্তারিত জান", "survey scout"],
        "ধন্যবাদ আমাদের সাথে যোগাযোগ করার জন্য 🙏\n\n"
        "👉 পদ: Survey Scout (সার্ভে স্কট)\n"
        "📌 কাজ: লাইটার জাহাজে মালামাল তদারকি, লোড–আনলোড হিসাব, চুরি প্রতিরোধ\n"
        "⏰ ডিউটি: গড়ে ৬–৮ ঘণ্টা | 🏠 থাকা: জাহাজেই (ফ্রি)\n"
        "💰 বেতন: ১০,০০০–১৮,০০০ টাকা | ✅ অভিজ্ঞতা লাগবে না\n\n"
        "📄 আবেদন করতে পাঠান: নাম, বয়স, শিক্ষা, ঠিকানা\n"
        "📲 WhatsApp: 01958 122322",
    ),
    (
        ["ঠিকানা", "লোকেশন", "address", "অফিস কোথায়", "একে খান", "google map", "কোথায় যাব"],
        "📍 আমাদের অফিস:\nআল-আকসা সিকিউরিটি সার্ভিস\n"
        "ভিক্টোরিয়া গেইট, একে খান মোড়, পাহাড়তলী, চট্টগ্রাম\n"
        "🕘 সকাল ৯টা – বিকাল ৫টা (শুক্রবার বন্ধ)\n"
        "📌 আনুন: NID ফটোকপি + ২ কপি ছবি\n"
        "📲 WhatsApp: 01958 122322",
    ),
    (
        ["করতে চাই", "আগ্রহী", "interested", "আমাকে নিবেন", "জয়েন করতে চাই", "apply"],
        "ধন্যবাদ আগ্রহ দেখানোর জন্য 🙏\n\n"
        "আবেদন করতে নিচের তথ্যগুলো পাঠান:\n"
        "1️⃣ পূর্ণ নাম\n2️⃣ বয়স\n3️⃣ শিক্ষাগত যোগ্যতা\n"
        "4️⃣ বর্তমান ঠিকানা (জেলাসহ)\n5️⃣ মোবাইল নম্বর\n\n"
        "📲 WhatsApp: 01958 122322\n"
        "✅ কোনো ভর্তি ফি বা জামানত লাগবে না",
    ),
    (
        ["বাটপার", "ভুয়া", "fake", "প্রতারক", "ধোঁকাবাজ", "fraud"],
        "আল-আকসা সিকিউরিটি সার্ভিস একটি নিবন্ধিত প্রতিষ্ঠান।\n"
        "✅ কোনো ফি বা জামানত নেওয়া হয় না\n"
        "✅ বেতন প্রতি মাসে নিয়মিত প্রদান করা হয়\n"
        "📍 ভিক্টোরিয়া গেইট, একে খান মোড়, পাহাড়তলী, চট্টগ্রাম\n"
        "📲 01958 122322 — সরাসরি অফিসে এসে যাচাই করুন।",
    ),
    (
        ["কী কী লাগবে", "কাগজপত্র", "NID", "ছবি", "বয়সসীমা", "certificate", "কী নিয়ে আসব"],
        "📌 আবেদনের জন্য যা লাগবে:\n"
        "✔ বয়স: ১৮–৪৫ বছর | ✔ শিক্ষা: ন্যূনতম অষ্টম শ্রেণি\n"
        "✔ অভিজ্ঞতা লাগবে না — ৪৫ দিন ট্রেনিং দেওয়া হবে\n\n"
        "📄 অফিসে আনুন:\n1. NID / জন্ম নিবন্ধন (ফটোকপি)\n"
        "2. পাসপোর্ট সাইজ ছবি (২ কপি)\n"
        "✅ সার্টিফিকেট না থাকলেও আবেদন করা যাবে",
    ),
    (
        ["মাদ্রাসা", "সার্টিফিকেট নেই", "দাখিল", "আলিম", "ক্লাস ৮", "পড়তে পারি"],
        "জি ভাই, আবেদন করতে পারবেন 😊\n"
        "✅ মাদ্রাসার ছাত্র — চলবে | ✅ দাখিল/আলিম — চলবে\n"
        "✅ শুধু পড়তে ও লিখতে পারলেই যথেষ্ট\n"
        "📄 পাঠান: নাম, বয়স, শিক্ষা (যা আছে), ঠিকানা\n"
        "📲 WhatsApp: 01958 122322",
    ),
    (
        ["vacancy", "আসন আছে", "এখনো নিচ্ছেন", "লোক নিচ্ছেন", "আর কত জন"],
        "⚠️ সীমিত আসন — এখনো নিয়োগ চলছে\n"
        "👉 আগ্রহী হলে দেরি না করে এখনই আবেদন করুন\n"
        "📌 পাঠান: নাম, বয়স, শিক্ষা, ঠিকানা\n"
        "📲 WhatsApp: 01958 122322\n"
        "✔ আগে এলে আগে সুযোগ",
    ),
    (
        ["টাকা লাগবে", "ভর্তি ফি", "জামানত", "joining fee", "deposit", "ট্রেনিং ফি"],
        "❌ কোনো ভর্তি ফি নেই | ❌ কোনো জামানত নেই\n"
        "❌ কোনো ট্রেনিং ফি নেই | ❌ কোনো ইউনিফর্ম ফি নেই\n\n"
        "✅ আল-আকসা সিকিউরিটি সার্ভিস কখনো টাকা চায় না\n"
        "যদি কেউ আমাদের নামে টাকা চায় — সেটি প্রতারণা।\n"
        "📲 01958 122322 এ জানান।",
    ),
    (
        ["বেতন কত", "salary", "মাসে কত", "কত পাব", "বেতন কাঠামো"],
        "💰 বেতন কাঠামো:\n"
        "🔹 প্রশিক্ষণকাল (৪৫ দিন): ১০,০০০–১৫,০০০ টাকা\n"
        "🔹 পরে: ১২,০০০–১৮,০০০ টাকা\n"
        "✔ দক্ষতার উপর বেতন বাড়ে\n"
        "✔ ভবিষ্যতে পদোন্নতির সুযোগ",
    ),
    (
        ["বেতন মেরে", "বেতন পাই না", "পাওনা দেয়নি"],
        "আমরা আপনার অভিযোগ গুরুত্বের সাথে নিচ্ছি।\n"
        "📲 WhatsApp: 01958 122322\n"
        "📍 ভিক্টোরিয়া গেইট, একে খান মোড়, পাহাড়তলী, চট্টগ্রাম\n"
        "✅ সব বৈধ পাওনা মিটিয়ে দিতে আমরা প্রতিশ্রুতিবদ্ধ",
    ),
    (
        ["সালাম", "আস্সালামু", "hello", "hi", "হ্যালো", "menu", "মেনু", "start"],
        "ওয়ালাইকুম আস্সালাম 🙏\n\n"
        "আমি ফজলে — আল-আকসা সিকিউরিটি সার্ভিসের ডিজিটাল সহকারী।\n\n"
        "কীভাবে সাহায্য করতে পারি?\n"
        "1️⃣ চাকরি সম্পর্কে জানতে\n"
        "2️⃣ অফিসের ঠিকানা\n"
        "3️⃣ বেতন সম্পর্কে\n"
        "4️⃣ ডিউটি/পেমেন্ট\n\n"
        "যেকোনো প্রশ্ন করুন 👇",
    ),
]


async def get_reply(text: str, intent: Optional[str] = None) -> Optional[str]:
    """
    Return a knowledge-base reply for the given message text.
    Tries DB first, falls back to hardcoded constants.
    Returns None if nothing matches — caller should use LLM.
    """
    text_lower = text.lower().strip()

    # 1. Try DB
    try:
        rows = await fetch_all(
            "SELECT key, trigger_keywords, reply_text FROM fazle_knowledge_base WHERE is_active = true",
        )
        for row in rows:
            keywords = row.get("trigger_keywords") or []
            for kw in keywords:
                if kw.lower() in text_lower:
                    log.info(f"[KB] DB match: key={row['key']} kw={kw!r}")
                    return row["reply_text"]
    except Exception as e:
        log.debug(f"[KB] DB lookup failed (table may not exist yet): {e}")

    # 2. Fallback to hardcoded
    for keywords, reply in _FALLBACK:
        for kw in keywords:
            if kw.lower() in text_lower:
                log.info(f"[KB] fallback match: kw={kw!r}")
                return reply

    return None


async def get_recruitment_reply(text: str) -> Optional[str]:
    """Shortcut: match only recruitment-category KB entries."""
    text_lower = text.lower().strip()
    try:
        rows = await fetch_all(
            "SELECT trigger_keywords, reply_text FROM fazle_knowledge_base "
            "WHERE is_active = true AND category = 'recruitment'",
        )
        for row in rows:
            for kw in (row.get("trigger_keywords") or []):
                if kw.lower() in text_lower:
                    return row["reply_text"]
    except Exception:
        pass
    # Fallback subset
    for keywords, reply in _FALLBACK:
        for kw in keywords:
            if kw.lower() in text_lower:
                return reply
    return None
