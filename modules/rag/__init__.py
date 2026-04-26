"""
Fazle Core — Batch 21
Lightweight Retrieval-Augmented Generation (RAG) layer.

Design constraints:
  • No external embedding/LLM service (offline-first VPS deployment)
  • Bilingual corpus (Bangla + English) — BM25 with Unicode-aware tokenizer
  • Small corpus (<10 MB) → in-process index, rebuild in <1s
  • Deterministic, no network calls during query

Sources indexed:
  1. Plain-text files under fazle-core/resources/*.txt
  2. Active rows of fazle_knowledge_base (key, reply_text)

Public API (all async, safe to call from FastAPI handlers):
  • await build_index()              → (re)build the in-memory index
  • await ensure_index()             → build if not yet built
  • await search(q, k=5, min_score=0.0) → list[dict]
  • await stats()                    → diagnostics dict
  • await answer(q, k=3, min_score=1.0) → {answer, citations} or None
"""
from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import time
from collections import Counter
from typing import Any, Optional

from app.database import fetch_all

log = logging.getLogger("fazle.rag")

# ── Configuration ──────────────────────────────────────────────────────────────
RESOURCES_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "resources",
)
CHUNK_SIZE = int(os.getenv("RAG_CHUNK_SIZE", "320"))      # chars per chunk
CHUNK_OVERLAP = int(os.getenv("RAG_CHUNK_OVERLAP", "60"))  # chars overlap
MIN_TOKEN_LEN = 2

# BM25 params
_K1 = 1.5
_B = 0.75


