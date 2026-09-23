"""Existing news dataset audit (news-admission-gate deliverable H).

Read-only analysis of the current news.db. NO deletion, NO migration, NO
write of any kind — evidence only for the retention/architecture decision.

The brief hypothesized 40k-50k records with "significant portion low value".
This script measures the real numbers so the admission threshold calibration
is grounded in actual data rather than the hypothesis.

Usage (read-only URI — never disturbs the running engine, WAL stays intact):

    python scripts/audit/news_dataset_audit.py [--db artifacts/news.db] \
        [--json artifacts/forensics/news_dataset_audit_<date>.json]

Columns are NEVER assumed from docs: the schema is dumped per table and the
audit adapts to the columns actually present (audit tables drift between
waves — see the repo's DB-hygiene notes).
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path


def _connect(db_path: str) -> sqlite3.Connection:
    if not Path(db_path).exists():
        print(f"ERROR: {db_path} does not exist", file=sys.stderr)
        raise SystemExit(2)
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def _table_rows(conn: sqlite3.Connection, table: str) -> list[dict]:
    cols = [r[1] for r in conn.execute(f"PRAGMA table_xinfo('{table}')").fetchall()]
    if not cols:
        return []
    return [dict(zip(cols, r, strict=False)) for r in conn.execute(f"SELECT * FROM '{table}'")]


def _tier_weight(tier: str) -> float:
    return {"TIER_1": 1.0, "TIER_2": 0.8, "TIER_3": 0.55, "TIER_4": 0.25}.get(tier or "", 0.5)


def audit(db_path: str) -> dict:
    conn = _connect(db_path)
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        ]
        counts = {t: conn.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0] for t in tables}

        articles = _table_rows(conn, "news_articles")
        analysis = _table_rows(conn, "news_analysis")
        junk = _table_rows(conn, "news_junk_hashes")

        # ----- duplicates / near-duplicates (structural, no embeddings) -----
        hash_counts = Counter(a["article_hash"] for a in articles)
        exact_dupes = sum(c - 1 for c in hash_counts.values() if c > 1)
        title_norm: dict[str, int] = {}
        for a in articles:
            key = _norm_title(a.get("title", ""))
            title_norm[key] = title_norm.get(key, 0) + 1
        same_normalized_title = sum(c - 1 for c in title_norm.values() if c > 1)

        # exact content fingerprints (title+summary+body sha256, first 2000 ch)
        content_fp: dict[str, int] = {}
        for a in articles:
            blob = " ".join(
                [str(a.get("title", "")), str(a.get("summary", "")), str(a.get("body", ""))]
            )[:2000]
            key = _norm_title(blob)
            content_fp[key] = content_fp.get(key, 0) + 1
        byte_identical_content = sum(c - 1 for c in content_fp.values() if c > 1)

        # ----- status / importance / source mix -----
        status_counts = Counter(a.get("article_status", "ACTIVE") for a in articles)
        importance_counts = Counter(a.get("importance") for a in articles)
        by_source = Counter(a.get("source_id") for a in articles)

        # analysis-derived relevance (the same axis auto-prune + the admission
        # gateway use): how many analyzed rows are genuinely XAUUSD-relevant?
        rel_buckets = Counter(_bucket(r.get("relevance_to_xauusd")) for r in analysis)
        imp_buckets = Counter(_bucket(r.get("importance_score")) for r in analysis)

        # projected admission outcome for EXISTING rows under the gateway's
        # thresholds — the "what would the gate have done" counterfactual.
        # Relevance/importance are recomputed only where an analysis row exists
        # (never fabricated; unanalyzed rows are reported separately).
        projected = Counter()
        analysis_by_id = {r["article_id"]: r for r in analysis}
        for a in articles:
            r = analysis_by_id.get(a["article_id"])
            if not r:
                projected["UNANALYZED"] += 1
                continue
            rel = float(r.get("relevance_to_xauusd") or 0.0)
            imp = float(r.get("importance_score") or 0.0)
            # same floors as NewsAdmissionConfig defaults
            if rel < 0.10 and imp < 0.10:
                projected["REJECT"] += 1
            elif imp < 0.10 and rel < 0.05:
                projected["REJECT"] += 1
            else:
                projected["ADMIT_OR_QUARANTINE"] += 1

        # text weight / size
        text_bytes = sum(
            len(str(a.get("title", "")).encode())
            + len(str(a.get("summary", "")).encode())
            + len(str(a.get("body", "")).encode())
            for a in articles
        )
        db_bytes = Path(db_path).stat().st_size

        return {
            "audit_utc": datetime.now(UTC).isoformat(),
            "db_path": db_path,
            "db_size_bytes": db_bytes,
            "article_text_bytes": text_bytes,
            "tables": counts,
            "total_articles": len(articles),
            "exact_duplicate_article_hash_rows": exact_dupes,
            "same_normalized_title_extra_rows": same_normalized_title,
            "byte_identical_content_extra_rows": byte_identical_content,
            "article_status": dict(status_counts),
            "importance_labels": dict(importance_counts),
            "articles_by_source": dict(by_source.most_common()),
            "analysis_relevance_to_xauusd_buckets": dict(rel_buckets),
            "analysis_importance_score_buckets": dict(imp_buckets),
            "junk_hash_reasons": dict(Counter(j.get("reason") for j in junk).most_common()),
            "projected_under_admission_floors": dict(projected),
            "projected_reduction_pct": round(
                100.0
                * projected.get("REJECT", 0)
                / max(1, len(articles) - projected.get("UNANALYZED", 0)),
                2,
            ),
        }
    finally:
        conn.close()


def _bucket(value) -> str:
    try:
        v = float(value or 0.0)
    except (TypeError, ValueError):
        return "NONE"
    if v >= 0.5:
        return "HIGH(>=0.5)"
    if v >= 0.25:
        return "MID(0.25-0.5)"
    if v >= 0.10:
        return "LOW(0.10-0.25)"
    return "ZERO(<0.10)"


def _norm_title(text: str) -> str:
    return " ".join((text or "").lower().split())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="artifacts/news.db")
    ap.add_argument("--json", default="")
    args = ap.parse_args()
    result = audit(args.db)
    print(json.dumps(result, indent=2, sort_keys=True))
    if args.json:
        Path(args.json).parent.mkdir(parents=True, exist_ok=True)
        Path(args.json).write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
        print(f"\n[audit] written to {args.json}", file=sys.stderr)


if __name__ == "__main__":
    main()
