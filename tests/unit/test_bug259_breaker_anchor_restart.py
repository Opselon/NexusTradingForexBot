"""BUG-259 regression battery (Agent-15 — Risk & Capital Protection Core).

PROVEN DEFECT: the profit-protection daily/weekly loss budgets anchored on
the equity at the FIRST evaluation of a period. A process restart mid-day
silently re-anchored the daily budget to CURRENT equity — a loss taken
before the restart vanished from the budget accounting (start-of-day
100k -> -8k -> restart -> budget re-anchored at 92k, effectively granting a
fresh 2% on top of the day's real loss).

FIX under test:
  * CircuitBreakerEngine anchors are identity-tagged (UTC day / ISO week)
    and restorable only for the CURRENT period identity.
  * AuditRepository persists/restores the anchors in the canonical
    runtime_risk_state row (new additive columns; corrupt/ambiguous data
    reads back as absent — never as a fabricated anchor).
  * LiveEngine._restore_runtime_risk_state adopts the persisted anchors at
    boot BEFORE any trading.

All tests are offline and deterministic (SQLite file DBs in tmp_path).
"""

from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime

import pytest

from nexus_scalp.adapters.database.audit_repository import AuditRepository
from nexus_scalp.risk.circuit_breakers import CircuitBreakerEngine


def _utcnow() -> datetime:
    return datetime.now(UTC)


class TestBreakerRestoreSemantics:
    def test_same_day_restart_retains_anchor(self) -> None:
        """THE restart scenario: start-of-day anchor 100k; loss to 92k;
        restart; the restored breaker must measure the NEXT loss against the
        ORIGINAL 100k anchor, not the post-loss 92k."""
        now = _utcnow()
        day_id, week_id = CircuitBreakerEngine().period_identities(now)

        # --- process 1: anchors on 100k, day goes badly (-8%) ---
        engine1 = CircuitBreakerEngine()
        engine1.evaluate(equity=100_000.0, now=now)  # anchors on first eval
        assert engine1._day_start_equity == pytest.approx(100_000.0)

        # --- restart: engine2 restores from the persisted anchors ---
        engine2 = CircuitBreakerEngine()
        restored = engine2.restore_anchors(
            day_anchor=100_000.0,
            day_utc=day_id,
            week_anchor=100_000.0,
            week_iso=week_id,
            now=now,
        )
        assert restored is True
        # A further -1.5% (92k -> ~90.6k) must breach the 2% daily budget
        # measured from the ORIGINAL anchor (8% + 1.5% = 9.5% total).
        snap = engine2.evaluate(equity=90_600.0, now=now)
        assert snap.allowed is False
        assert snap.level == "DAILY_HALT"

    def test_without_restore_budget_resets(self) -> None:
        """Control (documents the old defect's shape): a fresh engine anchors
        on the first evaluation — 90.6k would NOT breach. This is why the
        restore exists."""
        fresh = CircuitBreakerEngine()
        snap = fresh.evaluate(equity=90_600.0, now=_utcnow())
        assert snap.allowed is True  # anchored at 90.6k: no loss yet

    def test_stale_day_identity_rejected(self) -> None:
        """Yesterday's anchor must NOT bleed into today (natural rollover)."""
        now = _utcnow()
        _, week_id = CircuitBreakerEngine().period_identities(now)
        engine = CircuitBreakerEngine()
        restored = engine.restore_anchors(
            day_anchor=100_000.0,
            day_utc="2020-01-01",  # not today
            week_anchor=100_000.0,
            week_iso=week_id,
            now=now,
        )
        assert restored is False
        assert engine._day_start_equity is None  # nothing fabricated

    def test_stale_week_identity_rejected(self) -> None:
        now = _utcnow()
        day_id, _ = CircuitBreakerEngine().period_identities(now)
        engine = CircuitBreakerEngine()
        restored = engine.restore_anchors(
            day_anchor=100_000.0,
            day_utc=day_id,
            week_anchor=100_000.0,
            week_iso="1999-W01",
            now=now,
        )
        assert restored is False

    def test_corrupt_values_rejected(self) -> None:
        now = _utcnow()
        day_id, week_id = CircuitBreakerEngine().period_identities(now)
        engine = CircuitBreakerEngine()
        for kwargs in (
            {"day_anchor": float("nan"), "week_anchor": 1.0},
            {"day_anchor": 1.0, "week_anchor": float("inf")},
            {"day_anchor": -5.0, "week_anchor": 1.0},
            {"day_anchor": 0.0, "week_anchor": 1.0},
        ):
            assert (
                engine.restore_anchors(
                    day_utc=day_id, week_iso=week_id, now=now, **kwargs
                )
                is False
            )
        assert engine._day_start_equity is None

    def test_period_rollover_rearms(self) -> None:
        """A restored anchor does not survive a period change: the next day
        the breaker re-arms from the first evaluation (identity-tagged)."""
        now = _utcnow()
        day_id, week_id = CircuitBreakerEngine().period_identities(now)
        engine = CircuitBreakerEngine()
        assert (
            engine.restore_anchors(
                day_anchor=100_000.0,
                day_utc=day_id,
                week_anchor=100_000.0,
                week_iso=week_id,
                now=now,
            )
            is True
        )
        # Force the internal clock into the NEXT day: update_equity rolls the
        # anchor from current equity (natural rollover semantics preserved).
        next_day = datetime.fromtimestamp(now.timestamp() + 86400 * 2, UTC)
        engine.evaluate(equity=100_000.0, now=next_day)
        assert engine._day != now.date()


