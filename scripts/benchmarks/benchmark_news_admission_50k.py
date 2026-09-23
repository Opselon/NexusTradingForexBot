"""Performance benchmark for the News Admission Gateway (deliverable G.3).

Executes a realistic synthetic workload of 50,000 incoming news items
resembling a high-volume multi-feed burst, comparing:

    * BASELINE: legacy path (no admission gate: every item persisted),
    * GATEWAY:  news-admission-gate (progressive cost: cheap gates first).

Measures:
    * throughput (items/sec),
    * latency: mean, p50, p90, p95, p99,
    * database writes avoided,
    * duplicate collapse count,
    * peak memory delta.

Emits structured JSON to stdout and an artifacts path.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    import psutil  # type: ignore[import-untyped]
except ImportError:
    psutil = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))

from nexus_scalp.news.admission import NewsAdmissionGateway  # noqa: E402
from nexus_scalp.news.database import NewsDatabase  # noqa: E402
from nexus_scalp.news.ingest import NewsIngestor  # noqa: E402
from nexus_scalp.news.sources import SourceFetchResult  # noqa: E402

# Per-item INFO/DEBUG logging is measurement noise at benchmark scale; keep
# the benchmark to the code paths themselves (errors still surface).
for _name in (
    "nexus_scalp.news.engine",
    "nexus_scalp.news.ingest",
    "nexus_scalp.news.admission",
    "nexus_scalp.news.db_articles",
    "nexus_scalp.news.analysis",
):
    logging.getLogger(_name).setLevel(logging.ERROR)


# ---------------------------------------------------------------------------
# Workload generator: realistic 50k distribution
# ---------------------------------------------------------------------------
# Reflects the real production news.db audit:
#   * ~10% genuinely relevant macro/gold stories (ADMIT candidate),
#   * ~25% lifestyle/consumer noise from aggregators (REJECT candidate),
#   * ~15% borderline industry/bank notes (QUARANTINE candidate),
#   * ~45% repeat polls of the same story under reminted published_at (RE-POLL DUP),
#   * ~5% malformed/too-short garbage titles (STAGE-0 REJECT candidate).
# ---------------------------------------------------------------------------


def generate_workload(n: int = 50_000) -> list[dict[str, Any]]:
    base_time = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)

    macro_templates = [
        (
            "Federal Reserve cuts benchmark interest rate by 25 basis points",
            "FOMC statement shows inflation easing toward 2% target, real yields decline and gold rallies.",
            "https://reuters.com/markets/fed-cuts-rate-{i}",
        ),
        (
            "US CPI rises 0.2% month-over-month, annual pace cools to 2.5%",
            "Labor department reports headline consumer prices in line with consensus, core sticky.",
            "https://bloomberg.com/news/us-cpi-{i}",
        ),
        (
            "Gold surges past record high as dollar softens on dovish Fed bets",
            "Spot gold advanced to new highs in London trade as real yields fell across the curve.",
            "https://reuters.com/commodities/gold-record-{i}",
        ),
        (
            "Treasury yields slide following weaker-than-expected non-farm payrolls",
            "Two-year yields down 8 bps after payrolls print 110k vs 160k expected.",
            "https://wsj.com/markets/treasury-yields-{i}",
        ),
    ]

    noise_templates = [
        (
            "Best warehouse club patio furniture deals this weekend",
            "Roundup of discounted patio chairs, umbrella sets and summer grills at local retail stores.",
            "https://aggregator.net/lifestyle/patio-{i}",
        ),
        (
            "Top 10 streaming shows to binge-watch during the holiday weekend",
            "Critics review the newest streaming television releases across platforms.",
            "https://aggregator.net/culture/shows-{i}",
        ),
        (
            "Local high school football scores and Friday night highlights",
            "Sports recap covering county division championship games.",
            "https://aggregator.net/sports/scores-{i}",
        ),
        (
            "Simple kitchen hacks to keep produce fresh for two weeks",
            "Home economics tips for storing greens and vegetables.",
            "https://aggregator.net/home/kitchen-{i}",
        ),
    ]

    borderline_templates = [
        (
            "Regional commercial bank announces new chief risk officer appointment",
            "Board confirms leadership transition effective next quarter.",
            "https://businesswire.com/news/bank-cro-{i}",
        ),
        (
            "Mid-cap industrial distributor declares quarterly cash dividend",
            "Regular dividend of 15 cents per share payable next month.",
            "https://globenewswire.com/news/dividend-{i}",
        ),
    ]

    # Pre-build pool of reusable canonical stories for the re-poll pattern (BUG-294 fix:
    # the 231 identical-title re-ingests from Bank of England match EXACT URL + Summary,
    # not per-poll URL variants).
    repoll_pool = [
        (
            "Bank Rate maintained at 3.75% - Monetary Policy Summary",
            "Monetary Policy Committee voted by a majority of 7-2 to maintain Bank Rate.",
            "https://bankofengland.co.uk/mpc-summary",
        ),
        (
            "UK financial regulators to begin overseeing Critical Third Parties - Notice",
            "HM Treasury announces regulatory framework for CTPs effective July 2026.",
            "https://www.bankofengland.co.uk/statistics/notice/green-notice-2026-01",
        ),
    ]

    workload: list[dict[str, Any]] = []
    for i in range(n):
        slot = i % 100
        if slot < 5:  # malformed / stage 0
            workload.append(
                {
                    "title": "short" if i % 2 == 0 else "   ",
                    "summary": "",
                    "url": f"https://bad.org/item-{i}",
                    "published_at": base_time - timedelta(minutes=i),
                    "source_id": "aggregator_test",
                    "source_name": "Aggregator",
                }
            )
        elif slot < 15:  # macro/gold admit
            tpl = macro_templates[i % len(macro_templates)]
            workload.append(
                {
                    "title": tpl[0],
                    "summary": tpl[1],
                    "url": tpl[2].format(i=i),
                    "published_at": base_time - timedelta(minutes=i % 1440),
                    "source_id": "reuters_test",
                    "source_name": "Reuters",
                }
            )
        elif slot < 40:  # noise reject
            tpl = noise_templates[i % len(noise_templates)]
            workload.append(
                {
                    "title": tpl[0],
                    "summary": tpl[1],
                    "url": tpl[2].format(i=i),
                    "published_at": base_time - timedelta(minutes=i % 1440),
                    "source_id": "aggregator_test",
                    "source_name": "Aggregator",
                }
            )
        elif slot < 55:  # borderline quarantine
            tpl = borderline_templates[i % len(borderline_templates)]
            workload.append(
                {
                    "title": tpl[0],
                    "summary": tpl[1],
                    "url": tpl[2].format(i=i),
                    "published_at": base_time - timedelta(minutes=i % 1440),
                    "source_id": "aggregator_test",
                    "source_name": "Aggregator",
                }
            )
        else:  # re-polls of the same 2 items (BUG-294: 231 copies with same URL + summary)
            base_story = repoll_pool[i % len(repoll_pool)]
            workload.append(
                {
                    "title": base_story[0],
                    "summary": base_story[1],
                    "url": base_story[2],  # same URL every re-poll: the production pattern
                    "published_at": base_time - timedelta(minutes=i),  # reminted per poll
                    "source_id": "boe_test",
                    "source_name": "Bank of England",
                }
            )

    return workload


def _measure_run(
    name: str,
    workload: list[dict[str, Any]],
    db_path: Path,
    use_gateway: bool,
) -> dict[str, Any]:
    if db_path.exists():
        db_path.unlink()

    db = NewsDatabase(db_path)
    db.upsert_source(
        {"source_id": "reuters_test", "name": "Reuters", "tier": "TIER_1", "enabled": True}
    )
    db.upsert_source({"source_id": "boe_test", "name": "BoE", "tier": "TIER_1", "enabled": True})
    db.upsert_source(
        {"source_id": "aggregator_test", "name": "Agg", "tier": "TIER_4", "enabled": True}
    )

    gw = NewsAdmissionGateway(db) if use_gateway else None
    ingestor = NewsIngestor(db, admission_gateway=gw)

    proc = psutil.Process(os.getpid()) if psutil is not None else None
    mem_before = proc.memory_info().rss if proc else 0

    latencies_ms: list[float] = []
    t_start = time.perf_counter()

    batch_size = 500
    total_new = 0
    total_dup = 0
    total_admitted = 0
    total_quarantined = 0
    total_rejected = 0

    for i in range(0, len(workload), batch_size):
        chunk = workload[i : i + batch_size]
        src_id = chunk[0]["source_id"]
        src_name = chunk[0]["source_name"]
        tier = "TIER_1" if "reuters" in src_id or "boe" in src_id else "TIER_4"

        t_batch_start = time.perf_counter()
        stats = ingestor.ingest_source_items(
            {"source_id": src_id, "name": src_name, "tier": tier},
            SourceFetchResult(ok=True, items=chunk, status=200),
        )
        batch_duration_ms = (time.perf_counter() - t_batch_start) * 1000.0
        per_item_ms = batch_duration_ms / max(1, len(chunk))
        latencies_ms.extend([per_item_ms] * len(chunk))

        total_new += stats.get("new", 0)
        total_dup += stats.get("duplicate", 0)
        total_admitted += stats.get("admitted", 0)
        total_quarantined += stats.get("quarantined", 0)
        total_rejected += stats.get("rejected", 0)

    elapsed_s = time.perf_counter() - t_start
    mem_after = proc.memory_info().rss if proc else 0
    db_size_bytes = db_path.stat().st_size if db_path.exists() else 0

    latencies_sorted = sorted(latencies_ms)
    n = len(latencies_sorted)

    def _pct(p: float) -> float:
        if not latencies_sorted:
            return 0.0
        idx = min(n - 1, max(0, int(n * p)))
        return round(latencies_sorted[idx], 3)

    return {
        "name": name,
        "items_processed": n,
        "elapsed_seconds": round(elapsed_s, 2),
        "throughput_items_per_sec": round(n / max(0.001, elapsed_s), 1),
        "latency_ms": {
            "mean": round(sum(latencies_sorted) / max(1, n), 3),
            "p50": _pct(0.50),
            "p90": _pct(0.90),
            "p95": _pct(0.95),
            "p99": _pct(0.99),
        },
        "stats": {
            "new_persisted": total_new,
            "duplicate_collapsed": total_dup,
            "admitted": total_admitted,
            "quarantined": total_quarantined,
            "rejected_before_db": total_rejected,
            "db_writes_avoided": n - total_new,
            "write_avoidance_pct": round(100.0 * (n - total_new) / max(1, n), 2),
        },
        "db_size_mb": round(db_size_bytes / (1024 * 1024), 2),
        "rss_memory_delta_mb": round((mem_after - mem_before) / (1024 * 1024), 2),
        "gateway_metrics": gw.metrics.snapshot() if gw else None,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="News Admission Gateway 50k benchmark")
    parser.add_argument("--count", type=int, default=50_000, help="Workload item count")
    parser.add_argument("--output", type=str, default="", help="Path to write JSON results")
    args = parser.parse_args()

    workload = generate_workload(args.count)

    tmp_dir = REPO_ROOT / "artifacts" / "benchmarks"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    base_db = tmp_dir / "bench_baseline.db"
    gate_db = tmp_dir / "bench_gateway.db"

    results = {
        "benchmark": "news_admission_gateway_50k",
        "timestamp": datetime.now(UTC).isoformat(),
        "workload_size": args.count,
        "runs": {},
    }

    try:
        results["runs"]["with_gateway"] = _measure_run(
            "with_gateway", workload, gate_db, use_gateway=True
        )
        results["runs"]["baseline_no_gateway"] = _measure_run(
            "baseline_no_gateway", workload, base_db, use_gateway=False
        )

        gw_run = results["runs"]["with_gateway"]
        base_run = results["runs"]["baseline_no_gateway"]
        results["comparison"] = {
            "db_writes_avoided_count": base_run["stats"]["new_persisted"]
            - gw_run["stats"]["new_persisted"],
            "db_size_reduction_pct": round(
                100.0 * (1.0 - (gw_run["db_size_mb"] / max(0.01, base_run["db_size_mb"]))), 2
            ),
            "throughput_ratio": round(
                gw_run["throughput_items_per_sec"] / max(0.1, base_run["throughput_items_per_sec"]),
                2,
            ),
        }

        print(json.dumps(results, indent=2))

        out_path = (
            Path(args.output) if args.output else tmp_dir / "news_admission_50k_benchmark.json"
        )
        out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
        print(f"\nWritten to {out_path}", file=sys.stderr)
    finally:
        for p in (base_db, gate_db):
            if p.exists():
                try:
                    p.unlink()
                except Exception:
                    pass


if __name__ == "__main__":
    main()