# ── Tokenizer (Unicode-friendly: works for Bangla + Latin) ─────────────────────
# Keep letters/digits, drop everything else. Bangla codepoints 0x0980-0x09FF.
_TOKEN_RE = re.compile(r"[A-Za-z0-9\u0980-\u09FF]+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    if not text:
        return []
    toks = _TOKEN_RE.findall(text.lower())
    return [t for t in toks if len(t) >= MIN_TOKEN_LEN]


# ── Chunker ────────────────────────────────────────────────────────────────────
def _chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= size:
        return [text]
    chunks = []
    step = max(1, size - overlap)
    for i in range(0, len(text), step):
        piece = text[i : i + size].strip()
        if piece:
            chunks.append(piece)
        if i + size >= len(text):
            break
    return chunks


# ── Index state ────────────────────────────────────────────────────────────────
class _Index:
    def __init__(self):
        self.docs: list[dict[str, Any]] = []   # [{source, title, text, tokens}]
        self.df: Counter = Counter()           # token -> # docs containing it
        self.avgdl: float = 0.0
        self.built_at: Optional[float] = None
        self.build_ms: Optional[int] = None
        self.lock = asyncio.Lock()

    def reset(self):
        self.docs = []
        self.df = Counter()
        self.avgdl = 0.0


_IDX = _Index()


# ── Source loaders ─────────────────────────────────────────────────────────────
def _load_resource_files() -> list[tuple[str, str, str]]:
    """Return list of (source_id, title, text) for each resource file."""
    out: list[tuple[str, str, str]] = []
    if not os.path.isdir(RESOURCES_DIR):
        log.warning(f"[rag] resources dir missing: {RESOURCES_DIR}")
        return out
    for name in sorted(os.listdir(RESOURCES_DIR)):
        if not name.lower().endswith(".txt"):
            continue
        path = os.path.join(RESOURCES_DIR, name)
        try:
            with open(path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
        except Exception as e:
            log.warning(f"[rag] failed to read {name}: {e}")
            continue
        if not text.strip():
            continue
        title = name.rsplit(".", 1)[0]
        out.append((f"file:{name}", title, text))
    return out


async def _load_kb_rows() -> list[tuple[str, str, str]]:
    """Return list of (source_id, title, text) for KB rows."""
    out: list[tuple[str, str, str]] = []
    try:
        rows = await fetch_all(
            "SELECT key, COALESCE(category,'') AS category, reply_text "
            "FROM fazle_knowledge_base WHERE is_active = true",
        )
    except Exception as e:
        log.debug(f"[rag] KB table unavailable: {e}")
        return out
    for r in rows:
        key = r.get("key") or "kb"
        title = f"{key} ({r.get('category') or 'kb'})"
        text = r.get("reply_text") or ""
        if text.strip():
            out.append((f"kb:{key}", title, text))
    return out


# ── Build ──────────────────────────────────────────────────────────────────────
async def build_index() -> dict[str, Any]:
    """(Re)build the in-memory RAG index. Safe under concurrency."""
    async with _IDX.lock:
        t0 = time.monotonic()
        _IDX.reset()

        sources: list[tuple[str, str, str]] = []
        sources.extend(_load_resource_files())
        sources.extend(await _load_kb_rows())

        for source_id, title, text in sources:
            for idx, chunk in enumerate(_chunk_text(text)):
                tokens = _tokenize(chunk)
                if not tokens:
                    continue
                _IDX.docs.append({
                    "source": source_id,
                    "title": title,
                    "chunk_idx": idx,
                    "text": chunk,
                    "tokens": tokens,
                    "len": len(tokens),
                })

        # document frequencies
        for d in _IDX.docs:
            for t in set(d["tokens"]):
                _IDX.df[t] += 1
        _IDX.avgdl = (
            sum(d["len"] for d in _IDX.docs) / len(_IDX.docs) if _IDX.docs else 0.0
        )
        _IDX.built_at = time.time()
        _IDX.build_ms = int((time.monotonic() - t0) * 1000)
        log.info(
            f"[rag] index built docs={len(_IDX.docs)} vocab={len(_IDX.df)} "
            f"avgdl={_IDX.avgdl:.1f} in {_IDX.build_ms}ms"
        )
        return await _stats_locked()


async def ensure_index() -> None:
    if _IDX.built_at is None:
        await build_index()


# ── BM25 scoring ───────────────────────────────────────────────────────────────
def _bm25_score(q_tokens: list[str], doc: dict[str, Any], n_docs: int) -> float:
    if not q_tokens or not doc["tokens"]:
        return 0.0
    tf = Counter(doc["tokens"])
    score = 0.0
    dl = doc["len"]
    for q in q_tokens:
        df = _IDX.df.get(q, 0)
        if df == 0:
            continue
        idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
        f = tf.get(q, 0)
        if f == 0:
            continue
        denom = f + _K1 * (1 - _B + _B * dl / (_IDX.avgdl or 1.0))
        score += idf * (f * (_K1 + 1)) / denom
    return score


# ── Search ─────────────────────────────────────────────────────────────────────
async def search(q: str, k: int = 5, min_score: float = 0.0) -> list[dict[str, Any]]:
    await ensure_index()
    q_tokens = _tokenize(q)
    if not q_tokens or not _IDX.docs:
        return []
    n_docs = len(_IDX.docs)
    scored = []
    for d in _IDX.docs:
        s = _bm25_score(q_tokens, d, n_docs)
        if s > min_score:
            scored.append((s, d))
    scored.sort(key=lambda x: x[0], reverse=True)
    out = []
    for s, d in scored[:k]:
        out.append({
            "score": round(s, 4),
            "source": d["source"],
            "title": d["title"],
            "chunk_idx": d["chunk_idx"],
            "text": d["text"],
        })
    return out


# ── Templated answer (extractive, no LLM) ─────────────────────────────────────
async def answer(q: str, k: int = 3, min_score: float = 1.0) -> Optional[dict[str, Any]]:
    """Build an extractive answer from top-k chunks. Returns None if no good hit."""
    hits = await search(q, k=k, min_score=min_score)
    if not hits:
        return None
    parts = []
    citations = []
    for i, h in enumerate(hits, 1):
        parts.append(f"[{i}] {h['text']}")
        citations.append({"n": i, "source": h["source"], "title": h["title"], "score": h["score"]})
    return {
        "answer": "\n\n".join(parts),
        "citations": citations,
        "top_score": hits[0]["score"],
    }


# ── Stats ──────────────────────────────────────────────────────────────────────
async def _stats_locked() -> dict[str, Any]:
    by_source: Counter = Counter()
    for d in _IDX.docs:
        by_source[d["source"].split(":", 1)[0]] += 1
    return {
        "built_at": _IDX.built_at,
        "build_ms": _IDX.build_ms,
        "docs": len(_IDX.docs),
        "vocab": len(_IDX.df),
        "avgdl": round(_IDX.avgdl, 2),
        "by_source_kind": dict(by_source),
        "chunk_size": CHUNK_SIZE,
        "chunk_overlap": CHUNK_OVERLAP,
    }


async def stats() -> dict[str, Any]:
    await ensure_index()
    async with _IDX.lock:
        return await _stats_locked()
