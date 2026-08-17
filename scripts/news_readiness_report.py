#!/usr/bin/env python3
"""Generate the news benchmark readiness gate + feature-quality report.

Writes:
    artifacts/model_generation/news_benchmark_readiness.json
    artifacts/model_generation/news_benchmark_readiness.md

Reads the News subsystem database (artifacts/news.db) if it exists; when the
DB is absent (the current repository state), the report honestly records
NOT READY with zero evidence — the benchmark gate stays RED and no
A/B/C/D may run until real news exists.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from nexus_scalp.model_generation.news_bridge import (
    build_news_frame_from_db,
    news_benchmark_readiness,
    news_quality_diagnostics,
)

_REPO_ROOT = Path.cwd()
_DB = _REPO_ROOT / "artifacts" / "news.db"
_OUT_JSON = _REPO_ROOT / "artifacts" / "model_generation" / "news_benchmark_readiness.json"
_OUT_MD = _REPO_ROOT / "artifacts" / "model_generation" / "news_benchmark_readiness.md"


def _main() -> int:
    _OUT_JSON.parent.mkdir(parents=True, exist_ok=True)

    frame = None
    db_exists = _DB.exists()
    db_articles = 0
    db_sources = 0
    if db_exists:
        from nexus_scalp.news.database import NewsDatabase

        db = NewsDatabase(_DB)
        frame = build_news_frame_from_db(db)
        summary = db.summary()
        db_articles = int(summary.get("articles", 0))
        db_sources = int(summary.get("sources", 0))

    diag = news_quality_diagnostics(frame)
    gate = news_benchmark_readiness(frame)

    report = {
        "generated_at": datetime.now(UTC).isoformat(),
        "news_db_exists": db_exists,
        "news_db_path": str(_DB),
        "db_articles": db_articles,
        "db_sources": db_sources,
        "readiness": gate,
        "feature_quality": diag["per_field"],
        "summary": {
            "articles": db_articles,
            "sources": db_sources,
            "news_rows": diag["total_news_rows"],
            "non_neutral_rows": diag["non_neutral_rows"],
            "xauusd_relevant_rows": diag["xauusd_relevant_rows"],
            "distinct_events": diag["distinct_events"],
            "dead_zero_features": diag["dead_zero_fields"],
            "date_range": None,
            "future_rejected": 0,
            "duplicates_removed": 0,
        },
        "benchmark_blocked": not gate["ready"],
        "reason_blocked": (
            "REAL NEWS DATA NOT AVAILABLE — collect news first (source ingest + "
            "analysis must populate artifacts/news.db). Do NOT run A/B/C/D with "
            "synthetic or empty news."
            if not gate["ready"]
            else ""
        ),
    }

    _OUT_JSON.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")

    lines = [
        "# News Benchmark Readiness Gate (PHASE 13B)",
        "",
        f"- Generated: {report['generated_at']}",
        f"- News DB exists: **{db_exists}** ({_DB})",
        f"- DB articles: {db_articles} | sources: {db_sources}",
        "",
        "## Gate",
        "",
        f"**READY: {gate['ready']}**",
        "",
        "| Check | Result |",
        "| --- | --- |",
    ]
    for k, v in gate["checks"].items():
        lines.append(f"| {k} | {v} |")
    lines += [
        "",
        "## Feature quality (per 12-field schema)",
        "",
        "| Field | nonzero | unique | min | max | mean | std | missing |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for f, st in diag["per_field"].items():
        lines.append(
            f"| {f} | {st['nonzero']} | {st['unique']} | {st['min']} | {st['max']} "
            f"| {st['mean']} | {st['std']} | {st['missing']} |"
        )
    lines += [
        "",
        "## Verdict",
        "",
        report["reason_blocked"] or "Gate GREEN — real news benchmark may proceed.",
        "",
    ]
    _OUT_MD.write_text("\n".join(lines), encoding="utf-8")

    print(f"wrote {_OUT_JSON}")
    print(f"wrote {_OUT_MD}")
    print(f"READY={gate['ready']} news_db_exists={db_exists} news_rows={diag['total_news_rows']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
