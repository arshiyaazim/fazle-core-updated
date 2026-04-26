"""
Batch 21 — RAG smoke test (offline, no service required).

Builds the index in-process and exercises tokenizer, BM25 search, and
templated answer assembly against the resources/*.txt corpus.

Run:  venv/bin/python scripts/test_batch21_rag.py
"""
import asyncio
import os
import sys

# Allow `python scripts/...` from project root
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from modules import rag


async def main():
    print("[1] tokenizer sanity")
    toks = rag._tokenize("Salary বেতন কত — 12,000 BDT?")
    assert "salary" in toks and "বেতন" in toks and "12" in toks, toks
    print("    ok:", toks)

    print("[2] build_index from resources/*.txt only (KB DB likely unavailable in test ctx)")
    s = await rag.build_index()
    print("    stats:", s)
    assert s["docs"] > 0, "no docs indexed — check resources dir"
    assert s["vocab"] > 50

    print("[3] search Bangla query")
    hits = await rag.search("বেতন কত", k=5)
    print(f"    {len(hits)} hits, top score={hits[0]['score'] if hits else None}")
    assert hits, "no Bangla hits"
    assert hits[0]["score"] > 0

    print("[4] search English query")
    hits = await rag.search("security guard duty", k=5)
    print(f"    {len(hits)} hits, top={hits[0]['title'] if hits else None}")
    assert hits

    print("[5] answer() returns extractive answer + citations")
    ans = await rag.answer("escort কাজের ডিউটি কত ঘণ্টা", k=3, min_score=0.5)
    assert ans is not None, "answer should not be None for known topic"
    assert "citations" in ans and len(ans["citations"]) > 0
    print(f"    top_score={ans['top_score']} citations={len(ans['citations'])}")

    print("[6] empty query returns []")
    assert await rag.search("") == []
    assert await rag.search("   ") == []

    print("[7] stats consistency")
    s2 = await rag.stats()
    assert s2["docs"] == s["docs"]
    assert s2["vocab"] == s["vocab"]

    print("\n✅ Batch 21 RAG — ALL TESTS PASS")


if __name__ == "__main__":
    asyncio.run(main())
