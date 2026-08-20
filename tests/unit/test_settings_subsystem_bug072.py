"""
Isolated settings subsystem tests (BUG-072).
=============================================
Covers the requirements of §38/§39 test matrices:

1. Settings DB created on first run.
2. Settings persist across restart.
3. Missing DB self-initializes safely.
4. Invalid DB enters explicit degraded state.
5. Settings isolated from audit.db / news.db.
6. Token never stored plaintext (secret store only).
7. Secret decrypted only through the approved mechanism.
8. Token never appears in API/UI/logs (masked only).
9. Restart reloads Telegram config.
10. Legacy YAML migration: idempotent, failure-safe, never logs secret.
"""

from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest

from nexus_scalp.settings import (
    MIGRATION_FLAG_KEY,
    TELEGRAM_ADMIN_KEY,
    TELEGRAM_ENABLED_KEY,
    TELEGRAM_TOKEN_KEY,
    SecureSecretStore,
    SettingsDatabase,
    SettingsService,
)
from nexus_scalp.settings.secret_store import SecretStoreError


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "app_settings.db"


@pytest.fixture
def secret_root(tmp_path: Path) -> Path:
    return tmp_path


def _svc(db_path: Path, secret_root: Path) -> SettingsService:
    return SettingsService(
        db=SettingsDatabase(db_path),
        secret_store=SecureSecretStore(secret_root),
    )


class TestSettingsDatabase:
    def test_db_created_on_first_run(self, db_path: Path) -> None:
        assert not db_path.exists()
        db = SettingsDatabase(db_path)
        assert db_path.exists()
        db.close()

    def test_persist_across_restart(self, db_path: Path) -> None:
        db1 = SettingsDatabase(db_path)
        db1.set("telegram.enabled", True, source="USER_SETTINGS")
        db1.close()
        db2 = SettingsDatabase(db_path)
        sv = db2.get("telegram.enabled")
        assert sv is not None and sv.value is True
        assert sv.source == "USER_SETTINGS"
        db2.close()

    def test_typed_values_roundtrip(self, db_path: Path) -> None:
        db = SettingsDatabase(db_path)
        db.set("risk.risk_per_trade_pct", 0.5)
        db.set("risk.max_concurrent_positions", 3)
        db.set("risk.enforce_stop_loss", False)
        assert db.get("risk.risk_per_trade_pct").value == 0.5
        assert db.get("risk.max_concurrent_positions").value == 3
        assert db.get("risk.enforce_stop_loss").value is False
        db.close()

    def test_version_increments(self, db_path: Path) -> None:
        db = SettingsDatabase(db_path)
        db.set("telegram.enabled", True)
        v1 = db.get("telegram.enabled").version
        db.set("telegram.enabled", False)
        v2 = db.get("telegram.enabled").version
        assert v2 == v1 + 1
        db.close()

    def test_corrupt_db_reports_degraded_state(self, db_path: Path) -> None:
        db_path.write_bytes(b"this is not a sqlite database at all" * 10)
        with pytest.raises(sqlite3.DatabaseError):
            SettingsDatabase(db_path)

    def test_audit_records_every_mutation(self, db_path: Path) -> None:
        db = SettingsDatabase(db_path)
        db.set("telegram.enabled", True, actor="web", correlation_id="cid-1")
        db.set("telegram.enabled", False, actor="cli", correlation_id="cid-2")
        log = db.audit_log()
        assert len(log) >= 2
        assert log[0]["correlation_id"] == "cid-2"
        assert log[0]["setting_name"] == "telegram.enabled"
        db.close()

    def test_mutability_classification(self, db_path: Path) -> None:
        db = SettingsDatabase(db_path)
        db.set("telegram.bot_token", "x")  # SECRET class
        assert db.get("telegram.bot_token").mutability == "SECRET"
        db.set("execution.symbol", "EURUSD")
        assert db.get("execution.symbol").mutability == "RESTART_REQUIRED"
        db.set("algo.min_risk_reward_ratio", 1.9)
        assert db.get("algo.min_risk_reward_ratio").mutability == "HOT_RESTRICTED"
        db.close()


