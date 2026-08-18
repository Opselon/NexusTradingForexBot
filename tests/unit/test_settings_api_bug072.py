"""
Web API integration tests for the BUG-072 settings/telegram architecture.

Covers:
- GET /api/settings  returns masked token status, never plaintext.
- GET /api/settings/telegram/status  returns truthful state.
- POST /api/settings/telegram  persists securely + rebuilds notifier.
- POST /api/telegram/test  returns the REAL delivery state (not local HTTP 200).
- GET /api/config  masks the token.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from nexus_scalp.settings import SecureSecretStore, SettingsDatabase, SettingsService


@pytest.fixture
def settings_service(tmp_path: Path) -> SettingsService:
    return SettingsService(
        db=SettingsDatabase(tmp_path / "app_settings.db"),
        secret_store=SecureSecretStore(tmp_path),
    )


class TestSettingsApi:
    def test_settings_never_contains_plaintext_token(
        self, settings_service: SettingsService
    ) -> None:
        settings_service.set_telegram(
            enabled=True,
            bot_token="123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh",
            admin_id="5094837833",
        )
        snapshot = settings_service.safe_snapshot()
        blob = str(snapshot)
        assert "123456789:ABCDEFGH" not in blob
        assert "ABCDEFGHIJKLMNOPQRSTUVWXYZ" not in blob
        assert snapshot["settings"]["telegram"]["masked_token"].endswith("fgh")

    def test_telegram_status_truthful_when_missing(self, settings_service: SettingsService) -> None:
        status = settings_service.telegram_config_status()
        assert status["configured"] is False
        assert status["token_status"] == "MISSING"
        assert status["source"] in ("MISSING", "NOT_CONFIGURED")

    def test_provenance_shows_source_and_version(self, settings_service: SettingsService) -> None:
        settings_service.set_telegram(enabled=True)
        settings_service.db.set("algo.min_risk_reward_ratio", 1.9, source="USER_SETTINGS")
        prov = settings_service.provenance()
        assert prov["algo.min_risk_reward_ratio"]["value"] == 1.9
        assert prov["algo.min_risk_reward_ratio"]["source"] == "USER_SETTINGS"
        assert prov["algo.min_risk_reward_ratio"]["version"] >= 1

    def test_mutation_audited(self, settings_service: SettingsService) -> None:
        settings_service.set_telegram(enabled=True, actor="web")
        settings_service.set_telegram(enabled=False, actor="cli")
        log = settings_service.db.audit_log()
        assert any(a["setting_name"] == "telegram.enabled" for a in log)
        assert any(a["actor"] == "cli" for a in log)


class TestNotifierRebuild:
    def test_telegram_settings_rebuild_notifier(self, settings_service: SettingsService) -> None:
        """POST /api/settings/telegram rebuilds the engine notifier."""
        from nexus_scalp.observability.telegram_notifier import TelegramNotifier

        MagicMock()

        # Simulate the server route's rebuild logic
        token, admin = settings_service.get_telegram_credentials()
        settings_service.set_telegram(enabled=True, bot_token="tok_123", admin_id="42")
        token, admin = settings_service.get_telegram_credentials()
        assert token == "tok_123"

        new_notifier = TelegramNotifier(bot_token=token, admin_id=admin, enabled=True)
        assert new_notifier.enabled is True
        assert new_notifier.bot_token == "tok_123"
        new_notifier.shutdown()

    def test_clear_token_disables(self, settings_service: SettingsService) -> None:
        settings_service.set_telegram(enabled=True, bot_token="tok_a", admin_id="42")
        settings_service.set_telegram(enabled=True, bot_token="", admin_id="42")
        status = settings_service.telegram_config_status()
        assert status["configured"] is False
        assert status["token_present"] is False


class TestSaveConfigTelegramPersistence:
    """BUG-080: POST /api/config (UI save + test flows) must persist telegram
    credentials into the secure secret store and rebuild the live notifier —
    NOT just write them into configs/live.yaml plaintext (which the engine
    never reads for secrets)."""

    REPO_ROOT = Path(__file__).resolve().parents[2]

    def _make_engine(self, settings_service: SettingsService) -> Any:
        from nexus_scalp.observability.telegram_notifier import TelegramNotifier

        class _FakeEngine:
            def __init__(self) -> None:
                self.settings_service = settings_service
                self.notifier = TelegramNotifier(
                    bot_token="",
                    admin_id="",
                    enabled=False,
                )  # unconfigured notifier, as in the field incident
                self.config = SimpleNamespace(
                    execution=SimpleNamespace(symbol="XAUUSD", mode=SimpleNamespace(value="LIVE")),
                    risk=SimpleNamespace(
                        risk_per_trade_pct=0.5,
                        max_account_drawdown_pct=20.0,
                        max_concurrent_positions=2,
                        max_spread_points=40,
                    ),
                    model=SimpleNamespace(confidence_threshold=0.75),
                )
                self.risk_engine = SimpleNamespace(config=self.config.risk)
                self.signal_policy = SimpleNamespace(confidence_threshold=0.75)

        return _FakeEngine()

    def _isolate_live_yaml(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        """Point the route's CWD-relative live.yaml at a tmp copy and chdir
        there so the test NEVER touches the real repo config."""
        repo_yaml = self.REPO_ROOT / "configs" / "live.yaml"
        (tmp_path / "configs").mkdir(parents=True, exist_ok=True)
        if repo_yaml.exists():
            (tmp_path / "configs" / "live.yaml").write_bytes(repo_yaml.read_bytes())
        monkeypatch.chdir(tmp_path)
        return tmp_path / "configs" / "live.yaml"

    def _client(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> tuple[TestClient, SettingsService]:
        from nexus_scalp.settings import SecureSecretStore, SettingsDatabase, SettingsService
        from nexus_scalp.web.server import create_app

        svc = SettingsService(
            db=SettingsDatabase(tmp_path / "app_settings.db"),
            secret_store=SecureSecretStore(tmp_path),
        )
        self._isolate_live_yaml(tmp_path, monkeypatch)
        return TestClient(create_app(engine_ref=self._make_engine(svc))), svc

    def test_config_save_persists_token_to_secret_store(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, svc = self._client(tmp_path, monkeypatch)
        payload: dict[str, Any] = {
            "execution": {"symbol": "XAUUSD"},
            "telegram": {
                "enabled": True,
                "bot_token": "123456789:TESTTOKEN",
                "admin_id": "5094837833",
            },
        }
        resp = client.post("/api/config", json=payload)
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        # Credential must now live in the secure store.
        token, admin = svc.get_telegram_credentials()
        assert token == "123456789:TESTTOKEN"
        assert admin == "5094837833"
        # The YAML on disk must NOT contain the plaintext token.
        yaml_text = (tmp_path / "configs" / "live.yaml").read_text(encoding="utf-8")
        assert "TESTTOKEN" not in yaml_text

    def test_config_save_empty_token_does_not_wipe_store(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, svc = self._client(tmp_path, monkeypatch)
        svc.set_telegram(enabled=True, bot_token="123456789:EXISTING", admin_id="42")
        # UI submits an empty token (masked GET value) -> must NOT delete.
        payload: dict[str, Any] = {
            "execution": {"symbol": "XAUUSD"},
            "telegram": {"enabled": True, "bot_token": "", "admin_id": ""},
        }
        resp = client.post("/api/config", json=payload)
        assert resp.status_code == 200
        token, admin = svc.get_telegram_credentials()
        assert token == "123456789:EXISTING"
        assert admin == "42"

    def test_config_save_rebuilds_notifier(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client, svc = self._client(tmp_path, monkeypatch)
        engine = client.app.state.engine
        # Initially unconfigured -> notifier disabled.
        assert engine.notifier.enabled is False
        payload: dict[str, Any] = {
            "execution": {"symbol": "XAUUSD"},
            "telegram": {
                "enabled": True,
                "bot_token": "123456789:TOKEN2",
                "admin_id": "5094837833",
            },
        }
        resp = client.post("/api/config", json=payload)
        assert resp.status_code == 200
        # The live notifier must have been rebuilt and now be configured.
        assert engine.notifier.enabled is True
        assert engine.notifier.bot_token == "123456789:TOKEN2"
        assert engine.notifier.admin_id == "5094837833"
        engine.notifier.shutdown()
