"""
Batch 22 — Observability smoke test (offline).
Run:  venv/bin/python scripts/test_batch22_observability.py
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from modules import observability as obs


def main():
    obs._reset_for_tests()

    print("[1] counters with labels")
    obs.inc("kb_hits_total")
    obs.inc("kb_hits_total", labels={"source": "db"})
    obs.inc("kb_hits_total", labels={"source": "db"}, value=2)
    obs.inc("kb_hits_total", labels={"source": "rag"})
    snap = obs.snapshot()
    series = snap["counters"]["kb_hits_total"]
    by = {tuple(sorted(e["labels"].items())): e["value"] for e in series}
    assert by[()] == 1
    assert by[(("source", "db"),)] == 3
    assert by[(("source", "rag"),)] == 1
    print("    ok")

    print("[2] gauge replaces (not adds)")
    obs.gauge("queue_depth", 5)
    obs.gauge("queue_depth", 9)
    snap = obs.snapshot()
    assert snap["gauges"]["queue_depth"][0]["value"] == 9
    print("    ok")

    print("[3] histogram + bucket cardinality")
    for v in [1, 7, 11, 80, 240, 900, 2400, 9000]:
        obs.observe("http_ms", v, labels={"path": "/x"})
    snap = obs.snapshot()
    h = snap["histograms"]["http_ms"][0]
    assert h["count"] == 8
    assert h["sum_ms"] == 12639  # 1+7+11+80+240+900+2400+9000
    # per-bucket (non-cumulative) counts:
    # 1→le=5, 7→le=10, 11→le=25, 80→le=100, 240→le=250, 900→le=1000, 2400→le=2500, 9000→le=10000
    b = h["buckets_ms"]
    assert b["5"] == 1 and b["10"] == 1 and b["25"] == 1 and b["100"] == 1
    assert b["250"] == 1 and b["1000"] == 1 and b["2500"] == 1 and b["10000"] == 1
    print("    ok avg=", h["avg_ms"])

    print("[4] prometheus exposition has TYPE lines + cumulative buckets")
    text = obs.render_prometheus()
    assert "# TYPE kb_hits_total counter" in text
    assert "# TYPE queue_depth gauge" in text
    assert "# TYPE http_ms histogram" in text
    # cumulative at +Inf must equal total count
    assert 'http_ms_bucket{path="/x",le="+Inf"} 8' in text
    # cumulative at le=10000 must also be 8 (all observations covered)
    assert 'http_ms_bucket{path="/x",le="10000"} 8' in text
    assert "http_ms_count" in text
    assert "fazle_uptime_seconds" in text
    print("    ok lines=", len(text.splitlines()))

    print("[5] label escaping")
    obs.inc("evil", labels={"q": 'a"b\nc'})
    text = obs.render_prometheus()
    assert 'q="a\\"b\\nc"' in text
    print("    ok")

    print("\n✅ Batch 22 Observability — ALL TESTS PASS")


if __name__ == "__main__":
    main()
