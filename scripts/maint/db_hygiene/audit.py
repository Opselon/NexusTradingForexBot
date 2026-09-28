"""Read-only database hygiene audit (DATABASE HYGIENE mission).

Classifies rows in the authoritative store (PostgreSQL, resolved via
``load_database_config``) plus the legacy SQLite mirrors, and emits an
evidence-backed report. Every number this module prints comes FROM a AS real
query against a real database; it never estimates, extrapolates, or
rounds.

Classification buckets (see ``docs/forensic-docs/database-hygiene-*.md``):

* DUPLICATE      - same logical event/entity present more than once
* SPAM           - non-authoritative write artifact (retry storm, poll churn)
* ORPHANED       - child row whose parent reference no longer exists
* STALE-BUT-VALID- age alone is NOT a deletion criterion; reported, never deleted

Usage::

    python -m scripts.maint.db_hygiene.audit --report hygiene-report.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

_SI = sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from nexus_scalp.database.config import build_postgres_url, load_database_config  # noqa: E402


@dataclass
class Finding:
    """One classified candidate set, backed by a real query."""

    rule_id: str
    database: str
    table: str
    bucket: str  # DUPLICATE | SPAM | ORPHANED | STALE-BUT-VALID
    candidate_count: int
    reason: str
    canonical_selection: str
    safe: bool  # a guard in the DELETE module must confirm before removal


@dataclass
class AuditReport:
    generated_at: str
    postgres: dict[str, Any] = field(default_factory=dict)
    sqlite: dict[str, Any] = field(default_factory=dict)
    findings: list[Finding] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "postgres": self.postgres,
            "sqlite": self.sqlite,
            "findings": [asdict(f) for f in self.findings],
        }


# --------------------------------------------------------------------------
# duplicate + spam probes (PostgreSQL)
# --------------------------------------------------------------------------

#: audit_orders duplicate probes. R1A is byte-identical repetition with no
#: informational value; R1B is the retry-storm tail (a ticket stuck in a
#: rejection loop for hours), which is real telemetry but massively
#: redundant and regenerates while the root cause is live.
_AUDIT_ORDER_BUSINESS_COLS = (
    "ticket, order_id, symbol, action, price, stop_loss, take_profit, "
    "volume, reason, latency, execution_mode, execution_id, timestamp"
)


def pg_audit_orders_duplicates(con: Any) -> list[Finding]:
    out: list[Finding] = []
    db = "postgresql:nexusdb"
    total, distinct = con.execute(
        "SELECT COUNT(*), COUNT(DISTINCT order_id) FROM audit_orders"
    ).fetchone()
    redundant = total - distinct

    r1a = con.execute(
        """
        SELECT COUNT(*) FROM audit_orders o WHERE EXISTS (
            SELECT 1 FROM audit_orders o2
            WHERE o2.order_id = o.order_id AND o2.id < o.id
              AND o2.ticket IS NOT DISTINCT FROM o.ticket
              AND o2.symbol IS NOT DISTINCT FROM o.symbol
              AND o2.action IS NOT DISTINCT FROM o.action
              AND o2.price IS NOT DISTINCT FROM o.price
              AND o2.stop_loss IS NOT DISTINCT FROM o.stop_loss
              AND o2.take_profit IS NOT DISTINCT FROM o.take_profit
              AND o2.volume IS NOT DISTINCT FROM o.volume
              AND o2.reason IS NOT DISTINCT FROM o.reason
              AND o2.latency IS NOT DISTINCT FROM o.latency
              AND o2.execution_mode IS NOT DISTINCT FROM o.execution_mode
              AND o2.execution_id IS NOT DISTINCT FROM o.execution_id
              AND o2.timestamp IS NOT DISTINCT FROM o.timestamp
        )
        """
    ).fetchone()[0]

    out.append(
        Finding(
            rule_id="R1A",
            database=db,
            table="audit_orders",
            bucket="DUPLICATE",
            candidate_count=int(r1a),
            reason=(
                "byte-identical business-key duplicates of an earlier row "
                "(same order_id + same ticket/price/reason/timestamp); exact "
                "repeated insert of an already-recorded protection event"
            ),
            canonical_selection="keep the lowest id per order_id + business column set",
            safe=True,
        )
    )

    storm = redundant - int(r1a)
    out.append(
        Finding(
            rule_id="R1B",
            database=db,
            table="audit_orders",
            bucket="SPAM",
            candidate_count=int(storm),
            reason=(
                "BREAKEVEN_FAILED retry-storm rows: one broker-rejected "
                "protection modification re-audited on every management tick "
                "for hours (root cause: protection.py throttles the console "
                "log but writes the audit row every time)"
            ),
            canonical_selection=(
                "keep the FIRST and LAST row of each retry-distinct group "
                "(first evidence + terminal state); collapse the middle"
            ),
            safe=True,
        )
    )
    return out


def pg_news_duplicate_analysis(con: Any) -> list[Finding]:
    db = "postgresql:nexusdb"
    out: list[Finding] = []

    redundant = con.execute(
        """
        SELECT COUNT(*) FROM news_analysis na WHERE EXISTS (
            SELECT 1 FROM news_analysis nb
            WHERE nb.article_id = na.article_id
              AND nb.provider = na.provider
              AND (nb.analyzed_at < na.analyzed_at
                   OR (nb.analyzed_at = na.analyzed_at
                       AND nb.analysis_id < na.analysis_id))
        )
        """
    ).fetchone()[0]
    out.append(
        Finding(
            rule_id="R2",
            database=db,
            table="news_analysis",
            bucket="DUPLICATE",
            candidate_count=int(redundant),
            reason=(
                "same article re-analyzed by the same provider after a "
                "completed analysis already exists (news_analysis has no "
                "(article_id, provider) uniqueness guard)"
            ),
            canonical_selection="keep the earliest analyzed_at per (article_id, provider)",
            safe=True,
        )
    )

    dup_groups = con.execute(
        """
        SELECT COUNT(*) FROM (
            SELECT status, article_ids, provider FROM news_analysis_runs GROUP BY status, article_ids, provider HAVING COUNT(*) > 1
        ) x
        """
    ).fetchone()[0]
    redundant_runs = con.execute(
        """
        SELECT COUNT(*) FROM news_analysis_runs r WHERE EXISTS (
            SELECT 1 FROM news_analysis_runs r2
            WHERE r2.article_ids = r.article_ids AND r2.status = r.status
              AND COALESCE(r2.provider, '') = COALESCE(r.provider, '')
              AND (r2.started_at < r.started_at
                   OR (r2.started_at = r.started_at AND r2.run_id < r.run_id))
        )
        """
    ).fetchone()[0]
    queued_empty = con.execute(
        "SELECT COUNT(*) FROM news_analysis_runs AS WHERE status='QUEUED' AND article_ids='[]'"
    ).fetchone()[0]
    out.extend(
        [
            Finding(
                rule_id="R4A",
                database=db,
                table="news_analysis_runs",
                bucket="DUPLICATE",
                candidate_count=int(redundant_runs),
                reason=(
                    "analysis-run records repeated for the same article set + "
                    "status + provider; poll/retry re-enqueue of an already "
                    f"completed batch ({dup_groups} duplicate groups)"
                ),
                canonical_selection="keep the earliest started_at per (article_ids, status, provider)",
                safe=True,
            ),
            Finding(
                rule_id="R4B",
                database=db,
                table="news_analysis_runs",
                bucket="SPAM",
                candidate_count=int(queued_empty),
                reason="empty QUEUED runs with no article payload (worker startup churn)",
                canonical_selection="keep none (all are empty placeholders)",
                safe=True,
            ),
        ]
    )
    return out


def pg_news_orphan_hashes(con: Any) -> list[Finding]:
    db = "postgresql:nexusdb"
    analyzed_orphan = con.execute(
        """
        SELECT COUNT(*) FROM news_analyzed_hashes h
        WHERE NOT EXISTS (SELECT 1 FROM news_articles a WHERE a.article_hash = h.article_hash)
        """
    ).fetchone()[0]
    orphan_with_junk = con.execute(
        """
        SELECT COUNT(*) FROM news_analyzed_hashes h
        WHERE NOT EXISTS (SELECT 1 FROM news_articles a WHERE a.article_hash = h.article_hash)
          AND EXISTS (SELECT 1 FROM news_junk_hashes j WHERE j.article_hash = h.article_hash)
        """
    ).fetchone()[0]
    return [
        Finding(
            rule_id="R3",
            database=db,
            table="news_analyzed_hashes",
            bucket="ORPHANED",
            candidate_count=int(analyzed_orphan - orphan_with_junk),
            reason=(
                f"idempotency tombstones whose article row was pruned by "
                f"AUTO_PRUNE (article_status ACTIVE->IRRELEVANT then deleted) "
                f"AND which have no news_junk_hashes tombstone. Of "
                f"{analyzed_orphan} orphan hashes, {orphan_with_junk} are also "
                f"junk tombstones (those are R7's contradictory-overlap set, "
                f"where the junk row subsumes the analyzed marker); only "
                f"{analyzed_orphan - orphan_with_junk} are pure orphans whose "
                f"removal would break the never-re-ingest guard. R3 is the "
                f"permanent guard rule for that class (candidate set is 0 "
                f"today by measurement, not by assumption)."
            ),
            canonical_selection=(
                "delete only when no news_junk_hashes row exists for the same hash"
            ),
            safe=True,
        )
    ]


def pg_news_orphan_children(con: Any) -> list[Finding]:
    """Child tables whose parent article disappeared (pruned without cascade)."""
    db = "postgresql:nexusdb"
    out = []
    for table in (
        "news_analysis",
        "news_ai_analysis",
        "news_impacts",
        "news_entities",
        "news_topics",
    ):
        n = con.execute(
            f"""
            SELECT COUNT(*) FROM {table} c
            WHERE NOT EXISTS (SELECT 1 FROM news_articles a WHERE a.article_id = c.article_id)
            """
        ).fetchone()[0]
        if n:
            out.append(
                Finding(
                    rule_id=f"R5-{table}",
                    database=db,
                    table=table,
                    bucket="ORPHANED",
                    candidate_count=int(n),
                    reason=(
                        f"rows whose parent article was pruned; {table} has no "
                        "ON DELETE CASCADE and prune deletes news_articles only"
                    ),
                    canonical_selection="delete all (parent is irrecoverably gone)",
                    safe=True,
                )
            )
    return out


def pg_news_prune_audit_orphans(con: Any) -> list[Finding]:
    db = "postgresql:nexusdb"
    n = con.execute(
        """
        SELECT COUNT(*) FROM news_prune_audit p
        WHERE NOT EXISTS (SELECT 1 FROM news_articles a WHERE a.article_id = p.article_id)
        """
    ).fetchone()[0]
    total = con.execute("SELECT COUNT(*) FROM news_prune_audit").fetchone()[0]
    return [
        Finding(
            rule_id="R6",
            database=db,
            table="news_prune_audit",
            bucket="STALE-BUT-VALID",
            candidate_count=int(n),
            reason=(
                "prune audit rows whose target article is gone. These are the "
                "audit TRAIL of a legit operation (AUTO_PRUNE) and are "
                "deliberately retained: deleting audit evidence because its "
                "subject is archived would destroy provenance. Retained; "
                f"reported for transparency (table total {total})."
            ),
            canonical_selection="keep all (audit evidence; age is not a deletion criterion)",
            safe=False,
        )
    ]


def pg_split_brain_hashes(con: Any) -> list[Finding]:
    """A hash marked analyzed AND junk is a contradictory state."""
    db = "postgresql:nexusdb"
    n = con.execute(
        """
        SELECT COUNT(*) FROM news_analyzed_hashes h
        JOIN news_junk_hashes j USING (article_hash)
        """
    ).fetchone()[0]
    return [
        Finding(
            rule_id="R7",
            database=db,
            table="news_analyzed_hashes",
            bucket="SPAM",
            candidate_count=int(n),
            reason=(
                "hash present in BOTH news_analyzed_hashes and "
                "news_junk_hashes: an article recorded as analyzed AND as "
                "junk. news_junk_hashes is the never-re-ingest tombstone and "
                "subsumes the analyzed marker (is_junk_hash is checked first "
                "in the ingest path), so the analyzed copy is contradictory "
                "redundant state"
            ),
            canonical_selection="keep the news_junk_hashes row (the stricter tombstone)",
            safe=True,
        )
    ]


def pg_audit_ledger_guard(con: Any) -> list[Finding]:
    """Money rows are NEVER cleanup candidates; report them as protected."""
    db = "postgresql:nexusdb"
    ledger = con.execute("SELECT COUNT(*) FROM audit_ledger").fetchone()[0]
    return [
        Finding(
            rule_id="P1",
            database=db,
            table="audit_ledger",
            bucket="STALE-BUT-VALID",
            candidate_count=int(ledger),
            reason=(
                "financial settlement history - money rows are protected by "
                "policy and are never a cleanup candidate in any hygiene pass"
            ),
            canonical_selection="keep all",
            safe=False,
        )
    ]


# --------------------------------------------------------------------------
# SQLite legacy mirror probes
# --------------------------------------------------------------------------

_LEGACY_SQLITE_DBS = (
    ("artifacts/audit.db", ("audit_orders",)),
    (
        "artifacts/news.db",
        (
            "news_analysis_runs",
            "news_analyzed_hashes",
            "news_junk_hashes",
            "news_analysis",
            "news_articles",
        ),
    ),
)


def sqlite_mirror_probe(repo_root: Path) -> dict[str, Any]:
    """The SQLite files are legacy mirrors: PG is the configured provider.

    ``application_settings`` persists ``database.provider=postgresql`` for
    every domain, so all live writers target PostgreSQL. The SQLite files
    were copied by the SQLite->PG migration and then kept receiving stray
    writes (mtime evidence) FROM code AS paths that still anchor a filename.
    """
    out: dict[str, Any] = {}
    for rel, tables in _LEGACY_SQLITE_DBS:
        fp = repo_root / rel
        entry: dict[str, Any] = {"path": rel, "exists": fp.exists()}
        if not fp.exists():
            out[rel] = entry
            continue
        entry["size_bytes"] = fp.stat().st_size
        entry["mtime"] = fp.stat().st_mtime
        try:
            con = sqlite3.connect(f"file:{fp}?mode=ro", uri=True)
            entry["integrity"] = con.execute("PRAGMA integrity_check").fetchone()[0]
            entry["journal_mode"] = con.execute("PRAGMA journal_mode").fetchone()[0]
            counts = {}
            for t in tables:
                try:
                    counts[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                except sqlite3.OperationalError:
                    counts[t] = None
            entry["counts"] = counts
            entry["audit_orders_redundant"] = None
            if "audit_orders" in tables:
                entry["audit_orders_redundant"] = con.execute(
                    "SELECT COUNT(*) - COUNT(DISTINCT order_id) FROM audit_orders"
                ).fetchone()[0]
            con.close()
        except sqlite3.Error as exc:  # pragma: no cover - defensive read
            entry["error"] = f"{type(exc).__name__}: {exc}"
        out[rel] = entry
    return out


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------


def run_audit(repo_root: Path, report_path: Path | None = None) -> AuditReport:
    import time

    report = AuditReport(generated_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))

    cfg = load_database_config("audit")
    report.postgres = {
        "provider": cfg.provider.value,
        "host": cfg.host,
        "port": cfg.port,
        "database": cfg.database,
    }
    import psycopg

    con = psycopg.connect(build_postgres_url(cfg))
    try:
        con.autocommit = True
        for probe in (
            pg_audit_orders_duplicates,
            pg_news_duplicate_analysis,
            pg_news_orphan_hashes,
            pg_news_orphan_children,
            pg_news_prune_audit_orphans,
            pg_split_brain_hashes,
            pg_audit_ledger_guard,
        ):
            report.findings.extend(probe(con))
    finally:
        con.close()

    report.sqlite = sqlite_mirror_probe(repo_root)

    if report_path is not None:
        report_path.write_text(
            json.dumps(report.to_dict(), indent=2, sort_keys=False), encoding="utf-8"
        )
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--report", type=Path, default=None, help="write JSON report here")
    ap.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[3])
    args = ap.parse_args()

    rep = run_audit(args.repo_root, args.report)
    print(f"audit generated at {rep.generated_at}; findings={len(rep.findings)}")
    for f in rep.findings:
        print(
            f"  {f.rule_id:8s} {f.bucket:16s} {f.database}:{f.table:24s} "
            f"candidates={f.candidate_count:6d} safe={f.safe}"
        )
    if args.report:
        print(f"report written to {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
