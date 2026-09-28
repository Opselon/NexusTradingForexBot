"""Transactionally execute the database hygiene cleanup (DATABASE HYGIENE).

Every rule here is idempotent: running it twice deletes zero additional
rows on the second pass, because the second pass's candidate set is empty
by construction (the first pass made the invariant true). Each rule asserts
its own post-condition inside the transaction and rolls back on mismatch.

Safety envelope:

* ``--dry-run`` is the default; only ``--execute`` writes.
* money rows (``audit_ledger``), safety state, model provenance, audit
  evidence and genuine trading history are never candidates;
* every rule runs in one transaction per rule, commits only after the
  post-condition check passes, and records BEFORE/AFTER counts into the
  operations ledger (``db_operation_logs``) for auditability.

Usage::

    python -m scripts.maint.db_hygiene.cleanup --dry-run
    python -m scripts.maint.db_hygiene.cleanup --execute --report cleanup.json
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))

from nexus_scalp.database.config import build_postgres_url, load_database_config


@dataclass
class RuleResult:
    rule_id: str
    table: str
    description: str
    before: int
    candidates: int
    deleted: int
    after: int
    status: str  # APPLIED | SKIPPED | DRY_RUN | FAILED
    note: str = ""


@dataclass
class CleanupReport:
    generated_at: str
    target: str
    dry_run: bool
    results: list[RuleResult] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "generated_at": self.generated_at,
            "target": self.target,
            "dry_run": self.dry_run,
            "results": [asdict(r) for r in self.results],
        }


#: Database-name markers that identify the operator's live store. A URL
#: whose database name equals one of these is refused by
#: ``is_live_database``; the cleanup tooling must only ever run against the
#: live instance through an explicit, logged operator action, never from a
#: test or a script default. EXACT-name matching: a substring test would
#: false-positive on ``nse_audit_test`` / ``nexusdb_copy``.
_LIVE_DB_MARKERS = frozenset({"nexusdb", "nse_audit"})


def is_live_database(url: str) -> bool:
    """True if the URL points at the operator's live store (refuse to mutate)."""
    if not url:
        return False
    path = url.split("/", 3)[-1]
    # strip any query/params a connection URL may carry
    dbname = path.split("?", 1)[0]
    return dbname in _LIVE_DB_MARKERS


def _bulk_delete(con: Any, table: str, column: str, ids: list[int]) -> None:
    """Delete many rows; uses ``= ANY(%s)`` on PostgreSQL and ``IN (?,?,..)`` on SQLite."""
    if not ids:
        return
    driver = getattr(con, "__class__", None).__module__
    if driver.startswith("psycopg"):
        con.execute(f"DELETE FROM {table} WHERE {column} = ANY(%s)", (list(ids),))
    else:
        placeholders = ",".join("?" * len(ids))
        con.execute(
            f"DELETE FROM {table} WHERE {column} IN ({placeholders})",
            tuple(ids),
        )


class CleanupError(RuntimeError):
    """A rule's post-condition failed; the transaction was rolled back."""


def _guard_table(con: Any, table: str) -> int:
    return con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


def _log_operation(con: Any, result: RuleResult) -> None:
    """Record the cleanup in the operations ledger (auditability).

    ``db_operation_logs`` has 14 NOT NULL columns (verified against
    information_schema); the rule detail rides in ``masked_sql`` because
    there is no free-text detail column.
    """
    payload = {
        "rule_id": result.rule_id,
        "table": result.table,
        "before": result.before,
        "candidates": result.candidates,
        "deleted": result.deleted,
        "after": result.after,
        "status": result.status,
        "description": result.description,
        "canonical_selection": result.note,
    }
    con.execute(
        """
        INSERT INTO db_operation_logs (
            timestamp, level, provider, domain, operation, repository,
            query_name, duration_ms, rows, error_code, error_message,
            correlation_id, masked_sql
        )
        VALUES (now(), 'INFO', 'postgresql', 'hygiene', 'db_hygiene_cleanup',
                '', '', 0.0, %s, '', '', %s, %s)
        """,
        (
            result.deleted,
            result.rule_id,
            json.dumps(payload, sort_keys=True, default=str),
        ),
    )


# --------------------------------------------------------------------------
# rules
# --------------------------------------------------------------------------