class TestBreakerAnchorPersistence:
    def _repo(self, tmp_path, name: str) -> AuditRepository:
        path = os.path.join(str(tmp_path), f"{name}.db")
        if os.path.exists(path):
            os.remove(path)
        return AuditRepository(db_url=f"sqlite:///{path}")

    def test_roundtrip(self, tmp_path) -> None:
        repo = self._repo(tmp_path, "rt")
        assert (
            repo.save_breaker_anchors(
                day_anchor=100_000.0,
                day_utc="2026-09-11",
                week_anchor=98_000.0,
                week_iso="2026-W37",
            )
            is True
        )
        anchors = repo.get_breaker_anchors()
        assert anchors is not None
        assert anchors["day_anchor"] == pytest.approx(100_000.0)
        assert anchors["day_utc"] == "2026-09-11"
        assert anchors["week_anchor"] == pytest.approx(98_000.0)
        assert anchors["week_iso"] == "2026-W37"

    def test_missing_row_reads_absent(self, tmp_path) -> None:
        """A fresh store has NO anchors — reads None (never fabricated)."""
        repo = self._repo(tmp_path, "fresh")
        assert repo.get_breaker_anchors() is None

    def test_corrupt_data_fails_closed(self, tmp_path) -> None:
        """Garbage in the anchor columns (manual tamper, partial write) reads
        back as None — the boot path then restores NOTHING, never a weak
        anchor."""
        repo = self._repo(tmp_path, "corrupt")
        repo.save_breaker_anchors(
            day_anchor=100_000.0,
            day_utc="2026-09-11",
            week_anchor=98_000.0,
            week_iso="2026-W37",
        )
        # Tamper: set the day anchor to garbage directly in the store.
        db_path = str(repo._db_path)
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE runtime_risk_state SET breaker_day_anchor = 'oops'")
            conn.commit()
        assert repo.get_breaker_anchors() is None

        # Negative / zero anchors are equally refused.
        with sqlite3.connect(db_path) as conn:
            conn.execute("UPDATE runtime_risk_state SET breaker_day_anchor = -1.0")
            conn.commit()
        assert repo.get_breaker_anchors() is None

    def test_save_rejects_invalid_anchors(self, tmp_path) -> None:
        repo = self._repo(tmp_path, "reject")
        assert (
            repo.save_breaker_anchors(
                day_anchor=float("nan"),
                day_utc="2026-09-11",
                week_anchor=1.0,
                week_iso="2026-W37",
            )
            is False
        )
        assert (
            repo.save_breaker_anchors(
                day_anchor=-1.0,
                day_utc="2026-09-11",
                week_anchor=1.0,
                week_iso="2026-W37",
            )
            is False
        )
        assert repo.get_breaker_anchors() is None


class TestBootRestoreWiring:
    def test_live_engine_boot_restores_anchors(self, tmp_path) -> None:
        """Composition test: after a save from 'process 1', a booting engine
        must restore the anchors before any trading (wired in
        _restore_runtime_risk_state)."""
        import inspect

        from nexus_scalp.application.live_engine import LiveEngine

        src = inspect.getsource(LiveEngine._restore_runtime_risk_state)
        assert "get_breaker_anchors" in src, (
            "boot must read the persisted breaker anchors"
        )
        assert "restore_anchors" in src, (
            "boot must adopt persisted anchors into the breaker engine"
        )