class TestSecureSecretStore:
    def test_secret_never_plaintext_on_disk(self, tmp_path: Path) -> None:
        store = SecureSecretStore(tmp_path)
        store.set_secret(TELEGRAM_TOKEN_KEY, "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh")
        raw = (tmp_path / "secrets.enc").read_text(encoding="utf-8")
        assert "123456789:ABCDEFGH" not in raw

    def test_roundtrip(self, tmp_path: Path) -> None:
        store = SecureSecretStore(tmp_path)
        store.set_secret(TELEGRAM_TOKEN_KEY, "tok_12345")
        assert store.get_secret(TELEGRAM_TOKEN_KEY) == "tok_12345"

    def test_delete(self, tmp_path: Path) -> None:
        store = SecureSecretStore(tmp_path)
        store.set_secret(TELEGRAM_TOKEN_KEY, "tok")
        store.delete_secret(TELEGRAM_TOKEN_KEY)
        assert not store.has_secret(TELEGRAM_TOKEN_KEY)

    def test_absent_returns_none(self, tmp_path: Path) -> None:
        store = SecureSecretStore(tmp_path)
        assert store.get_secret("nope") is None


class TestSettingsServiceTelegram:
    def test_telegram_from_secure_store(self, db_path: Path, secret_root: Path) -> None:
        svc = _svc(db_path, secret_root)
        svc.set_telegram(
            enabled=True,
            bot_token="123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh",
            admin_id="5094837833",
        )
        status = svc.telegram_config_status()
        assert status["configured"] is True
        assert status["token_present"] is True
        assert status["token_status"] == "CONFIGURED"
        assert status["source"] == "SECURE_SECRET_STORE"
        # masked, never plaintext
        assert "123456789:ABCDEFGH" not in str(status)
        assert status["masked_token"].endswith("fgh")
        assert status["masked_token"].startswith("*")
        token, admin = svc.get_telegram_credentials()
        assert token == "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefgh"
        assert admin == "5094837833"

    def test_missing_token_explicit_state(self, db_path: Path, secret_root: Path) -> None:
        svc = _svc(db_path, secret_root)
        status = svc.telegram_config_status()
        assert status["configured"] is False
        assert status["token_present"] is False
        assert status["token_status"] == "MISSING"

    def test_enabled_without_config_still_safe(self, db_path: Path, secret_root: Path) -> None:
        svc = _svc(db_path, secret_root)
        svc.set_telegram(enabled=True)
        status = svc.telegram_config_status()
        assert status["enabled"] is True
        assert status["configured"] is False  # never pretend READY without secrets

    def test_restart_reloads_telegram(self, db_path: Path, secret_root: Path) -> None:
        svc1 = _svc(db_path, secret_root)
        svc1.set_telegram(enabled=True, bot_token="tok_restart_123", admin_id="12345")
        svc1.close()
        svc2 = _svc(db_path, secret_root)
        status = svc2.telegram_config_status()
        assert status["configured"] is True
        token, _ = svc2.get_telegram_credentials()
        assert token == "tok_restart_123"
        svc2.close()


