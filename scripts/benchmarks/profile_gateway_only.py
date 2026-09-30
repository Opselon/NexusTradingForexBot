"""cProfile of NewsAdmissionGateway.evaluate alone (no DB in the loop).

Wave-6 lane-4 profiling: isolates the admission-gateway CPU work from the
persistence round-trips, so the pure-Python hot loop is visible.
"""

from __future__ import annotations

import cProfile
import io
import os
import pstats
import sys
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
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    workload = generate_workload(n)
    gw = NewsAdmissionGateway(_FakeDb())
    source_config = {"source_id": "reuters_test"}

    canon = [canonicalize_item(it, it["source_id"], it["source_name"]) for it in workload]

    prof = cProfile.Profile()
    prof.enable()
    for c in canon:
        gw.evaluate(c, source_config)
    prof.disable()

    stream = io.StringIO()
    stats = pstats.Stats(prof, stream=stream)
    stats.sort_stats("cumulative")
    stats.print_stats(25)
    print(stream.getvalue())

    stream2 = io.StringIO()
    stats2 = pstats.Stats(prof, stream=stream2)
    stats2.sort_stats("tottime")
    stats2.print_stats(15)
    print(stream2.getvalue())


if __name__ == "__main__":
    main()
