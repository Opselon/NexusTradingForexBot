"""Segment cost breakdown for the admission/ingest hot loop (wave-6 lane-4).

Splits the per-item cost of the in-scope Python hot loop into:
  * canonicalize_item (deduplicator.py  - IN SCOPE),
  * NewsAdmissionGateway.evaluate (admission.py - IN SCOPE),
      of which LocalNewsAnalyzer regex work (analysis/local.py),
  * calendar classify_event_title (calendar/classify.py).

No DB in the loop: this measures CPU only, so a change to pure-Python work
shows up here instead of being buried under SQLite round-trips.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

for _k in [k for k in os.environ if k.startswith("NSE_DATABASE__")]:
    del os.environ[_k]

sys.path.insert(0, str(REPO_ROOT / "scripts" / "benchmarks"))
from benchmark_news_admission_50k import generate_workload  # noqa: E402

from nexus_scalp.calendar.classify import classify_event_title  # noqa: E402
from nexus_scalp.news.admission import NewsAdmissionGateway  # noqa: E402
from nexus_scalp.news.ingest.deduplicator import canonicalize_item  # noqa: E402


class _FakeDb:
    def get_source(self, source_id):
        return {"tier": "TIER_1"}


def _time(fn) -> float:
    t = time.perf_counter()
    fn()
    return time.perf_counter() - t


def main() -> None:
    n = 10_000
    workload = generate_workload(n)
    gw = NewsAdmissionGateway(_FakeDb())
    src = {"source_id": "reuters_test"}

    t_canon = _time(
        lambda: [canonicalize_item(it, it["source_id"], it["source_name"]) for it in workload]
    )
    canon = [canonicalize_item(it, it["source_id"], it["source_name"]) for it in workload]

    t_gw = _time(lambda: [gw.evaluate(c, src) for c in canon])

    # calendar classify alone (the ingest path calls it once more per admitted
    # item after the gateway already computed the same match)
    titles = [c["title"] for c in canon]
    t_classify = _time(lambda: [classify_event_title(t) for t in titles])

    print(f"items                     : {n}")
    print(
        f"canonicalize_item         : {t_canon:6.3f}s  ({100 * t_canon / (t_canon + t_gw + t_classify):5.1f}% of python hot loop)"
    )
    print(
        f"gateway.evaluate          : {t_gw:6.3f}s  ({100 * t_gw / (t_canon + t_gw + t_classify):5.1f}%)"
    )
    print(
        f"classify_event_title x1    : {t_classify:6.3f}s  ({100 * t_classify / (t_canon + t_gw + t_classify):5.1f}%)"
    )
    print(
        f"python hot loop total     : {t_canon + t_gw + t_classify:6.3f}s  ({n / (t_canon + t_gw + t_classify):,.0f} items/s)"
    )


if __name__ == "__main__":
    main()