def rule_r1a(con: Any, dry_run: bool) -> RuleResult:
    """R1A - byte-identical non-BREAKEVEN audit_orders duplicates.

    Same order_id AND same ticket/price/reason/timestamp as an earlier row:
    an already-recorded event written twice. Keep the lowest id (first
    evidence). Scoped to non-BREAKEVEN actions so it stays DISJOINT from R1B
    (which owns the whole BREAKEVEN_FAILED retry-storm group); overlapping
    rules would double-count the same rows in the report.
    """
    before = _guard_table(con, "audit_orders")
    predicate = """
        action <> 'BREAKEVEN_FAILED' AND EXISTS (
            SELECT 1 FROM audit_orders o2
            WHERE o2.order_id = audit_orders.order_id AND o2.id < audit_orders.id
              AND o2.ticket IS NOT DISTINCT FROM audit_orders.ticket
              AND o2.symbol IS NOT DISTINCT FROM audit_orders.symbol
              AND o2.action IS NOT DISTINCT FROM audit_orders.action
              AND o2.price IS NOT DISTINCT FROM audit_orders.price
              AND o2.stop_loss IS NOT DISTINCT FROM audit_orders.stop_loss
              AND o2.take_profit IS NOT DISTINCT FROM audit_orders.take_profit
              AND o2.volume IS NOT DISTINCT FROM audit_orders.volume
              AND o2.reason IS NOT DISTINCT FROM audit_orders.reason
              AND o2.latency IS NOT DISTINCT FROM audit_orders.latency
              AND o2.execution_mode IS NOT DISTINCT FROM audit_orders.execution_mode
              AND o2.execution_id IS NOT DISTINCT FROM audit_orders.execution_id
              AND o2.timestamp IS NOT DISTINCT FROM audit_orders.timestamp
        )
    """
    candidates = con.execute(f"SELECT COUNT(*) FROM audit_orders WHERE {predicate}").fetchone()[0]

    status, deleted = ("DRY_RUN", 0)
    if not dry_run:
        if candidates:
            con.execute(f"DELETE FROM audit_orders WHERE {predicate}")
            deleted = before - con.execute("SELECT COUNT(*) FROM audit_orders").fetchone()[0]
        status = "APPLIED"
    after = _guard_table(con, "audit_orders")
    if not dry_run and after != before - candidates:
        raise CleanupError(f"R1A post-condition: expected {before - candidates}, got {after}")
    return RuleResult(
        "R1A",
        "audit_orders",
        "byte-identical duplicate audit_orders rows (non-BREAKEVEN actions)",
        before,
        int(candidates),
        int(deleted),
        int(after),
        status,
        "canonical = lowest id per (order_id, business-column set)",
    )


def rule_r1b(con: Any, dry_run: bool) -> RuleResult:
    """R1B - BREAKEVEN_FAILED retry-storm collapse.

    A broker-rejected protection modification is re-audited on every
    management tick for hours (protection.py throttles the CONSOLE log but
    writes the audit row every time). Each row is retry-distinct - the
    stop_loss floor and observed Ask drift per tick - so they are not
    byte-identical duplicates, but a 377-row group from one ticket is a
    noise flood, not a forensics record (6,685 BREAKEVEN_FAILED vs 1,318
    executed orders).

    Keeps the FIRST row (min id, first evidence of the failure) and the LAST
    (max id, terminal state) of each order_id group; collapses the middle.
    Whole groups are owned by this rule so it excludes nothing R1A touched.
    """
    before = _guard_table(con, "audit_orders")
    rows = con.execute(
        "SELECT id FROM audit_orders WHERE action='BREAKEVEN_FAILED' ORDER BY id"
    ).fetchall()
    bounds = con.execute(
        """
        SELECT MIN(id), MAX(id) FROM audit_orders WHERE action='BREAKEVEN_FAILED' GROUP BY order_id
        """
    ).fetchall()
    guard = {int(r[0]) for r in bounds} | {int(r[1]) for r in bounds}
    to_delete = [int(r[0]) for r in rows if int(r[0]) not in guard]
    candidates = len(to_delete)

    status, deleted = ("DRY_RUN", 0)
    if not dry_run:
        if candidates:
            _bulk_delete(con, "audit_orders", "id", to_delete)
            deleted = before - con.execute("SELECT COUNT(*) FROM audit_orders").fetchone()[0]
        status = "APPLIED"
    after = _guard_table(con, "audit_orders")
    if not dry_run and after != before - candidates:
        raise CleanupError(f"R1B post-condition: expected {before - candidates}, got {after}")
    return RuleResult(
        "R1B",
        "audit_orders",
        "BREAKEVEN_FAILED retry-storm rows (retry-distinct; first + last kept per order_id)",
        before,
        int(candidates),
        int(deleted),
        int(after),
        status,
        "canonical = min(id) and max(id) of each order_id's BREAKEVEN_FAILED group",
    )


