"""Pure-Python segment micro-benchmark: admission-path CPU work, no DB.

Isolates the parts of the ingest hot loop that are pure Python
(canonicalize_item + NewsAdmissionGateway.evaluate), so the DB round-trip
cost does not hide them. Wave-6 lane-4 measurement.
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

from nexus_scalp.news.admission import NewsAdmissionGateway  # noqa: E402
from nexus_scalp.news.ingest.deduplicator import canonicalize_item  # noqa: E402


class _FakeDb:
    def get_source(self, source_id):
        return {"tier": "TIER_1"}


def main() -> None:
    n = 20_000
    workload = generate_workload(n)
    gw = NewsAdmissionGateway(_FakeDb())

    # canonicalize_item segment
    t = time.perf_counter()
    canon = [canonicalize_item(it, it["source_id"], it["source_name"]) for it in workload]
    t_canon = time.perf_counter() - t

    # evaluate segment
    t = time.perf_counter()
    verdicts = [gw.evaluate(c, {"source_id": c["source_id"]}) for c in canon]
    t_eval = time.perf_counter() - t

    decisions = {}
    for v in verdicts:
        decisions[v.decision] = decisions.get(v.decision, 0) + 1

    print(f"items                       : {n}")
    print(f"canonicalize_item total     : {t_canon:.3f}s  ({n / t_canon:,.0f}/s)")
    print(f"gateway.evaluate total      : {t_eval:.3f}s  ({n / t_eval:,.0f}/s)")
    print(f"sum                         : {t_canon + t_eval:.3f}s")
    print(f"decisions                   : {decisions}")
    print(f"verdict sample reason_codes : {verdicts[0].reason_codes}")


if __name__ == "__main__":
    main()
