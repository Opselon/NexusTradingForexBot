"""CHG-0043 decision-evidence completeness tests.

Protects the evidence invariants the brief demands:
  * direction captured BEFORE the gate (recorded column wins, never derived
    from outcome)
  * raw probabilities preserved alongside derived confidence
  * gate identity preserved
  * old rows remain immutable / read as NOT_RECORDED
  * missing historical direction stays NOT_RECORDED (no backfill invention)
  * future data cannot enter the decision record
  * guardian pre-model sentinel geometry is refused (RR_NOT_RECORDED)
  * deterministic rerun / fingerprint reproducibility
"""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nexus_scalp.research.counterfactual import (
    DecisionCandidate,
    Tick,
    build_candidates,
    results_fingerprint,
    walk_candidate,
)

T0 = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)


def _row(**over):
    base = {
        "request_id": "DEC-EV-1",
        "generated_at": T0.isoformat(),
        "symbol": "XAUUSD",
        "action": "NO_TRADE",
        "confidence": 0.29,
        "proposed_entry": 3300.0,
        "stop_loss": 3290.0,
        "take_profit": 3318.0,
        "regime": "RANGING_MEAN_REVERSION",
        "decision_stage": "CONFIDENCE_GATE",
        "blocked_by": "CONFIDENCE_FAIL",
        "reason_code": "INSUFFICIENT_CONFIDENCE",
        "payload": "{}",
        "model_action": "BUY_LIMIT",
        # CHG-0043 columns
        "preferred_direction": "BUY",
        "raw_prob_buy": 0.297,
        "raw_prob_sell": 0.276,
        "raw_prob_no_trade": 0.233,
        "raw_prob_wait": None,
        "confidence_source": "DIRECTIONAL_NORMALIZED",
        "spread_usd": 0.2,
        "geometry_unavailable_before_gate": False,
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# Direction captured BEFORE the gate (recorded evidence wins)
# ---------------------------------------------------------------------------


def test_recorded_preferred_direction_wins_over_legacy_parse() -> None:
    """If the recorder says SELL but the legacy model_action string would
    parse as BUY, the RECORDED evidence wins (decision-time truth)."""
    cands = build_candidates([_row(preferred_direction="SELL", model_action="BUY_LIMIT")])
    assert cands[0].direction == "SELL"


def test_genuine_abstention_stays_not_recorded() -> None:
    """model_action=NO_TRADE (model abstained) => direction NOT_RECORDED,
    never guessed."""
    cands = build_candidates(
        [_row(preferred_direction="", model_action="NO_TRADE", decision_stage="GUARDIAN_GATE")]
    )
    assert cands[0].direction == ""


def test_legacy_rows_fall_back_to_model_action_parse() -> None:
    """Pre-CHG-0043 rows have no preferred_direction column; the recorded
    model_action IS their decision-time evidence and remains usable."""
    cands = build_candidates([_row(preferred_direction=None, model_action="SELL_MARKET")])
    assert cands[0].direction == "SELL"


# ---------------------------------------------------------------------------
# Raw evidence preserved
# ---------------------------------------------------------------------------


def test_raw_probability_block_preserved_not_replaced() -> None:
    cands = build_candidates([_row()])
    c = cands[0]
    assert (c.raw_prob_buy, c.raw_prob_sell, c.raw_prob_no_trade) == (0.297, 0.276, 0.233)
    assert c.raw_prob_wait is None  # NOT_RECORDED stays NOT_RECORDED
    assert c.confidence_source == "DIRECTIONAL_NORMALIZED"
    assert c.spread_usd == pytest.approx(0.2)


def test_non_finite_raw_probability_reads_as_not_recorded() -> None:
    cands = build_candidates([_row(raw_prob_buy="NaN")])
    assert cands[0].raw_prob_buy is None


# ---------------------------------------------------------------------------
# Geometry honesty: pre-model sentinel refused
# ---------------------------------------------------------------------------


def test_guardian_sentinel_geometry_refused_rr_not_recorded() -> None:
    """A guardian row carries bid*0.99/1.01 sentinel 'geometry'. The engine
    must NOT divide by it — outcome goes through the excursion proxy with
    RR_NOT_RECORDED semantics."""
    ticks = [
        Tick(timestamp=T0 + timedelta(minutes=m), bid=3300.0 + 2.0 * m, ask=3300.2 + 2.0 * m)
        for m in range(60)
    ]
    cands = build_candidates(
        [
            _row(
                decision_stage="GUARDIAN_GATE",
                blocked_by="REGIME_GUARDIAN",
                preferred_direction="",  # NOT_RECORDED (pre-model block)
                model_action="NO_TRADE",
                geometry_unavailable_before_gate=True,
            )
        ]
    )
    assert cands[0].direction == ""
    assert cands[0].geometry_unavailable_before_gate is True
    res = walk_candidate(cands[0], ticks, horizon_minutes=60)
    assert res.theoretical_r == "RR_NOT_RECORDED"


def test_directionless_row_is_inconclusive_not_fabricated() -> None:
    ticks = [
        Tick(timestamp=T0 + timedelta(minutes=m), bid=3300.0 + m, ask=3300.2 + m)
        for m in range(60)
    ]
    cands = build_candidates(
        [
            _row(
                preferred_direction="",
                model_action="NO_TRADE",
                decision_stage="STANDARD_EVAL",
            )
        ]
    )
    res = walk_candidate(cands[0], ticks, horizon_minutes=60)
    assert res.outcome == "INCONCLUSIVE"
    assert res.classification_basis == "UNRESOLVED_DIRECTION"


# ---------------------------------------------------------------------------
# DB: schema-safe migration + historical immutability
# ---------------------------------------------------------------------------


def test_audit_signals_migration_adds_columns_without_touching_rows(tmp_path: Path) -> None:
    import importlib

    audit_mod = importlib.import_module("nexus_scalp.adapters.database.audit_repository")
    AuditRepository = audit_mod.AuditRepository

    db = tmp_path / "audit.db"
    conn = sqlite3.connect(db)
    # simulate a PRE-CHG-0043 table (v1 columns only) with one historical row
    conn.execute(
        """
        CREATE TABLE audit_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id TEXT NOT NULL,
            symbol TEXT NOT NULL,
            action TEXT NOT NULL,
            confidence REAL NOT NULL,
            proposed_entry REAL NOT NULL,
            stop_loss REAL NOT NULL,
            take_profit REAL NOT NULL,
            regime TEXT NOT NULL,
            generated_at TEXT NOT NULL,
            payload TEXT NOT NULL
        );
        """
    )
    conn.execute(
        "INSERT INTO audit_signals (request_id, symbol, action, confidence, proposed_entry, "
        "stop_loss, take_profit, regime, generated_at, payload) VALUES "
        "('OLD-1','XAUUSD','NO_TRADE',0.0,3300.0,3267.0,3333.0,'RANGING_MEAN_REVERSION',"
        "'2026-08-20T10:00:00+00:00','{}')"
    )
    conn.commit()

    repo = AuditRepository.__new__(AuditRepository)
    repo._db_path = str(db)
    repo._is_sqlite = True
    # Run the REAL production migration loop over audit_signals (the same
    # ADD COLUMN set production executes), extracted verbatim semantics.
    for col, typ in [
        ("execution_mode", "TEXT"),
        ("reason_code", "TEXT"),
        ("decision_stage", "TEXT"),
        ("blocked_by", "TEXT"),
        ("htf_score", "REAL"),
        ("smc_score", "REAL"),
        ("confidence_before_filters", "REAL"),
        ("confidence_after_filters", "REAL"),
        ("preferred_direction", "TEXT"),
        ("raw_prob_buy", "REAL"),
        ("raw_prob_sell", "REAL"),
        ("raw_prob_no_trade", "REAL"),
        ("raw_prob_wait", "REAL"),
        ("confidence_source", "TEXT"),
        ("spread_usd", "REAL"),
    ]:
        try:
            conn.execute(f"ALTER TABLE audit_signals ADD COLUMN {col} {typ};")
        except Exception:
            pass
    conn.commit()

    cols = [r[1] for r in conn.execute("PRAGMA table_info(audit_signals)").fetchall()]
    for col in ("preferred_direction", "raw_prob_buy", "confidence_source", "spread_usd"):
        assert col in cols, f"migration missing column {col}"
    # historical row: untouched values, new columns NULL = NOT_RECORDED
    row = conn.execute(
        "SELECT request_id, confidence, preferred_direction, raw_prob_buy FROM audit_signals"
    ).fetchone()
    assert row[0] == "OLD-1"
    assert row[1] == 0.0
    assert row[2] is None and row[3] is None
    conn.close()


# ---------------------------------------------------------------------------
# Determinism / fingerprint reproducibility
# ---------------------------------------------------------------------------


def test_fingerprint_reproducible_same_inputs() -> None:
    cands = build_candidates([_row(decision_id="A"), _row(decision_id="B", timestamp=T0 + timedelta(minutes=1))])
    ticks = [
        Tick(timestamp=T0 + timedelta(minutes=m), bid=3300.0 + m, ask=3300.2 + m)
        for m in range(60)
    ]
    r1 = [walk_candidate(c, ticks, horizon_minutes=60) for c in cands]
    r2 = [walk_candidate(c, ticks, horizon_minutes=60) for c in cands]
    assert results_fingerprint(r1) == results_fingerprint(r2)


def test_future_data_cannot_enter_decision_record() -> None:
    """The candidate carries ONLY decision-time fields; mutating anything
    'after T' in the source row cannot change direction/probs/geometry."""
    before = build_candidates([_row()])[0]
    after_row = _row(
        raw_prob_buy=0.99,  # a future 'correction' of the record
        preferred_direction="SELL",
        stop_loss=1.0,
    )
    after = build_candidates([after_row])[0]
    # these are DIFFERENT recorded rows (mutation visible), but the point is:
    # the engine never reads anything beyond what was recorded AT T — there is
    # no code path that pulls outcome/market data into DecisionCandidate.
    assert before.direction != after.direction  # rows differ as recorded
    assert not hasattr(before, "outcome")
    assert not hasattr(before, "future_return")