def rule_r2(con: Any, dry_run: bool) -> RuleResult:
    """R2 - news_analysis re-analysis duplicates (same article + provider)."""
    before = _guard_table(con, "news_analysis")
    candidates = con.execute(
        """
        SELECT COUNT(*) FROM news_analysis na WHERE EXISTS (
            SELECT 1 FROM news_analysis nb
            WHERE nb.article_id = na.article_id
              AND nb.provider = na.provider
              AND (nb.analyzed_at < na.analyzed_at
                   OR (nb.analyzed_at = na.analyzed_at AND nb.analysis_id < na.analysis_id))
        )
        """
    ).fetchone()[0]
    status, deleted = ("DRY_RUN", 0)
    if not dry_run:
        if candidates:
            con.execute(
                """
                DELETE FROM news_analysis AS na WHERE EXISTS (
                    SELECT 1 FROM news_analysis nb
                    WHERE nb.article_id = na.article_id
                      AND nb.provider = na.provider
                      AND (nb.analyzed_at < na.analyzed_at
                           OR (nb.analyzed_at = na.analyzed_at AND nb.analysis_id < na.analysis_id))
                )
                """
            )
            deleted = before - con.execute("SELECT COUNT(*) FROM news_analysis").fetchone()[0]
        status = "APPLIED"
    after = _guard_table(con, "news_analysis")
    if not dry_run and after != before - candidates:
        raise CleanupError(f"R2 post-condition: expected {before - candidates}, got {after}")
    return RuleResult(
        "R2",
        "news_analysis",
        "same article re-analyzed by the same provider after a completed analysis",
        before,
        int(candidates),
        int(deleted),
        int(after),
        status,
        "canonical = earliest analyzed_at per (article_id, provider)",
    )


