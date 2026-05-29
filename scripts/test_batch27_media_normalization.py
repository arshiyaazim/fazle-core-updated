"""
Batch 27 — Meta media normalization tests (offline).
Run: /home/azim/.venv/bin/python scripts/test_batch27_media_normalization.py
"""
import asyncio
import os
import sys
from unittest.mock import AsyncMock, patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from modules import media_normalization as mn


async def test_audio_transcript_replaces_placeholder():
    with patch.object(mn, "_download_meta_media", new=AsyncMock(return_value="/tmp/fake.ogg")), \
         patch.object(mn, "process_voice", new=AsyncMock(return_value={"transcript": "আমি বেতন চাই", "language_hint": "bn", "confident": True})):
        with patch("os.path.exists", return_value=False):
            out = await mn.normalize_meta_media_message({"type": "audio", "audio": {"id": "m1", "mime_type": "audio/ogg"}})
    assert out["text"].startswith("[audio message]")
    assert "আমি বেতন চাই" in out["text"]
    assert out["meta"]["normalized"] is True


async def test_image_ocr_preserves_marker_and_adds_text():
    with patch.object(mn, "_download_meta_media", new=AsyncMock(return_value="/tmp/fake.jpg")), \
         patch.object(mn, "process_image", new=AsyncMock(return_value={"raw_text": "escort slip text", "slip_type": "escort_slip", "is_duplicate": False})):
        with patch("os.path.exists", return_value=False):
            out = await mn.normalize_meta_media_message({"type": "image", "image": {"id": "m2", "mime_type": "image/jpeg"}})
    assert out["text"].startswith("[image message]")
    assert "escort slip text" in out["text"]
    assert out["meta"]["slip_type"] == "escort_slip"


async def test_document_non_image_falls_back_to_placeholder_with_filename():
    out = await mn.normalize_meta_media_message({"type": "document", "document": {"id": "m3", "mime_type": "application/pdf", "filename": "slip.pdf"}})
    assert out["text"] == "[document message] slip.pdf"
    assert out["meta"]["normalized"] is False


async def test_missing_media_id_stays_placeholder():
    out = await mn.normalize_meta_media_message({"type": "image", "image": {}})
    assert out["text"] == "[image message]"
    assert out["meta"]["normalized"] is False


async def main():
    print("[1] audio transcript normalizes media text")
    await test_audio_transcript_replaces_placeholder()
    print("    ok")

    print("[2] image OCR preserves marker and adds extracted text")
    await test_image_ocr_preserves_marker_and_adds_text()
    print("    ok")

    print("[3] non-image document remains placeholder with filename")
    await test_document_non_image_falls_back_to_placeholder_with_filename()
    print("    ok")

    print("[4] missing media id stays placeholder")
    await test_missing_media_id_stays_placeholder()
    print("    ok")

    print("\n✅ Batch 27 Media Normalization — CORE TESTS PASS")


if __name__ == "__main__":
    asyncio.run(main())