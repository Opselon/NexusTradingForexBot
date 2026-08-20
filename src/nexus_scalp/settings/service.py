"""Isolated application-settings database + service (BUG-072).

A dedicated SQLite database under %LOCALAPPDATA%\\NexusScalpEngine\\databases\\
holds USER / INSTALLATION configuration only — never trading data, never the
experience/accounting history (those stay in artifacts/audit.db + news.db).

Secret values (bot token) are NEVER stored here; they live in the
OS-protected SecureSecretStore (settings/secret_store.py). This table keeps
only a `secret_reference` marker + masked tail for diagnostics.

Precedence (deterministic):
    SYSTEM DEFAULTS (code) < INSTALLATION SETTINGS (app_settings.db)
    < SAFE ENV OVERRIDES (NEXUS_TELEGRAM_*) < RUNTIME HOT SETTINGS (in-memory)

Legacy migration: telegram.bot_token / telegram.admin_id present in
configs/live.yaml are detected, validated, moved into the secure store, and
the YAML values are blanked (idempotent + restart-safe + failure-safe —
legacy values are only blanked AFTER the secure write-back verifies).
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from nexus_scalp.settings.paths import settings_db_path
from nexus_scalp.settings.secret_store import SecureSecretStore

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema / keys
# ---------------------------------------------------------------------------

#: Mutability classification for every setting (see skill.md §25).
HOT_SAFE = "HOT_SAFE"
HOT_RESTRICTED = "HOT_RESTRICTED"
RESTART_REQUIRED = "RESTART_REQUIRED"
INSTALLATION_ONLY = "INSTALLATION_ONLY"
SECRET = "SECRET"

MUTABILITY = {
    "telegram.enabled": HOT_RESTRICTED,
    "telegram.bot_token": SECRET,
    "telegram.admin_id": SECRET,
    "factory.llm_api_key": SECRET,
    "factory.llm_base_url": HOT_RESTRICTED,
    "factory.llm_model": HOT_RESTRICTED,
    "factory.llm_temperature": HOT_RESTRICTED,
    "execution.symbol": RESTART_REQUIRED,
    "execution.timeframe": RESTART_REQUIRED,
    "execution.mode": HOT_RESTRICTED,
    "execution.magic_number": RESTART_REQUIRED,
    "execution.max_slippage_points": HOT_RESTRICTED,
    "risk.max_account_drawdown_pct": HOT_RESTRICTED,
    "risk.risk_per_trade_pct": HOT_RESTRICTED,
    "risk.max_concurrent_positions": HOT_RESTRICTED,
    "risk.max_spread_points": HOT_RESTRICTED,
    "risk.max_allowed_lots": HOT_RESTRICTED,
    "risk.enforce_stop_loss": HOT_RESTRICTED,
    "model.confidence_threshold": HOT_RESTRICTED,
    "model.liquidity_features_enabled": HOT_RESTRICTED,
    "model.model_artifact_path": RESTART_REQUIRED,
    "algo.atr_sl_buffer_multiplier": HOT_RESTRICTED,
    "algo.min_risk_reward_ratio": HOT_RESTRICTED,
    "algo.ai_zone_confidence_threshold": HOT_RESTRICTED,
    "algo.fvg_mitigation_sensitivity": HOT_RESTRICTED,
    "algo.order_block_lookback_bars": HOT_RESTRICTED,
}

TELEGRAM_TOKEN_KEY = "telegram.bot_token"
TELEGRAM_ADMIN_KEY = "telegram.admin_id"
TELEGRAM_ENABLED_KEY = "telegram.enabled"
MIGRATION_FLAG_KEY = "telegram.migrated_from_yaml"

# Strategy Factory LLM provider configuration (2026-08-20).
# The API key is a SECRET and lives ONLY in the OS-protected secret store
# (DPAPI on Windows); base URL / model / temperature are non-secret runtime
# settings in the settings DB and are changeable from the web UI without
# restart (hot-swapped onto the running factory provider).
FACTORY_LLM_API_KEY = "factory.llm_api_key"
FACTORY_LLM_BASE_URL = "factory.llm_base_url"
FACTORY_LLM_MODEL = "factory.llm_model"
FACTORY_LLM_TEMPERATURE = "factory.llm_temperature"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS application_settings (
    key          TEXT PRIMARY KEY,
    value        TEXT NOT NULL,
    value_type   TEXT NOT NULL DEFAULT 'str',
    version      INTEGER NOT NULL DEFAULT 1,
    mutability   TEXT NOT NULL DEFAULT 'HOT_RESTRICTED',
    source       TEXT NOT NULL DEFAULT 'SYSTEM_DEFAULT',
    updated_at   REAL NOT NULL,
    CONSTRAINT allowed_mutability CHECK (
        mutability IN ('HOT_SAFE','HOT_RESTRICTED','RESTART_REQUIRED','INSTALLATION_ONLY','SECRET')
    )
);
CREATE TABLE IF NOT EXISTS configuration_metadata (
    key          TEXT PRIMARY KEY,
    value        TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings_audit (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    setting_name     TEXT NOT NULL,
    old_safe_value   TEXT,
    new_safe_value   TEXT,
    source           TEXT NOT NULL,
    actor            TEXT NOT NULL DEFAULT 'web',
    timestamp        REAL NOT NULL,
    validation_status TEXT NOT NULL DEFAULT 'OK',
    correlation_id   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_settings_audit_ts ON settings_audit(timestamp);
"""