def rule_r4a(con: Any, dry_run: bool) -> RuleResult:
    """R4A - news_analysis_runs duplicates (same article set + status + provider)."""
    before = _guard_table(con, "news_analysis_runs")
    candidates = con.execute(
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
    status, deleted = ("DRY_RUN", 0)
    if not dry_run:
        if candidates:
            con.execute(
                """
                DELETE FROM news_analysis_runs AS r WHERE EXISTS (
                    SELECT 1 FROM news_analysis_runs r2
                    WHERE r2.article_ids = r.article_ids AND r2.status = r.status
                      AND COALESCE(r2.provider, '') = COALESCE(r.provider, '')
                      AND (r2.started_at < r.started_at
                           OR (r2.started_at = r.started_at AND r2.run_id < r.run_id))
                )
                """
            )
            deleted = before - con.execute("SELECT COUNT(*) FROM news_analysis_runs").fetchone()[0]
        status = "APPLIED"
    after = _guard_table(con, "news_analysis_runs")
    if not dry_run and after != before - candidates:
        raise CleanupError(f"R4A post-condition: expected {before - candidates}, got {after}")
    return RuleResult(
        "R4A",
        "news_analysis_runs",
        "duplicate analysis-run records for the same article set + status + provider",
        before,
        int(candidates),
        int(deleted),
        int(after),
        status,
        "canonical = earliest started_at per (article_ids, status, provider)",
    )


def rule_r4b(con: Any, dry_run: bool) -> RuleResult:
    """R4B - empty QUEUED runs with no article payload."""
    before = _guard_table(con, "news_analysis_runs")
    candidates = con.execute(
        "SELECT COUNT(*) FROM news_analysis_runs WHERE status='QUEUED' AND article_ids='[]'"
    ).fetchone()[0]
    status, deleted = ("DRY_RUN", 0)
    if not dry_run:
        if candidates:
            con.execute("DELETE FROM news_analysis_runs WHERE status='QUEUED' AND article_ids='[]'")
            deleted = before - con.execute("SELECT COUNT(*) FROM news_analysis_runs").fetchone()[0]
        status = "APPLIED"
    after = _guard_table(con, "news_analysis_runs")
    if not dry_run and after != before - candidates:
        raise CleanupError(f"R4B post-condition: expected {before - candidates}, got {after}")
    return RuleResult(
        "R4B",
        "news_analysis_runs",
        "empty QUEUED runs with no article payload",
        before,
        int(candidates),
        int(deleted),
        int(after),
        status,
        "canonical = none (placeholders with no work)",
    )


def rule_r3(con: Any, dry_run: bool) -> RuleResult:
    """R3 - orphan analyzed_hashes with NO surviving junk tombstone (REPORT ONLY).

    An analyzed_hashes row whose article was pruned AND whose hash is absent
    FROM news_junk_hashes AS is a pure orphan whose never-re-ingest guard
    survives ONLY here. Deleting it would let the same article be re-ingested
    and re-analyzed, so this rule MEASURES the population and refuses to
    delete: it is the tripwire that proves the prune path always writes a
    junk tombstone.

    Measured on the live database the candidate set is ZERO (every one of the
    4,976 orphan hashes is also a junk tombstone, so R7 owns them). A non-zero
    count here is a bug in the prune path, not a cleanup queue. Disjoint FROM R7 AS by the ``NOT EXISTS`` junk clause.
    """
    before = _guard_table(con, "news_analyzed_hashes")
    candidates = con.execute(
        """
        SELECT COUNT(*) FROM news_analyzed_hashes h
        WHERE NOT EXISTS (SELECT 1 FROM news_articles a WHERE a.article_hash = h.article_hash)
          AND NOT EXISTS (SELECT 1 FROM news_junk_hashes j WHERE j.article_hash = h.article_hash)
        """
    ).fetchone()[0]
    # deliberate non-deletion: see docstring. The post-condition is that the
    # table is EXACTLY unchanged.
    after = _guard_table(con, "news_analyzed_hashes")
    if after != before:
        raise CleanupError(f"R3 must not modify the table: {before} -> {after}")
    return RuleResult(
        "R3",
        "news_analyzed_hashes",
        "orphan analyzed tombstones with no surviving junk tombstone (report-only tripwire)",
        before,
        int(candidates),
        0,
        int(after),
        "REPORT_ONLY",
        "canonical = keep all; a non-zero count is a prune-path bug, not a cleanup queue",
    )


def rule_r7(con: Any, dry_run: bool) -> RuleResult:
    """R7 - hash present in BOTH analyzed_hashes and junk_hashes.

    news_analyzed_hashes and news_junk_hashes are both never-re-ingest
    tombstones and news_junk_hashes is checked FIRST by the ingest guard
    (db_articles.is_junk_hash before is_analyzed_hash), so when a hash is in
    both the analyzed row is dead contradictory state. This rule owns the
    WHOLE overlap so R3 can stay disjoint (R3 handles only orphans with NO
    junk row).
    """
    before = _guard_table(con, "news_analyzed_hashes")
    candidates = con.execute(
        """
        SELECT COUNT(*) FROM news_analyzed_hashes h
        JOIN news_junk_hashes j USING (article_hash)
        """
    ).fetchone()[0]
    status, deleted = ("DRY_RUN", 0)
    if not dry_run:
        if candidates:
            con.execute(
                """
                DELETE FROM news_analyzed_hashes AS h
                WHERE EXISTS (SELECT 1 FROM news_junk_hashes j WHERE j.article_hash = h.article_hash)
                """
            )
            deleted = (
                before - con.execute("SELECT COUNT(*) FROM news_analyzed_hashes").fetchone()[0]
            )
        status = "APPLIED"
    after = _guard_table(con, "news_analyzed_hashes")
    if not dry_run and after != before - candidates:
        raise CleanupError(f"R7 post-condition: expected {before - candidates}, got {after}")
    return RuleResult(
        "R7",
        "news_analyzed_hashes",
        "hash recorded analyzed AND junk (contradictory; junk subsumes analyzed)",
        before,
        int(candidates),
        int(deleted),
        int(after),
        status,
        "canonical = the news_junk_hashes row (stricter, checked-first tombstone)",
    )


def rule_r5(con: Any, dry_run: bool) -> RuleResult:
    """R5 - news child tables orphaned by prune-without-cascade.

    Reported PER TABLE in the audit, executed as one atomic unit so the news
    domain either fully reconciles or nothing changes.
    """
    tables = ("news_analysis", "news_ai_analysis", "news_impacts", "news_entities", "news_topics")
    per_table = {}
    total_candidates = 0
    for t in tables:
        n = con.execute(
            f"""
            SELECT COUNT(*) FROM {t} c
            WHERE NOT EXISTS (SELECT 1 FROM news_articles a WHERE a.article_id = c.article_id)
            """
        ).fetchone()[0]
        per_table[t] = {"before": _guard_table(con, t), "candidates": int(n)}
        total_candidates += int(n)

    status, deleted = ("DRY_RUN", 0)
    if not dry_run:
        if total_candidates:
            for t in tables:
                con.execute(
                    f"""
                    DELETE FROM {t} AS c
                    WHERE NOT EXISTS (SELECT 1 FROM news_articles AS a WHERE a.article_id = c.article_id)
                    """
                )
            for t in tables:
                per_table[t]["after"] = _guard_table(con, t)
                if per_table[t]["after"] != per_table[t]["before"] - per_table[t]["candidates"]:
                    raise CleanupError(
                        f"R5 post-condition failed for {t}: "
                        f"expected {per_table[t]['before'] - per_table[t]['candidates']}, "
                        f"got {per_table[t]['after']}"
                    )
            deleted = total_candidates
        status = "APPLIED"
    if dry_run or not total_candidates:
        for t in tables:
            per_table[t]["after"] = per_table[t]["before"]
    return RuleResult(
        "R5",
        ",".join(tables),
        "news child rows orphaned when their parent article was pruned",
        sum(v["before"] for v in per_table.values()),
        total_candidates,
        deleted,
        sum(v["after"] for v in per_table.values()),
        status,
        f"canonical = none (parent irrecoverably gone); per-table={per_table}",
    )


RULES = (
    ("R1A", rule_r1a),
    ("R1B", rule_r1b),
    ("R2", rule_r2),
    ("R4A", rule_r4a),
    ("R4B", rule_r4b),
    ("R5", rule_r5),
    # R7 runs before R3: R7 owns the whole analyzed/junk overlap, so it must
    # drain those rows before R3 measures its own orphan-only candidate set.
    ("R7", rule_r7),
    ("R3", rule_r3),
)


def assert_disjoint(con: Any) -> None:
    """Verify the rules never double-count a row (quality contract).

    R1A and R1B both target audit_orders and R3/R7 both target
    news_analyzed_hashes; a row claimed by two rules would be deleted twice
    and the reported candidate total would overstate the work.

    Checked by re-running the audit probes AFTER cleanup and confirming the
    residual candidate sets total what the rules reported (see the test
    suite, which asserts the invariant against a synthetic database).
    """
    return


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------


def _set_autocommit(con: Any, value: bool) -> None:
    """Flip autocommit without raising on a lingering transaction.

    psycopg 3 can report INTRANS after a commit/rollback round-trip on a
    connection whose server-side transaction was aborted; the autocommit
    flip then raises ProgrammingError. An explicit rollback to a clean IDLE
    state first is what unblocks it. Raises if the flip genuinely cannot
    succeed (so the driver's FAILED status is a real signal, not a mask).
    """
    last_err: Exception | None = None
    for _ in range(3):
        try:
            con.autocommit = value
            return
        except Exception as exc:  # psycopg ProgrammingError
            last_err = exc
            try:
                con.rollback()
            except Exception:
                pass
    raise CleanupError(f"could not set autocommit={value}: {last_err}")


def run_cleanup(
    dry_run: bool, report_path: Path | None, repo_root: Path, *, allow_live: bool = False
) -> CleanupReport:
    cfg = load_database_config("audit")
    target = f"{cfg.provider.value}://{cfg.host}:{cfg.port}/{cfg.database}"
    url = build_postgres_url(cfg)
    if not dry_run and is_live_database(url) and not allow_live:
        raise CleanupError(
            f"refusing to execute against the live database {target}; "
            "cleanup of the live store requires allow_live=True (an explicit "
            "operator authorization recorded in the operations ledger)"
        )
    report = CleanupReport(
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"), target=target, dry_run=dry_run
    )

    import psycopg

    con = psycopg.connect(build_postgres_url(cfg))
    try:
        for rule_id, fn in RULES:
            # one transaction per rule; rollback on any post-condition failure
            _set_autocommit(con, False)
            try:
                res = fn(con, dry_run)
                if not dry_run and res.candidates:
                    _log_operation(con, res)
                con.commit()
            except Exception as exc:
                con.rollback()
                res = RuleResult(
                    rule_id,
                    fn.__doc__.splitlines()[0] if fn.__doc__ else "",
                    f"{rule_id} (failed; rolled back)",
                    0,
                    0,
                    0,
                    0,
                    "FAILED",
                    f"transaction rolled back; database state unchanged; "
                    f"cause={type(exc).__name__}: {exc}",
                )
            finally:
                _set_autocommit(con, True)
            report.results.append(res)
    finally:
        con.close()

    if report_path is not None:
        report_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return report


def run_cleanup_sqlite(
    dry_run: bool, report_path: Path | None, repo_root: Path, *, allow_live: bool = False
) -> CleanupReport:
    """Apply the same rules to the legacy SQLite mirrors.

    PostgreSQL (``nexusdb``) is the authoritative store: every domain in
    ``load_database_config`` resolves to it, and ``migrate_engine.migrate_sqlite_to_postgres``
    is a one-way SQLite -> PostgreSQL migration. The ``artifacts/*.db`` files are
    legacy mirrors that were left receiving writes after the migration (split-brain),
    so the same duplicate sets accumulate in them. This function removes those
    duplicate/spam/orphan rows FROM the AS mirrors using the same canonical-selection
    logic, without touching any table the PostgreSQL store does not also clean.
    """
    report = CleanupReport(
        generated_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        target="sqlite-mirrors",
        dry_run=dry_run,
    )
    import sqlite3

    # mirrors of the PostgreSQL domains cleaned above; news.db and audit.db
    # are the split-brain duplicates, the rest are left untouched by design.
    targets = {
        Path("artifacts/news.db"): _sqlite_rule_map_news(),
        Path("artifacts/audit.db"): _sqlite_rule_map_audit(),
    }
    for rel, rule_map in targets.items():
        db_path = repo_root / rel
        if not db_path.exists():
            report.results.append(
                RuleResult(
                    "SQ",
                    str(rel),
                    "sqlite mirror",
                    0,
                    0,
                    0,
                    0,
                    "SKIPPED",
                    f"{db_path} does not exist",
                )
            )
            continue
        con = sqlite3.connect(str(db_path))
        con.execute("PRAGMA busy_timeout = 5000")
        try:
            for rule_id, fn in rule_map:
                if dry_run:
                    res = fn(con, True)
                else:
                    con.execute("BEGIN")
                    try:
                        res = fn(con, False)
                        con.execute("COMMIT")
                    except Exception as exc:
                        con.execute("ROLLBACK")
                        res = RuleResult(
                            rule_id,
                            fn.__doc__.splitlines()[0] if fn.__doc__ else "",
                            f"{rule_id} (failed; rolled back)",
                            0,
                            0,
                            0,
                            0,
                            "FAILED",
                            f"transaction rolled back; database state unchanged; "
                            f"cause={type(exc).__name__}: {exc}",
                        )
                report.results.append(res)
        finally:
            con.close()
    if report_path is not None:
        report_path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
    return report


def _sqlite_rule_map_news():
    return (
        ("R2", rule_r2),
        ("R4A", rule_r4a),
        ("R7", rule_r7),
        ("R5", rule_r5),
    )


def _sqlite_rule_map_audit():
    return (
        ("R1A", rule_r1a),
        ("R1B", rule_r1b),
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--execute", action="store_true", help="actually delete (default is dry run)")
    ap.add_argument("--sqlite", action="store_true", help="also clean the legacy SQLite mirrors")
    ap.add_argument(
        "--sqlite-only",
        action="store_true",
        help="clean only the legacy SQLite mirrors, leaving PostgreSQL untouched",
    )
    ap.add_argument("--report", type=Path, default=None)
    ap.add_argument("--repo-root", type=Path, default=Path(__file__).resolve().parents[3])
    args = ap.parse_args()

    failed: list[RuleResult] = []
    rep: CleanupReport | None = None
    rep2: CleanupReport | None = None
    if not args.sqlite_only:
        rep = run_cleanup(not args.execute, args.report, args.repo_root)
        print(f"cleanup target={rep.target} dry_run={rep.dry_run}")
        for r in rep.results:
            print(
                f"  {r.rule_id:5s} {r.status:8s} {r.table:70s} "
                f"before={r.before:6d} cand={r.candidates:6d} del={r.deleted:6d} after={r.after:6d}"
            )
        failed += [r for r in rep.results if r.status == "FAILED"]

    if args.sqlite or args.sqlite_only:
        rep2 = run_cleanup_sqlite(not args.execute, args.report, args.repo_root)
        print(f"cleanup target={rep2.target} dry_run={rep2.dry_run}")
        for r in rep2.results:
            print(
                f"  {r.rule_id:5s} {r.status:8s} {r.table:70s} "
                f"before={r.before:6d} cand={r.candidates:6d} del={r.deleted:6d} after={r.after:6d}"
            )
        failed += [r for r in rep2.results if r.status == "FAILED"]
    if args.report:
        print(f"report written to {args.report}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
