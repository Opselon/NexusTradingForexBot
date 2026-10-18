"""cProfile of the NewsAdmissionGateway ingest path (wave-6 lane-4 profiling).

Drives the SAME workload/batch shape as benchmark_news_admission_50k.py
through NewsIngestor.ingest_source_items with a real gateway attached, then
dumps the top cumulative-time functions. Pure measurement — no behavior change.
"""

from __future__ import annotations

import cProfile
import io
import os
import pstats
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

# The benchmark must follow the ACTIVE provider config; this box exports
# NSE_DATABASE__* pointing at a PG that is not running, which turns every
# run into a pool-timeout wait instead of a measurement.
for _k in [k for k in os.environ if k.startswith("NSE_DATABASE__")]:
    del os.environ[_k]

from nexus_scalp.news.admission import NewsAdmissionGateway  # noqa: E402
from nexus_scalp.news.database import NewsDatabase  # noqa: E402
from nexus_scalp.news.ingest import NewsIngestor  # noqa: E402
from nexus_scalp.news.sources import SourceFetchResult  # noqa: E402

sys.path.insert(0, str(REPO_ROOT / "scripts" / "benchmarks"))
from benchmark_news_admission_50k import generate_workload  # noqa: E402


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    workload = generate_workload(n)

    tmp = Path(tempfile.mkdtemp(prefix="ns_profile_"))
    db_path = tmp / "profile.db"
    db = NewsDatabase(db_path)
    db.upsert_source(
        {"source_id": "reuters_test", "name": "Reuters", "tier": "TIER_1", "enabled": True}
    )
    db.upsert_source({"source_id": "boe_test", "name": "BoE", "tier": "TIER_1", "enabled": True})
    db.upsert_source(
        {"source_id": "aggregator_test", "name": "Agg", "tier": "TIER_4", "enabled": True}
    )

    gw = NewsAdmissionGateway(db)
    ingestor = NewsIngestor(db, admission_gateway=gw)

    # Warm-up (import caches, tier cache) so the profile measures steady state.
    warm = generate_workload(500)
    ingestor.ingest_source_items(
        {"source_id": "aggregator_test", "name": "Agg", "tier": "TIER_4"},
        SourceFetchResult(ok=True, items=warm, status=200),
    )

    prof = cProfile.Profile()
    prof.enable()
    batch_size = 500
    for i in range(0, len(workload), batch_size):
        chunk = workload[i : i + batch_size]
        src_id = chunk[0]["source_id"]
        tier = "TIER_1" if "reuters" in src_id or "boe" in src_id else "TIER_4"
        ingestor.ingest_source_items(
            {"source_id": src_id, "name": chunk[0]["source_name"], "tier": tier},
            SourceFetchResult(ok=True, items=chunk, status=200),
        )
    prof.disable()

    stream = io.StringIO()
    stats = pstats.Stats(prof, stream=stream)
    stats.sort_stats("cumulative")
    stats.print_stats(28)
    print(stream.getvalue())

    stream2 = io.StringIO()
    stats2 = pstats.Stats(prof, stream=stream2)
    stats2.sort_stats("tottime")
    stats2.print_stats(20)
    print(stream2.getvalue())

    db_path.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
