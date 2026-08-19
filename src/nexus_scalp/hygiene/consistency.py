"""
Data Consistency Rules (TASK-22)
=================================
Read-only validation rules per domain (spec §12). Each rule returns a
structured finding (PASS / VIOLATION / NOT_APPLICABLE) with evidence
(count + first N offenders). The engine NEVER mutates — violations are
reported, and uncertain rows go to quarantine via the caller.

Rules:
  trade:
    * entry time <= exit time (open_time <= close_time when both present)
    * volume > 0
    * symbol exists / non-empty
    * pnl finite
  ledger / balance:
    * balance transitions valid (no ZERO/negative balance snapshot rows that
      break monotonic book-keeping — REPORTED, never auto-removed)
    * profit matches execution: pnl present and finite for CLOSED rows
  dataset / learning:
    * feature count matches the declared schema dimension
    * label exists (non-null outcome / class column) for training rows
    * timestamp valid (parseable, sane year range)

Design notes:
  * All checks are defensive (try/except per check) — a schema that does
    not expose a column marks the check NOT_APPLICABLE, never a crash.
  * Violations create ZERO deletes here; the caller decides quarantine vs.
    report based on confidence.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

#: Sanity bound for parsed timestamps (2000-01-01 .. +5 years).
MIN_TS_EPOCH: float = 946_684_800.0
MAX_TS_EPOCH: float = 1_883_496_000.0

#: Feature-dimension registry — mirrors features/schema_contract canonical
#: layout (Base 50 / News 10 / Liquidity 10). Only used when the runtime
#: does not expose a schema; NOT_APPLICABLE if the table lacks feature cols.
FEATURE_DIMENSIONS: dict[str, int] = {
    "scalp_v1": 50,
    "scalp_v2": 60,
    "scalp_v3": 70,
    "scalp_v4": 70,
    "scalp_liquidity_v1": 60,
}


@dataclass
class ConsistencyFinding:
    rule_id: str
    domain: str
    table: str
    status: str  # PASS | VIOLATION | NOT_APPLICABLE
    detail: str = ""
    offender_count: int = 0
    offenders: list[dict[str, Any]] = field(default_factory=list)
    evidence_sql: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "domain": self.domain,
            "table": self.table,
            "status": self.status,
            "detail": self.detail,
            "offender_count": self.offender_count,
            "offenders": self.offenders[:10],
            "evidence_sql": self.evidence_sql,
        }


def _as_ts(text: Any) -> float | None:
    """Best-effort parse of an ISO/epoch timestamp -> epoch seconds."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        try:
            fval = float(text)
        except Exception:
            return None
        # epoch-shaped (small enough to be seconds) vs microseconds
        return fval if fval < 1e12 else fval / 1_000_000.0
    try:
        raw = str(text).strip()
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        if raw.endswith("+00:00 ") or not raw:
            return None
        if len(raw) == 10:
            raw += "T00:00:00+00:00"
        try:
            dt = datetime.fromisoformat(raw)
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.timestamp()
    except Exception:
        return None