@dataclass
class SettingValue:
    """Typed setting with full provenance (answer: which value + where from)."""

    key: str
    value: Any
    value_type: str = "str"
    version: int = 1
    mutability: str = HOT_RESTRICTED
    source: str = "SYSTEM_DEFAULT"
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "value_type": self.value_type,
            "version": self.version,
            "mutability": self.mutability,
            "source": self.source,
            "updated_at": self.updated_at,
        }


# ---------------------------------------------------------------------------
# State codes (§32)
# ---------------------------------------------------------------------------
STATE_OK = "OK"
STATE_SETTINGS_DB_UNAVAILABLE = "SETTINGS_DB_UNAVAILABLE"
STATE_SETTINGS_DB_CORRUPT = "SETTINGS_DB_CORRUPT"
STATE_SECRET_UNAVAILABLE = "SECRET_UNAVAILABLE"
STATE_CONFIG_INVALID = "CONFIG_INVALID"
STATE_MIGRATION_REQUIRED = "MIGRATION_REQUIRED"


@dataclass
class SettingsState:
    state: str = STATE_OK
    reason: str = ""
    db_path: str = ""


class SettingsDatabase:
    """Thin, thread-safe persistence for the isolated settings DB."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = db_path or settings_db_path()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = self._connect()
        self._init_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=10.0, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_schema(self) -> None:
        with self._lock:
            try:
                self._conn.executescript(_SCHEMA)
                self._conn.commit()
            except sqlite3.Error as exc:
                logger.error(
                    "[SETTINGS_DB] schema init failed state=%s reason=%s",
                    STATE_SETTINGS_DB_CORRUPT,
                    exc,
                )
                raise

    def health(self) -> SettingsState:
        try:
            with self._lock:
                self._conn.execute("SELECT COUNT(*) FROM application_settings").fetchone()
            return SettingsState(STATE_OK, "", str(self.db_path))
        except sqlite3.Error as exc:
            return SettingsState(STATE_SETTINGS_DB_CORRUPT, str(exc), str(self.db_path))

    # ------------------------------------------------------------- CRUD
    def _typed_value(self, raw: str, value_type: str) -> Any:
        if value_type == "bool":
            return raw.lower() in ("1", "true", "yes")
        if value_type == "int":
            return int(raw)
        if value_type == "float":
            return float(raw)
        if value_type == "json":
            return json.loads(raw)
        return raw

    def get(self, key: str) -> SettingValue | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM application_settings WHERE key = ?", (key,)
            ).fetchone()
        if not row:
            return None
        return SettingValue(
            key=row["key"],
            value=self._typed_value(row["value"], row["value_type"]),
            value_type=row["value_type"],
            version=row["version"],
            mutability=row["mutability"],
            source=row["source"],
            updated_at=row["updated_at"],
        )

    def set(
        self,
        key: str,
        value: Any,
        *,
        value_type: str | None = None,
        source: str = "USER_SETTINGS",
        actor: str = "cli",
        correlation_id: str | None = None,
        audit: bool = True,
        old_safe: str | None = None,
        new_safe: str | None = None,
    ) -> SettingValue:
        vtype = value_type or _infer_type(value)
        if vtype == "json":
            stored = json.dumps(value, ensure_ascii=False)
        elif vtype == "bool":
            stored = "1" if value else "0"
        else:
            stored = str(value)
        mutability = MUTABILITY.get(key, HOT_RESTRICTED)
        now = time.time()
        correlation_id = correlation_id or new_correlation_id()
        with self._lock:
            existing = self._conn.execute(
                "SELECT value, version FROM application_settings WHERE key = ?", (key,)
            ).fetchone()
            version = (existing["version"] + 1) if existing else 1
            self._conn.execute(
                """
                INSERT INTO application_settings
                    (key, value, value_type, version, mutability, source, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value = excluded.value,
                    value_type = excluded.value_type,
                    version = excluded.version,
                    mutability = excluded.mutability,
                    source = excluded.source,
                    updated_at = excluded.updated_at
                """,
                (key, stored, vtype, version, mutability, source, now),
            )
            if audit:
                self._conn.execute(
                    """
                    INSERT INTO settings_audit
                        (setting_name, old_safe_value, new_safe_value, source, actor,
                         timestamp, validation_status, correlation_id)
                    VALUES (?, ?, ?, ?, ?, ?, 'OK', ?)
                    """,
                    (
                        key,
                        old_safe,
                        new_safe if new_safe is not None else stored,
                        source,
                        actor,
                        now,
                        correlation_id,
                    ),
                )
            self._conn.commit()
        return SettingValue(
            key=key,
            value=value,
            value_type=vtype,
            version=version,
            mutability=mutability,
            source=source,
            updated_at=now,
        )

    def delete(self, key: str) -> bool:
        with self._lock:
            cur = self._conn.execute("DELETE FROM application_settings WHERE key = ?", (key,))
            self._conn.commit()
            return cur.rowcount > 0

    def all(self) -> dict[str, SettingValue]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM application_settings").fetchall()
        out: dict[str, SettingValue] = {}
        for row in rows:
            out[row["key"]] = SettingValue(
                key=row["key"],
                value=self._typed_value(row["value"], row["value_type"]),
                value_type=row["value_type"],
                version=row["version"],
                mutability=row["mutability"],
                source=row["source"],
                updated_at=row["updated_at"],
            )
        return out

    def audit_log(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM settings_audit ORDER BY timestamp DESC LIMIT ?", (limit,)
            ).fetchall()
        return [dict(r) for r in rows]

    def set_meta(self, key: str, value: str) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO configuration_metadata (key, value) VALUES (?, ?)
                ON CONFLICT(key) DO UPDATE SET value = excluded.value
                """,
                (key, value),
            )
            self._conn.commit()

    def get_meta(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM configuration_metadata WHERE key = ?", (key,)
            ).fetchone()
        return row["value"] if row else None

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass


