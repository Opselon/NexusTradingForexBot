"""
Duplicate Detector (TASK-11)
============================
Deterministic duplicate detection using CANONICAL IDENTITIES — never
same-PnL/same-price/same-timestamp heuristics.

Identity layers per domain:

    audit:
      broker deals / trades   -> (position_id, deal ticket set, order set)
      experiences/outcomes    -> idempotency_key (UNIQUE by construction)
      ledger                  -> ticket PRIMARY KEY; family = order_id
    news:
      articles                -> article_hash (UNIQUE by construction)
      analysis                -> (article_id, run_id)
    candle_intel:
      derived rows            -> (ts, symbol, timeframe, pattern) family

CRITICAL SPLIT-FILL SAFETY (spec §8):
    Multiple broker fills from one economic order are NOT duplicates.
    Siblings sharing order_id/request_id are one canonical family and MUST
    remain linked. The detector never proposes a ledger row for deletion
    when it belongs to a multi-ticket family.

Only EXACT_DUPLICATE (confidence 1.0) may enter automatic deletion
consideration, and ONLY the duplicate row whose canonical row exists,
NOT the canonical row itself.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from nexus_scalp.hygiene import Confidence


@dataclass(frozen=True)
class DuplicateCandidate:
    database: str
    table: str
    row_id: Any
    canonical_row_id: Any
    identity_layer: str
    confidence: Confidence
    detail: str = ""


class DuplicateDetector:
    """Read-only duplicate scan. Reports candidates; never deletes."""

    def __init__(self) -> None:
        self._candidates: list[DuplicateCandidate] = []

    # ------------------------------------------------------------------
    # audit.db
    # ------------------------------------------------------------------
    def scan_audit(self, conn: sqlite3.Connection) -> list[DuplicateCandidate]:
        found: list[DuplicateCandidate] = []

        # 1) audit_experience_outcomes: idempotency_key UNIQUE enforced by DB —
        #    double rows impossible; verify no drift.
        dup_keys = conn.execute(
            "SELECT idempotency_key, COUNT(*) n FROM audit_experience_outcomes "
            "GROUP BY idempotency_key HAVING n > 1"
        ).fetchall()
        for key, _n in dup_keys:
            rows = conn.execute(
                "SELECT id FROM audit_experience_outcomes WHERE idempotency_key = ? ORDER BY id",
                (key,),
            ).fetchall()
            for rid in rows[1:]:
                found.append(
                    DuplicateCandidate(
                        database="audit",
                        table="audit_experience_outcomes",
                        row_id=rid[0],
                        canonical_row_id=rows[0][0],
                        identity_layer="idempotency_key",
                        confidence=Confidence.EXACT_DUPLICATE,
                        detail=f"duplicate outcome key {key}",
                    )
                )

        # 2) audit_ledger: ticket is PRIMARY KEY (no internal dup possible).
        #    Split-fill families (same order_id, multiple tickets) are NOT
        #    duplicates — they are one economic trade. No candidates.
        #    (This is a documented guard, not a no-op: the family check below
        #    proves we looked.)
        family_overlap = conn.execute(
            "SELECT order_id, COUNT(DISTINCT ticket) n FROM audit_ledger "
            "WHERE order_id != '' AND order_id IS NOT NULL "
            "GROUP BY order_id HAVING n > 1"
        ).fetchall()
        for order_id, n in family_overlap:
            # Families are recorded as PROTECTED, never as duplicates.
            found.append(
                DuplicateCandidate(
                    database="audit",
                    table="audit_ledger",
                    row_id=order_id,
                    canonical_row_id=order_id,
                    identity_layer="order_id_family",
                    confidence=Confidence.NOT_DUPLICATE,
                    detail=f"split-fill family order_id={order_id} tickets={n} "
                    "(PROTECTED — one economic trade, never a duplicate)",
                )
            )

        # 3) audit_broker_trades: position_id UNIQUE? check for true dup rows.
        try:
            trade_dups = conn.execute(
                "SELECT trade_id, COUNT(*) n FROM audit_broker_trades GROUP BY trade_id HAVING n > 1"
            ).fetchall()
        except sqlite3.OperationalError:
            trade_dups = []
        for tid, _n in trade_dups:
            rows = conn.execute(
                "SELECT rowid FROM audit_broker_trades WHERE trade_id = ? ORDER BY rowid",
                (tid,),
            ).fetchall()
            for rid in rows[1:]:
                found.append(
                    DuplicateCandidate(
                        database="audit",
                        table="audit_broker_trades",
                        row_id=rid[0],
                        canonical_row_id=rows[0][0],
                        identity_layer="broker_trade_id",
                        confidence=Confidence.EXACT_DUPLICATE,
                        detail=f"duplicate broker trade {tid}",
                    )
                )
        return found

    # ------------------------------------------------------------------
    # news.db
    # ------------------------------------------------------------------
    def scan_news(self, conn: sqlite3.Connection) -> list[DuplicateCandidate]:
        found: list[DuplicateCandidate] = []

        # articles: article_hash UNIQUE by construction. Scan rows flagged
        # is_duplicate=1; the canonical row must actually EXIST (join on
        # duplicate_of hash) — otherwise the flag is ambiguous -> NOT a
        # deletion candidate.
        try:
            flagged = conn.execute(
                "SELECT article_id, article_hash, duplicate_of FROM news_articles "
                "WHERE is_duplicate = 1 AND duplicate_of IS NOT NULL AND duplicate_of != ''"
            ).fetchall()
        except sqlite3.OperationalError:
            flagged = []
        for article_id, _article_hash, dup_of in flagged:
            canonical = conn.execute(
                "SELECT article_id FROM news_articles WHERE article_hash = ?",
                (dup_of,),
            ).fetchone()
            if canonical is not None and int(canonical[0]) != int(article_id):
                found.append(
                    DuplicateCandidate(
                        database="news",
                        table="news_articles",
                        row_id=article_id,
                        canonical_row_id=canonical[0],
                        identity_layer="article_hash",
                        confidence=Confidence.EXACT_DUPLICATE,
                        detail=f"is_duplicate=1 verified against canonical article_hash={dup_of}",
                    )
                )
            else:
                found.append(
                    DuplicateCandidate(
                        database="news",
                        table="news_articles",
                        row_id=article_id,
                        canonical_row_id=None,
                        identity_layer="article_hash",
                        confidence=Confidence.UNKNOWN,
                        detail=f"is_duplicate=1 but canonical row missing "
                        f"(duplicate_of={dup_of}) — NOT deletable",
                    )
                )

        # analysis: (article_id, run_id) should be unique; scan for true dups
        # on the tuples that carry BOTH columns.
        try:
            a_dups = conn.execute(
                "SELECT article_id, run_id, COUNT(*) n FROM news_analysis "
                "WHERE run_id IS NOT NULL AND run_id != '' "
                "GROUP BY article_id, run_id HAVING n > 1"
            ).fetchall()
        except sqlite3.OperationalError:
            a_dups = []
        for article_id, run_id, _n in a_dups:
            rows = conn.execute(
                "SELECT analysis_id FROM news_analysis WHERE article_id = ? "
                "AND run_id = ? ORDER BY analysis_id",
                (article_id, run_id),
            ).fetchall()
            for rid in rows[1:]:
                found.append(
                    DuplicateCandidate(
                        database="news",
                        table="news_analysis",
                        row_id=rid[0],
                        canonical_row_id=rows[0][0],
                        identity_layer="article_id+run_id",
                        confidence=Confidence.EXACT_DUPLICATE,
                        detail=f"duplicate analysis article={article_id} run={run_id}",
                    )
                )
        return found

    # ------------------------------------------------------------------
    # candle_intel.db (derived rows — same-family rows are NOT duplicates;
    # the store is append-per-bar by design)
    # ------------------------------------------------------------------
    def scan_candle(self, conn: sqlite3.Connection) -> list[DuplicateCandidate]:
        # Derived tables are rebuildable but their rows are NOT duplicates of
        # one another (each row is one bar/decision). No candidates.
        return []

    def scan(self, db_key: str, conn: sqlite3.Connection) -> list[DuplicateCandidate]:
        if db_key == "audit":
            return self.scan_audit(conn)
        if db_key == "news":
            return self.scan_news(conn)
        if db_key == "candle_intel":
            return self.scan_candle(conn)
        return []

    @property
    def candidates(self) -> list[DuplicateCandidate]:
        return list(self._candidates)


class OrphanDetector:
    """Read-only orphan scan. Classifies; never deletes."""

    def scan_audit(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []

        # outcome -> experience missing (idempotency_key has no decision).
        try:
            orphan_outcomes = conn.execute(
                "SELECT o.idempotency_key FROM audit_experience_outcomes o "
                "LEFT JOIN audit_experiences e ON e.idempotency_key = o.idempotency_key "
                "WHERE e.idempotency_key IS NULL"
            ).fetchall()
        except sqlite3.OperationalError:
            orphan_outcomes = []
        for (key,) in orphan_outcomes:
            findings.append(
                {
                    "database": "audit",
                    "table": "audit_experience_outcomes",
                    "ref_table": "audit_experiences",
                    "ref_key": key,
                    "classification": "CORRUPTION",
                    "detail": "outcome without decision snapshot",
                }
            )

        # autopsies -> ledger ticket missing.
        try:
            orphan_autopsies = conn.execute(
                "SELECT a.ticket FROM trade_autopsies a "
                "LEFT JOIN audit_ledger l ON l.ticket = a.ticket "
                "WHERE l.ticket IS NULL"
            ).fetchall()
        except sqlite3.OperationalError:
            orphan_autopsies = []
        for (ticket,) in orphan_autopsies:
            findings.append(
                {
                    "database": "audit",
                    "table": "trade_autopsies",
                    "ref_table": "audit_ledger",
                    "ref_key": ticket,
                    "classification": "RECOVERABLE",
                    "detail": "autopsy for missing ledger row (rebuildable)",
                }
            )

        # broker trades -> ledger ticket missing (broker-only close).
        try:
            orphan_trades = conn.execute(
                "SELECT b.position_id FROM audit_broker_trades b "
                "LEFT JOIN audit_ledger l ON l.ticket = b.position_id "
                "WHERE l.ticket IS NULL"
            ).fetchall()
        except sqlite3.OperationalError:
            orphan_trades = []
        for (position_id,) in orphan_trades:
            findings.append(
                {
                    "database": "audit",
                    "table": "audit_broker_trades",
                    "ref_table": "audit_ledger",
                    "ref_key": position_id,
                    "classification": "EXPECTED_ORPHAN",
                    "detail": "broker trade without ledger row "
                    "(broker-only/historical reconciliation)",
                }
            )
        return findings

    def scan_news(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        findings: list[dict[str, Any]] = []
        # analysis -> article missing
        try:
            orph = conn.execute(
                "SELECT a.analysis_id FROM news_analysis a "
                "LEFT JOIN news_articles ar ON ar.article_id = a.article_id "
                "WHERE ar.article_id IS NULL"
            ).fetchall()
        except sqlite3.OperationalError:
            orph = []
        for (aid,) in orph:
            findings.append(
                {
                    "database": "news",
                    "table": "news_analysis",
                    "ref_table": "news_articles",
                    "ref_key": aid,
                    "classification": "UNKNOWN",
                    "detail": "analysis referencing missing article (archive check needed)",
                }
            )
        return findings

    def scan_candle(self, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        # Derived mirrors may reference nothing external; empty by design.
        return []

    def scan(self, db_key: str, conn: sqlite3.Connection) -> list[dict[str, Any]]:
        if db_key == "audit":
            return self.scan_audit(conn)
        if db_key == "news":
            return self.scan_news(conn)
        if db_key == "candle_intel":
            return self.scan_candle(conn)
        return []
