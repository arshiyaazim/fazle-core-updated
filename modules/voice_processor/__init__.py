"""
Fazle Core — Voice Message Processor (Phase 4F)

Calls local media-processor /transcribe to convert audio → text.
Then classifies intent and builds a reply.

Handles: .ogg (WhatsApp), .mp3, .wav, .m4a
"""

import logging
import os
import httpx
from typing import TypedDict, Optional

from app.config import get_settings

log = logging.getLogger("fazle.voice")

# Minimum confidence (word count) to trust transcript
MIN_WORD_COUNT = 2


class VoiceResult(TypedDict):
    transcript: str
    word_count: int
    confident: bool         # False → ask user to send text instead
    language_hint: str      # 'bn' | 'en' | 'mixed' | 'unknown'
    intent: str
    reply: str


async def process_voice(file_path: str) -> VoiceResult:
    """
    Full pipeline: transcribe → language detect → intent → reply.
    """
    settings = get_settings()

    transcript = await _call_transcribe(settings.media_processor_url, file_path)
    words = transcript.split() if transcript else []
    confident = len(words) >= MIN_WORD_COUNT
    lang = _detect_language(transcript)

    if not confident:
        return VoiceResult(
            transcript=transcript,
            word_count=len(words),
            confident=False,
            language_hint=lang,
            intent="unknown",
            reply="ভয়েস বার্তাটি ভালোভাবে বোঝা যায়নি। অনুগ্রহ করে টেক্সট আকারে পাঠান।",
        )

    # Lazy import to avoid circular
    from modules.intent import classify
    from app import ollama as ai

    intent = classify(transcript)
    if intent == "unknown":
        intent = await ai.classify_intent_llm(transcript)

    log.info(f"[voice] transcript={transcript[:80]!r} lang={lang} intent={intent}")

    return VoiceResult(
        transcript=transcript,
        word_count=len(words),
        confident=True,
        language_hint=lang,
        intent=intent,
        reply="",   # caller is responsible for generating the final reply
    )


async def _call_transcribe(base_url: str, file_path: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            r = await client.post(
                f"{base_url.rstrip('/')}/transcribe",
                json={"file_path": file_path},
            )
            if r.status_code == 200:
                return r.json().get("text", "").strip()
            log.warning(f"[voice] transcribe returned {r.status_code}")
    except Exception as e:
        log.error(f"[voice] transcribe error: {e}")
    return ""


def _detect_language(text: str) -> str:
    """Heuristic: count Bengali unicode characters."""
    if not text:
        return "unknown"
    bn_chars = sum(1 for c in text if "\u0980" <= c <= "\u09FF")
    latin_chars = sum(1 for c in text if c.isalpha() and ord(c) < 128)
    total = bn_chars + latin_chars
    if total == 0:
        return "unknown"
    bn_ratio = bn_chars / total
    if bn_ratio > 0.6:
        return "bn"
    if bn_ratio < 0.2:
        return "en"
    return "mixed"