def _infer_type(value: Any) -> str:
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, (dict, list)):
        return "json"
    return "str"


def new_correlation_id(prefix: str = "notif") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# Settings service
# ---------------------------------------------------------------------------


def _mask_token(token: str) -> str:
    if not token:
        return ""
    if len(token) <= 8:
        return "*" * len(token)
    return "*" * (len(token) - 4) + token[-4:]


class SettingsService:
    """One canonical settings provider (DB + secure secrets + precedence)."""

    def __init__(
        self,
        db: SettingsDatabase | None = None,
        secret_store: SecureSecretStore | None = None,
    ) -> None:
        self.db = db or SettingsDatabase()
        self.secrets = secret_store or SecureSecretStore()
        self.state = self.db.health()

    # ------------------------------------------------------------ Telegram
    def telegram_config_status(self) -> dict[str, Any]:
        """Truthful config forensics WITHOUT exposing the token."""
        token = self.secrets.get_secret(TELEGRAM_TOKEN_KEY)
        admin = self.secrets.get_secret(TELEGRAM_ADMIN_KEY) or self.db.get(TELEGRAM_ADMIN_KEY)
        enabled_row = self.db.get(TELEGRAM_ENABLED_KEY)
        enabled = enabled_row.value if enabled_row and enabled_row.value is not None else True
        source = (
            "SECURE_SECRET_STORE"
            if token
            else ("MISSING" if not self.db.get_meta(MIGRATION_FLAG_KEY) else "NOT_CONFIGURED")
        )
        return {
            "enabled": bool(enabled),
            "configured": bool(token and admin),
            "token_present": bool(token),
            "token_length": len(token) if token else 0,
            "masked_token": _mask_token(token or ""),
            "token_status": "CONFIGURED" if token else "MISSING",
            "admin_id_present": bool(admin),
            "admin_id_shape_valid": bool(re.fullmatch(r"-?\d{4,}", str(admin or ""))),
            "source": source,
            "state": self.state.state,
        }

    def get_telegram_credentials(self) -> tuple[str, str]:
        """Return (bot_token, admin_id) from the secure store / DB only."""
        token = self.secrets.get_secret(TELEGRAM_TOKEN_KEY) or ""
        admin = self.secrets.get_secret(TELEGRAM_ADMIN_KEY) or ""
        if not admin:
            row = self.db.get(TELEGRAM_ADMIN_KEY)
            admin = str(row.value) if row and row.value is not None else ""
        return token, admin

    def set_telegram(
        self,
        *,
        enabled: bool | None = None,
        bot_token: str | None = None,
        admin_id: str | None = None,
        actor: str = "web",
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        cid = correlation_id or new_correlation_id("settings")
        if bot_token is not None:
            if bot_token:
                self.secrets.set_secret(TELEGRAM_TOKEN_KEY, bot_token)
            else:
                self.secrets.delete_secret(TELEGRAM_TOKEN_KEY)
        if admin_id is not None:
            if admin_id:
                self.secrets.set_secret(TELEGRAM_ADMIN_KEY, admin_id)
            else:
                self.secrets.delete_secret(TELEGRAM_ADMIN_KEY)
        if enabled is not None:
            self.db.set(
                TELEGRAM_ENABLED_KEY,
                bool(enabled),
                source="USER_SETTINGS",
                actor=actor,
                correlation_id=cid,
                old_safe=self.db.get(TELEGRAM_ENABLED_KEY).value
                if self.db.get(TELEGRAM_ENABLED_KEY)
                else None,
                new_safe=str(enabled),
            )
        return {
            "success": True,
            "correlation_id": cid,
            "status": self.telegram_config_status(),
        }

    # ------------------------------------------------------------ Factory
    # Strategy Factory LLM provider config (2026-08-20).
    # ------------------------------------------------------------------
    def factory_llm_config_status(self) -> dict[str, Any]:
        """Truthful status WITHOUT exposing the API key."""
        key = self.secrets.get_secret(FACTORY_LLM_API_KEY)
        base = self.db.get(FACTORY_LLM_BASE_URL)
        model = self.db.get(FACTORY_LLM_MODEL)
        temp = self.db.get(FACTORY_LLM_TEMPERATURE)
        return {
            "configured": bool(key and base and model),
            "api_key_present": bool(key),
            "masked_api_key": _mask_token(key or ""),
            "base_url": str(base.value) if base else "",
            "model": str(model.value) if model else "",
            "temperature": float(temp.value) if temp and temp.value is not None else 0.7,
            "source": "SECURE_SECRET_STORE" if key else "NOT_CONFIGURED",
            "state": self.state.state,
        }

    def get_factory_llm_config(self) -> dict[str, Any]:
        """Return the full LLM runtime config (key included — used ONLY to
        build the in-process provider; never serialized to the web)."""
        return {
            "api_base_url": str(
                (
                    self.db.get(FACTORY_LLM_BASE_URL).value
                    if self.db.get(FACTORY_LLM_BASE_URL)
                    else ""
                )
                or ""
            ),
            "model": str(
                (self.db.get(FACTORY_LLM_MODEL).value if self.db.get(FACTORY_LLM_MODEL) else "")
                or ""
            ),
            "api_key": self.secrets.get_secret(FACTORY_LLM_API_KEY) or "",
            "temperature": float(
                (
                    self.db.get(FACTORY_LLM_TEMPERATURE).value
                    if self.db.get(FACTORY_LLM_TEMPERATURE)
                    else None
                )
                or 0.7
            ),
        }

    def set_factory_llm_config(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        actor: str = "web",
        correlation_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist the LLM provider config. The API key goes to the secret
        store (encrypted at rest); the rest to the settings DB. Returns the
        safe status (never the key)."""
        cid = correlation_id or new_correlation_id("factory")
        if api_key is not None:
            api_key = api_key.strip()
            if api_key:
                self.secrets.set_secret(FACTORY_LLM_API_KEY, api_key)
            else:
                self.secrets.delete_secret(FACTORY_LLM_API_KEY)
        if base_url is not None:
            self.db.set(
                FACTORY_LLM_BASE_URL,
                base_url.strip(),
                source="USER_SETTINGS",
                actor=actor,
                correlation_id=cid,
            )
        if model is not None:
            self.db.set(
                FACTORY_LLM_MODEL,
                model.strip(),
                source="USER_SETTINGS",
                actor=actor,
                correlation_id=cid,
            )
        if temperature is not None:
            bounded = max(0.0, min(float(temperature), 2.0))
            self.db.set(
                FACTORY_LLM_TEMPERATURE,
                bounded,
                value_type="float",
                source="USER_SETTINGS",
                actor=actor,
                correlation_id=cid,
            )
        return {
            "success": True,
            "correlation_id": cid,
            "status": self.factory_llm_config_status(),
        }

    # ------------------------------------------------------------ Migration
    def migrate_legacy_yaml(self, legacy: dict[str, Any]) -> dict[str, Any]:
        """Idempotent, restart-safe migration of live.yaml telegram secrets.

        Only blanks the legacy YAML AFTER the secure store write-back verifies
        (read-back succeeds). Never logs the secret.
        """
        result: dict[str, Any] = {
            "migrated": False,
            "already_migrated": False,
            "reason": "",
        }
        if self.db.get_meta(MIGRATION_FLAG_KEY) == "1":
            result["already_migrated"] = True
            return result
        legacy_tg = (legacy or {}).get("telegram") or {}
        legacy_token = str(legacy_tg.get("bot_token") or "").strip()
        legacy_admin = str(legacy_tg.get("admin_id") or "").strip()
        if not legacy_token and not legacy_admin:
            # Nothing to migrate — mark done so we don't re-scan every boot.
            self.db.set_meta(MIGRATION_FLAG_KEY, "1")
            result["already_migrated"] = True
            result["reason"] = "no_legacy_secrets"
            return result
        cid = new_correlation_id("migrate")
        if legacy_token:
            self.secrets.set_secret(TELEGRAM_TOKEN_KEY, legacy_token)
            readback = self.secrets.get_secret(TELEGRAM_TOKEN_KEY)
            if readback != legacy_token:
                self.db.set_meta("telegram.migration_failed", cid)
                result["reason"] = "write_back_verification_failed"
                return result  # legacy YAML untouched (failure-safe)
            logger.info(
                "[SETTINGS] legacy telegram.bot_token migrated to secure store (correlation_id=%s)",
                cid,
            )
        if legacy_admin:
            self.secrets.set_secret(TELEGRAM_ADMIN_KEY, legacy_admin)
        self.db.set_meta(MIGRATION_FLAG_KEY, "1")
        self.db.set_meta("telegram.migrated_at", str(time.time()))
        result["migrated"] = True
        result["correlation_id"] = cid
        return result

    def blank_legacy_secrets(self, yaml_path: Path) -> bool:
        """Remove active dependency: blank token/admin in live.yaml (post-migration)."""
        import yaml

        try:
            if not yaml_path.exists():
                return False
            raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            tg = raw.get("telegram")
            changed = False
            if isinstance(tg, dict):
                if tg.get("bot_token"):
                    tg["bot_token"] = ""
                    changed = True
                if tg.get("admin_id"):
                    tg["admin_id"] = ""
                    changed = True
            if not changed:
                return False
            tmp = yaml_path.with_suffix(".yaml.tmp")
            tmp.write_text(
                yaml.safe_dump(raw, default_flow_style=False, allow_unicode=True),
                encoding="utf-8",
            )
            tmp.replace(yaml_path)
            return True
        except Exception as exc:  # pragma: no cover - fs edge
            logger.error("[SETTINGS] legacy YAML blank failed (non-fatal): %s", exc)
            return False

    # ------------------------------------------------------------ Database portability
    def set_postgres_config(self, cfg: dict[str, Any], actor: str = "cli") -> dict[str, Any]:
        """Persist the PostgreSQL connection config (password NEVER stored here).

        The optional password is routed to the OS SecretStore; the settings DB
        holds only a non-secret JSON config with a secret-key reference.
        """
        from nexus_scalp.database.config import (
            PG_CONFIG_SETTING_KEY,
            PG_PASSWORD_SECRET_KEY,
        )

        payload: dict[str, Any] = {k: v for k, v in cfg.items() if k != "password"}
        # database/config.py:from_dict expects a provider field; without it the
        # persisted JSON config resolves as sqlite and the PG host/port are lost.
        payload.setdefault("provider", "postgresql")
        payload.setdefault("domain", "audit")
        password = str(cfg.get("password") or "")
        if password:
            self.secrets.set_secret(PG_PASSWORD_SECRET_KEY, password)
            payload["password_secret"] = PG_PASSWORD_SECRET_KEY
        safe_cfg = dict(payload)
        safe_cfg.pop("password_secret", None)
        self.db.set(
            PG_CONFIG_SETTING_KEY,
            safe_cfg,
            value_type="json",
            source="USER_SETTINGS",
            actor=actor,
        )
        return {"success": True, "persisted": True}

    def postgres_password_set(self) -> bool:
        """True when the PostgreSQL password exists in the OS secret store."""
        from nexus_scalp.database.config import PG_PASSWORD_SECRET_KEY

        return self.secrets.has_secret(PG_PASSWORD_SECRET_KEY)

    def set_database_provider(self, provider: str, actor: str = "cli") -> dict[str, Any]:
        """Persist the ACTIVE database provider (takes effect next startup)."""
        from nexus_scalp.database.config import PROVIDER_SETTING_KEY

        self.db.set(
            PROVIDER_SETTING_KEY,
            provider,
            value_type="str",
            source="USER_SETTINGS",
            actor=actor,
        )
        return {"success": True, "provider": provider, "restart_required": True}

    # ------------------------------------------------------------ Provenance
    def provenance(self) -> dict[str, Any]:
        """Answer: 'which value is active and where did it come from'."""
        telegram = self.telegram_config_status()
        out: dict[str, Any] = {"telegram": telegram}
        for key, sv in self.db.all().items():
            out[key] = {
                "value": sv.value,
                "source": sv.source,
                "version": sv.version,
                "mutability": sv.mutability,
            }
        return out

    def safe_snapshot(self) -> dict[str, Any]:
        """Never exposes secret values; always exposes masked status."""
        return {
            "state": self.state.state,
            "db_path": str(self.db.db_path),
            "settings": self.provenance(),
            "recent_audit": self.db.audit_log(limit=20),
        }

    def close(self) -> None:
        try:
            self.db.close()
        except Exception:
            pass


def load_settings_service(db_path: Path | None = None) -> SettingsService:
    """Convenience factory (shared by engine/CLI/web)."""
    return SettingsService(db=SettingsDatabase(db_path))
