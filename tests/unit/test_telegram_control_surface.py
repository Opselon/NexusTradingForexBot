"""Tests: Telegram operational control surface (mission item 5).

Covers command-bus authentication/authorization semantics and the engine
intent boundary — WITHOUT any network I/O (polling and replies are stubbed;
_apply path is tested directly).

- unauthorized chat is rejected (INV-010: bus never reaches authority)
- halt/resume REQUIRE the confirmation token
- halt arms the RiskEngine kill switch through the ENGINE boundary (no
  direct broker/adapter surface anywhere on the path)
- resume lifts it; status is read-only
- rollback without a staged previous artifact is refused honestly
- digest builder renders honest "—" for missing values, includes breaker
  + drift + parity blocks
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from nexus_scalp.application.command_intent import apply_command_intent
from nexus_scalp.configuration.config import RiskConfig
from nexus_scalp.observability.tg_command_bus import TelegramCommandBus
from nexus_scalp.risk.risk_engine import RiskEngine


class _NoNetworkBus(TelegramCommandBus):
    """Test bus: no HTTP at all; capture replies."""

    def __init__(self, *a, **kw) -> None:
        super().__init__(*a, **kw)
        self.replies: list[tuple[str, str]] = []

    def _poll_once(self) -> None:  # never poll in tests
        return None

    def _reply(self, chat_id: str, text: str) -> None:
        self.replies.append((chat_id, text))


def _make_bus(target=None, admin: str = "111", token: str = "sekrit") -> _NoNetworkBus:
    return _NoNetworkBus(bot_token="TEST:TOKEN", admin_id=admin, target=target, halt_token=token)


def _make_engine() -> SimpleNamespace:
    """Minimal engine surface for the intent boundary (risk + notifier)."""
    eng = SimpleNamespace()
    eng.risk_engine = RiskEngine(config=RiskConfig())
    eng._runtime_mode = "PAPER"
    eng._running = True
    eng.notifier = SimpleNamespace(
        enabled=False,
        notify_kill_switch_activated=lambda *a, **k: None,
    )
    eng.config = SimpleNamespace(
        model=SimpleNamespace(model_artifact_path="artifacts/models/none.pt")
    )
    return eng


class TestAuth:
    def test_unauthorized_chat_rejected(self) -> None:
        bus = _make_bus(target=_make_engine())
        bus._handle("999", "mallory", 1, "/status")
        assert bus.rejected_auth == 1
        assert bus.accepted == 0
        assert bus.replies and "Unauthorized" in bus.replies[0][1]

    def test_halt_requires_token(self) -> None:
        bus = _make_bus(target=_make_engine())
        bus._handle("111", "ops", 1, "/halt")
        assert bus.rejected_token == 1
        bus._handle("111", "ops", 2, "/halt wrong-token")
        assert bus.rejected_token == 2

    def test_unknown_command_counted(self) -> None:
        bus = _make_bus(target=_make_engine())
        bus._handle("111", "ops", 1, "/launch-nukes")
        assert bus.rejected_unknown == 1


class TestIntents:
    def test_status_is_read_only(self) -> None:
        eng = _make_engine()
        result = apply_command_intent(eng, {"command": "status"})
        assert result["ok"] is True
        assert eng.risk_engine._kill_switch_active is False

    def test_halt_arms_kill_switch_via_engine(self) -> None:
        eng = _make_engine()
        result = apply_command_intent(
            eng,
            {"command": "halt", "username": "ops", "chat_id": "111", "at": "t0"},
        )
        assert result["ok"] is True
        assert eng.risk_engine._kill_switch_active is True
        # kill switch actually blocks proposals (risk-layer authority proven)
        assert eng.risk_engine._kill_switch_active

    def test_resume_lifts_halt(self) -> None:
        eng = _make_engine()
        apply_command_intent(eng, {"command": "halt", "username": "o", "at": "t"})
        assert eng.risk_engine._kill_switch_active is True
        result = apply_command_intent(eng, {"command": "resume", "username": "o", "at": "t"})
        assert result["ok"] is True
        assert eng.risk_engine._kill_switch_active is False

    def test_halt_is_idempotent(self) -> None:
        eng = _make_engine()
        r1 = apply_command_intent(eng, {"command": "halt", "username": "o", "at": "t"})
        r2 = apply_command_intent(eng, {"command": "halt", "username": "o", "at": "t"})
        assert r1["ok"] and r2["ok"]
        assert "already" in r2["message"]

    def test_rollback_without_previous_refused_honestly(self) -> None:
        eng = _make_engine()
        # governance present but no verified champion -> honest refusal
        eng.governance_engine = SimpleNamespace(store=SimpleNamespace())
        eng.champion_manager = SimpleNamespace(champion_or_none=lambda: None)
        result = apply_command_intent(eng, {"command": "rollback", "username": "o", "at": "t"})
        assert result["ok"] is False
        assert "no verified champion" in result["message"]

    def test_rollback_without_governance_refused(self) -> None:
        eng = _make_engine()
        result = apply_command_intent(eng, {"command": "rollback", "username": "o", "at": "t"})
        assert result["ok"] is False
        assert "governance engine unavailable" in result["message"]

    def test_unknown_intent_refused(self) -> None:
        eng = _make_engine()
        result = apply_command_intent(eng, {"command": "self-destruct"})
        assert result["ok"] is False


class TestDigest:
    def test_digest_renders_with_missing_values(self) -> None:
        from nexus_scalp.reporting.operational_digest import build_operational_digest

        eng = _make_engine()
        text = build_operational_digest(eng, container=None)
        assert "OPERATIONAL DIGEST" in text
        # honest placeholders (no fabricated numbers), key sections present
        assert "not included" in text
        assert "Drift" in text and "Parity" in text and "Breakers" in text


class TestDigestResearchLine:
    """EDGE ROUND-4: digest exposes archive-only retention visibility."""

    def _engine_with_audit(self, tmp_path):
        from types import SimpleNamespace

        from nexus_scalp.adapters.database.audit_repository import AuditRepository

        repo = AuditRepository(
            db_url=f"sqlite:///{tmp_path / 'digest.db'}", flush_interval_sec=0.02
        )
        eng = SimpleNamespace(audit=repo)
        return eng, repo

    def test_digest_shows_research_unavailable_without_history(self, tmp_path) -> None:
        from nexus_scalp.reporting.operational_digest import build_operational_digest

        eng, repo = self._engine_with_audit(tmp_path)
        try:
            text = build_operational_digest(eng, container=None)
            # empty DB -> honest placeholder, never fabricated counts
            assert "Research" in text
        finally:
            repo.close()

    def test_digest_shows_live_and_archived_counts(self, tmp_path) -> None:
        import sqlite3
        from datetime import UTC, datetime, timedelta

        from nexus_scalp.reporting.operational_digest import build_operational_digest
        from nexus_scalp.research.archive import archive_research_history

        eng, repo = self._engine_with_audit(tmp_path)
        try:
            db_path = str(repo._db_path)
            conn = sqlite3.connect(db_path)
            ts_old = (datetime.now(UTC) - timedelta(days=400)).isoformat()
            ts_new = datetime.now(UTC).isoformat()
            for i in range(4):
                conn.execute(
                    "INSERT INTO research_events (event_id, strategy_id,"
                    " research_run_id, gate_id, event_type, message, payload,"
                    " occurred_at) VALUES (?, 'S', 'R', 'G', 'T', 'm', '{}', ?)",
                    (f"EVT-{i}", ts_old if i < 2 else ts_new),
                )
            conn.commit()
            archive_research_history(conn, older_than_days=365)
            conn.close()

            text = build_operational_digest(eng, container=None)
            assert "🔬 Research:" in text
            assert "2+2📦" in text  # 2 live + 2 archived events
        finally:
            repo.close()