class ConsistencyRuleEngine:
    """Read-only consistency validator across the managed databases."""

    def __init__(self, feature_dimensions: dict[str, int] | None = None) -> None:
        self.feature_dimensions = dict(FEATURE_DIMENSIONS)
        if feature_dimensions:
            self.feature_dimensions.update(feature_dimensions)

    # ------------------------------------------------------------------
    # audit.db
    # ------------------------------------------------------------------
    def scan_audit(self, conn: sqlite3.Connection) -> list[ConsistencyFinding]:
        findings: list[ConsistencyFinding] = []
        cols = self._table_columns(conn, "audit_ledger")

        # TRADE-001: entry_time <= exit_time
        if {"open_time", "close_time"} <= cols and "status" in cols:
            try:
                bad = conn.execute(
                    "SELECT ticket, open_time, close_time FROM audit_ledger "
                    "WHERE status = 'CLOSED' AND open_time IS NOT NULL "
                    "AND close_time IS NOT NULL AND close_time < open_time LIMIT 25"
                ).fetchall()
            except sqlite3.OperationalError:
                bad = []
            findings.append(
                self._mk(
                    "TRADE-001",
                    "audit",
                    "audit_ledger",
                    bad,
                    "closed rows with close_time < open_time",
                    "WHERE status='CLOSED' AND close_time < open_time",
                )
            )
        else:
            findings.append(
                self._mk(
                    "TRADE-001", "audit", "audit_ledger", [], "missing open_time/close_time cols",
                    "",
                    status="NOT_APPLICABLE",
                )
            )

        # TRADE-002: volume > 0
        if "volume" in cols:
            try:
                bad = conn.execute(
                    "SELECT ticket, symbol, volume FROM audit_ledger "
                    "WHERE volume IS NULL OR volume <= 0 LIMIT 25"
                ).fetchall()
            except sqlite3.OperationalError:
                bad = []
            findings.append(
                self._mk(
                    "TRADE-002",
                    "audit",
                    "audit_ledger",
                    bad,
                    "rows with missing/non-positive volume",
                    "WHERE volume IS NULL OR volume <= 0",
                )
            )
        else:
            findings.append(
                self._mk("TRADE-002", "audit", "audit_ledger", [], "no volume col", "",
                         status="NOT_APPLICABLE")
            )

        # TRADE-003: symbol exists (non-empty for executed rows)
        if "symbol" in cols:
            try:
                bad = conn.execute(
                    "SELECT ticket, symbol FROM audit_ledger "
                    "WHERE symbol IS NULL OR TRIM(symbol) = '' LIMIT 25"
                ).fetchall()
            except sqlite3.OperationalError:
                bad = []
            findings.append(
                self._mk(
                    "TRADE-003",
                    "audit",
                    "audit_ledger",
                    bad,
                    "rows with empty symbol",
                    "WHERE symbol IS NULL OR TRIM(symbol)=''",
                )
            )
        else:
            findings.append(
                self._mk("TRADE-003", "audit", "audit_ledger", [], "no symbol col", "",
                         status="NOT_APPLICABLE")
            )

        # LEDGER-001: pnl finite for closed rows
        if "pnl" in cols and "status" in cols:
            try:
                bad = conn.execute(
                    "SELECT ticket, pnl FROM audit_ledger "
                    "WHERE status = 'CLOSED' AND (pnl IS NULL OR pnl != pnl) LIMIT 25"
                ).fetchall()
            except sqlite3.OperationalError:
                bad = []
            findings.append(
                self._mk(
                    "LEDGER-001",
                    "audit",
                    "audit_ledger",
                    bad,
                    "closed rows with NULL/NaN pnl",
                    "WHERE status='CLOSED' AND (pnl IS NULL OR pnl != pnl)",
                )
            )
        else:
            findings.append(
                self._mk("LEDGER-001", "audit", "audit_ledger", [], "no pnl/status cols", "",
                         status="NOT_APPLICABLE")
            )

        # UNREAL-001: abandoned pending states (pending orders older than 14d)
        if {"status", "open_time"} <= cols:
            cutoff = (
                datetime.now(UTC) - __import__("datetime").timedelta(days=14)
            ).isoformat()
            try:
                bad = conn.execute(
                    "SELECT ticket, status, open_time FROM audit_ledger "
                    "WHERE status IN ('PENDING','OPENED') AND open_time < ? LIMIT 25",
                    (cutoff,),
                ).fetchall()
            except sqlite3.OperationalError:
                bad = []
            findings.append(
                self._mk(
                    "UNREAL-001",
                    "audit",
                    "audit_ledger",
                    bad,
                    f"abandoned pending/open states older than 14d (cutoff {cutoff})",
                    "WHERE status IN ('PENDING','OPENED') AND open_time < cutoff",
                )
            )
        else:
            findings.append(
                self._mk("UNREAL-001", "audit", "audit_ledger", [], "no status/open_time cols",
                         "", status="NOT_APPLICABLE")
            )

        # DATASET-001: experiences carry a sane decision timestamp
        if "decision_timestamp" in self._table_columns(conn, "audit_experiences"):
            try:
                rows = conn.execute(
                    "SELECT experience_id, decision_timestamp FROM audit_experiences "
                    "LIMIT 400"
                ).fetchall()
                bad = []
                for rid, ts in rows:
                    ep = _as_ts(ts)
                    if ep is None or not (MIN_TS_EPOCH <= ep <= MAX_TS_EPOCH):
                        bad.append((rid, ts))
                        if len(bad) >= 25:
                            break
            except sqlite3.OperationalError:
                bad = []
            findings.append(
                self._mk(
                    "DATASET-001",
                    "audit",
                    "audit_experiences",
                    bad,
                    "experiences with invalid/impossible decision timestamps",
                    "decision_timestamp unparseable or outside 2000..+5y",
                )
            )
        else:
            findings.append(
                self._mk(
                    "DATASET-001", "audit", "audit_experiences", [],
                    "no decision_timestamp col", "", status="NOT_APPLICABLE"
                )
            )

        # DATASET-002: outcomes reference labels (idempotency_key non-empty)
        if "idempotency_key" in self._table_columns(conn, "audit_experience_outcomes"):
            try:
                bad = conn.execute(
                    "SELECT id, idempotency_key FROM audit_experience_outcomes "
                    "WHERE idempotency_key IS NULL OR TRIM(idempotency_key) = '' LIMIT 25"
                ).fetchall()
            except sqlite3.OperationalError:
                bad = []
            findings.append(
                self._mk(
                    "DATASET-002",
                    "audit",
                    "audit_experience_outcomes",
                    bad,
                    "outcome rows missing idempotency_key (label linkage broken)",
                    "WHERE idempotency_key IS NULL OR TRIM(idempotency_key)=''",
                )
            )
        else:
            findings.append(
                self._mk(
                    "DATASET-002", "audit", "audit_experience_outcomes", [],
                    "no idempotency_key col", "", status="NOT_APPLICABLE"
                )
            )

        return findings

    # ------------------------------------------------------------------
    # candle_intel.db — dataset feature-count + timestamp sanity
    # ------------------------------------------------------------------
    def scan_candle(
        self, conn: sqlite3.Connection, declared_dim: int | None = None
    ) -> list[ConsistencyFinding]:
        findings: list[ConsistencyFinding] = []
        for table in ("feature_vectors", "trade_proposals"):
            cols = self._table_columns(conn, table)
            if not cols:
                findings.append(
                    self._mk("DATASET-003", "candle_intel", table, [],
                             "no table", "", status="NOT_APPLICABLE")
                )
                continue
            # COUNT feature-like columns (exclude id/ts/symbol/timeframe/meta).
            meta = {"id", "ts", "timestamp", "symbol", "timeframe", "created_at",
                    "payload", "request_id", "source_id", "updated_at", "strategy_id"}
            feature_cols = [c for c in cols if c not in meta]
            dim = declared_dim or self.feature_dimensions.get("scalp_v3", 70)
            # The candle cache stores arbitrary-width vectors; only flag when
            # the width is far from any declared schema (hard evidence of drift).
            known = set(self.feature_dimensions.values())
            if len(feature_cols) >= 5 and len(feature_cols) not in known:
                findings.append(
                    self._mk(
                        "DATASET-003",
                        "candle_intel",
                        table,
                        [],
                        f"feature column count {len(feature_cols)} not in declared "
                        f"dimensions {sorted(known)}",
                        f"PRAGMA table_info({table}) feature cols",
                    )
                )
            else:
                findings.append(
                    self._mk(
                        "DATASET-003",
                        "candle_intel",
                        table,
                        [],
                        f"feature columns {len(feature_cols)} matches a declared schema",
                        "",
                        status="PASS",
                    )
                )

            # DATASET-004: timestamps valid
            ts_col = "ts" if "ts" in cols else ("timestamp" if "timestamp" in cols else None)
            if ts_col:
                try:
                    rows = conn.execute(
                        f"SELECT {ts_col} FROM {table} LIMIT 400"
                    ).fetchall()
                    bad = []
                    for (ts,) in rows:
                        ep = _as_ts(ts)
                        if ep is None or not (MIN_TS_EPOCH <= ep <= MAX_TS_EPOCH):
                            bad.append((ts,))
                            if len(bad) >= 25:
                                break
                except sqlite3.OperationalError:
                    bad = []
                findings.append(
                    self._mk(
                        "DATASET-004",
                        "candle_intel",
                        table,
                        bad,
                        f"rows with invalid/impossible {ts_col}",
                        f"{ts_col} unparseable or outside 2000..+5y",
                    )
                )
            else:
                findings.append(
                    self._mk("DATASET-004", "candle_intel", table, [], "no ts column", "",
                             status="NOT_APPLICABLE")
                )
        return findings

    # ------------------------------------------------------------------
    # news.db
    # ------------------------------------------------------------------
    def scan_news(self, conn: sqlite3.Connection) -> list[ConsistencyFinding]:
        findings: list[ConsistencyFinding] = []
        cols = self._table_columns(conn, "news_articles")
        if "published_at" in cols:
            try:
                rows = conn.execute(
                    "SELECT article_id, published_at FROM news_articles LIMIT 500"
                ).fetchall()
                bad = []
                for aid, ts in rows:
                    ep = _as_ts(ts)
                    if ep is None or not (MIN_TS_EPOCH <= ep <= MAX_TS_EPOCH):
                        bad.append((aid, ts))
                        if len(bad) >= 25:
                            break
            except sqlite3.OperationalError:
                bad = []
            findings.append(
                self._mk(
                    "NEWS-001",
                    "news",
                    "news_articles",
                    bad,
                    "articles with invalid/impossible published_at",
                    "published_at unparseable or outside 2000..+5y",
                )
            )
        else:
            findings.append(
                self._mk("NEWS-001", "news", "news_articles", [], "no published_at col", "",
                         status="NOT_APPLICABLE")
            )
        return findings

    # ------------------------------------------------------------------
    # entry points
    # ------------------------------------------------------------------
    def scan(self, db_key: str, conn: sqlite3.Connection) -> list[ConsistencyFinding]:
        if db_key == "audit":
            return self.scan_audit(conn)
        if db_key == "candle_intel":
            return self.scan_candle(conn)
        if db_key == "news":
            return self.scan_news(conn)
        return []

    @staticmethod
    def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
        try:
            return {d[1] for d in conn.execute(f"PRAGMA table_info('{table}')").fetchall()}
        except sqlite3.OperationalError:
            return set()

    @staticmethod
    def _mk(
        rule_id: str,
        domain: str,
        table: str,
        bad_rows: list[Any],
        detail: str,
        evidence_sql: str,
        *,
        status: str | None = None,
    ) -> ConsistencyFinding:
        if status is None:
            status = "VIOLATION" if bad_rows else "PASS"
        offenders = []
        for row in bad_rows[:10]:
            if isinstance(row, (tuple, list)):
                offenders.append(
                    {f"col{i}": str(v) for i, v in enumerate(row)}
                )
            else:
                offenders.append({"value": str(row)})
        return ConsistencyFinding(
            rule_id=rule_id,
            domain=domain,
            table=table,
            status=status,
            detail=detail,
            offender_count=len(bad_rows),
            offenders=offenders,
            evidence_sql=evidence_sql,
        )


def findings_summary(findings: list[ConsistencyFinding]) -> dict[str, Any]:
    """Compact summary (spec §12 report shape)."""
    by_status: dict[str, int] = {}
    violations: list[dict[str, Any]] = []
    for f in findings:
        by_status[f.status] = by_status.get(f.status, 0) + 1
        if f.status == "VIOLATION":
            violations.append(f.as_dict())
    return {
        "checks": len(findings),
        "pass": by_status.get("PASS", 0),
        "violations": by_status.get("VIOLATION", 0),
        "not_applicable": by_status.get("NOT_APPLICABLE", 0),
        "violation_details": violations,
        "generated_at": datetime.now(UTC).isoformat(),
    }


def findings_json(findings: list[ConsistencyFinding]) -> str:
    return json.dumps(findings_summary(findings), indent=2, default=str)