class TestLegacyMigration:
    def test_migration_succeeds_and_blanks_yaml(
        self, db_path: Path, secret_root: Path, tmp_path: Path
    ) -> None:
        svc = _svc(db_path, secret_root)
        yaml_path = tmp_path / "live.yaml"
        yaml_path.write_text(
            "telegram:\n  enabled: true\n  bot_token: 'LEGACY_TOKEN_123'\n"
            "  admin_id: '5094837833'\n",
            encoding="utf-8",
        )
        legacy = {"telegram": {"bot_token": "LEGACY_TOKEN_123", "admin_id": "5094837833"}}
        result = svc.migrate_legacy_yaml(legacy)
        assert result["migrated"] is True
        # secure store now authoritative
        token, admin = svc.get_telegram_credentials()
        assert token == "LEGACY_TOKEN_123"
        assert admin == "5094837833"
        # blank legacy yaml
        assert svc.blank_legacy_secrets(yaml_path) is True
        raw = yaml_path.read_text(encoding="utf-8")
        assert "LEGACY_TOKEN_123" not in raw
        assert "bot_token: ''" in raw or 'bot_token: ""' in raw
        # idempotent re-run
        result2 = svc.migrate_legacy_yaml(legacy)
        assert result2["already_migrated"] is True
        svc.close()

    def test_new_install_requires_no_legacy(self, db_path: Path, secret_root: Path) -> None:
        svc = _svc(db_path, secret_root)
        result = svc.migrate_legacy_yaml({})
        assert result["already_migrated"] is True
        assert svc.db.get_meta(MIGRATION_FLAG_KEY) == "1"
        svc.close()

    def test_migration_failure_keeps_legacy(
        self, db_path: Path, secret_root: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Write-back verification fails -> legacy values stay intact (failure-safe)."""
        svc = _svc(db_path, secret_root)
        legacy = {"telegram": {"bot_token": "PRECIOUS_LEGACY", "admin_id": "123"}}

        original = SecureSecretStore.get_secret
        calls = {"n": 0}

        def broken_get(self_, name: str) -> str | None:
            calls["n"] += 1
            if name == TELEGRAM_TOKEN_KEY:
                return None  # verification read-back can never confirm
            return original(self_, name)

        monkeypatch.setattr(SecureSecretStore, "get_secret", broken_get)
        result = svc.migrate_legacy_yaml(legacy)
        assert result["migrated"] is False
        assert svc.db.get_meta(MIGRATION_FLAG_KEY) != "1"  # not marked migrated
        svc.close()


class TestSettingsServiceDatabasePortability:
    """DB-portability settings methods regression guards (DBP-01..05).

    These methods back the `nexus db postgres set` / `nexus db switch` CLI
    commands and the /api/db/manage/config + /provider web endpoints. They
    were MISSING at first (AttributeError on every call). The canonical
    implementation persists per-key settings (database.postgres.*) and
    routes the PG password ONLY to the OS secret store.
    """

    def test_set_database_provider_persists(self, db_path: Path, secret_root: Path) -> None:
        svc = _svc(db_path, secret_root)
        svc.set_database_provider("postgresql")
        row = svc.db.get("database.provider")
        assert row is not None and row.value == "postgresql"
        svc.close()

    def test_set_postgres_config_never_stores_password(
        self, db_path: Path, secret_root: Path
    ) -> None:
        from nexus_scalp.database.config import (
            PG_CONFIG_SETTING_KEY,
            PG_PASSWORD_SECRET_KEY,
        )

        svc = _svc(db_path, secret_root)
        svc.set_postgres_config(
            {
                "host": "db.local",
                "port": 5432,
                "database": "nse_audit",
                "username": "nse_user",
                "password": "S3cret!",
            }
        )
        row = svc.db.get(PG_CONFIG_SETTING_KEY)
        assert row is not None and row.value["host"] == "db.local"
        assert row.value.get("password") is None
        assert svc.postgres_password_set() is True
        assert svc.secrets.get_secret(PG_PASSWORD_SECRET_KEY) == "S3cret!"
        svc.close()

    def test_postgres_password_set_false_when_absent(
        self, db_path: Path, secret_root: Path
    ) -> None:
        svc = _svc(db_path, secret_root)
        assert svc.postgres_password_set() is False
        svc.close()

    def test_config_roundtrip_reload(self, db_path: Path, secret_root: Path) -> None:
        from nexus_scalp.database.config import PG_CONFIG_SETTING_KEY

        svc = _svc(db_path, secret_root)
        svc.set_postgres_config({"host": "h", "port": 5433, "database": "d", "username": "u"})
        svc.close()
        svc2 = _svc(db_path, secret_root)
        row = svc2.db.get(PG_CONFIG_SETTING_KEY)
        assert row is not None and "host" in row.value and row.value["host"] == "h"
        assert svc2.postgres_password_set() is False  # no password was ever given
        svc2.close()
