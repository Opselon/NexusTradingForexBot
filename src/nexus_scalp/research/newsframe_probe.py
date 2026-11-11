"""Phase 0 item 3: NEWS 50..59 end-to-end trace + news frame quality from the live DB."""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

import polars as pl

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))
OUT = REPO / "artifacts" / "research" / "phase0_20260921"

from nexus_scalp.model_generation.news_bridge import (  # noqa: E402
    build_news_frame_from_db,
    news_benchmark_readiness,
    news_context_at,
    news_quality_diagnostics,
)
from nexus_scalp.news.config import NewsConfig  # noqa: E402
from nexus_scalp.news.database import NewsDatabase  # noqa: E402


def main() -> None:
    db = NewsDatabase(NewsConfig().db_path)
    nf = build_news_frame_from_db(db)
    info: dict = {}
    if nf is None or nf.is_empty():
        info["news_frame"] = {"rows": 0}
    else:
        info["news_frame"] = {
            "rows": int(nf.height),
            "columns": nf.columns,
            "ts_min": str(nf["published_at"].min()),
            "ts_max": str(nf["published_at"].max()),
        }
        # coverage vs the dataset window 2026-05-01 18:09 .. 2026-08-17 19:24 UTC
        ts = nf["published_at"].cast(pl.Datetime("us")).dt.epoch("us")
        ds_start = int(datetime(2026, 5, 1, 18, 9, tzinfo=UTC).timestamp() * 1e6)
        ds_end = int(datetime(2026, 8, 17, 19, 24, tzinfo=UTC).timestamp() * 1e6)
        info["coverage_vs_dataset_window"] = {
            "dataset_window_utc": ["2026-05-01 18:09", "2026-08-17 19:24"],
            "news_rows_inside_dataset_window": int((ts <= ds_end).sum()),
            "news_rows_after_dataset_end": int((ts > ds_end).sum()),
            "news_rows_before_dataset_start": int((ts < ds_start).sum()),
        }
        # sample a few timestamps inside the dataset tail + after news starts
        for probe_ts in [
            datetime(2026, 8, 17, 19, 0, tzinfo=UTC),
            datetime(2026, 8, 21, 9, 0, tzinfo=UTC),
            datetime(2026, 9, 6, 12, 0, tzinfo=UTC),
        ]:
            ctx = news_context_at(nf, probe_ts)
            from nexus_scalp.features.features70 import news_10d_from_context

            info.setdefault("news_context_samples", []).append(
                {
                    "timestamp": probe_ts.isoformat(),
                    "context_12": {k: round(float(v), 6) for k, v in ctx.items()},
                    "news_10d_at_index_50_59": [
                        round(float(v), 6) for v in news_10d_from_context(ctx)
                    ],
                }
            )
        diag = news_quality_diagnostics(nf)
        info["quality_diagnostics"] = {k: v for k, v in diag.items() if k != "per_field"}
        info["per_field"] = diag["per_field"]
        info["readiness"] = news_benchmark_readiness(nf)

    (OUT / "news_frame_report.json").write_text(
        json.dumps(info, indent=2, default=str), encoding="utf-8"
    )
    print(json.dumps(info, indent=2, default=str)[:6000])


if __name__ == "__main__":
    main()
