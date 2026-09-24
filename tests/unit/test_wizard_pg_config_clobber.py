"""Regression tests: a repeated first run must never revert a validated
PostgreSQL install to SQLite (the provider-clobber regression).

Covers two guards in ``src/nexus_scalp/cli/wizard.py``:

* ``_postgres_config_was_validated`` — only a never-validated
  ``database.postgresql_config`` row may be cleared;
* Gate 1b in ``run_first_run_database_choice`` — a validated PostgreSQL config
  with a missing ``database.provider`` row is accepted as the answer instead of
  re-prompting (which could answer SQLITE and clear the config).
"""

from __future__ import annotations

from typing import Any

import pytest

from nexus_scalp.cli.wizard import (
    _postgres_config_was_validated,
    _persist_sqlite_choice,
    run_first_run_database_choice,
)


class _FakeRow:
    def __init__(self, value: Any) -> None:
        self.value = value


class _FakeSettingsDB:
    def __init__(self, rows: dict[str, Any] | None = None) -> None:
        self.rows: dict[str, Any] = dict(rows or {})
        self.deleted: list[str] = []

    def get(self, key: str) -> Any:
        value = self.rows.get(key)
        if value is None:
            return None
        return _FakeRow(value)

    def delete(self, key: str) -> None:
        self.deleted.append(key)
        self.rows.pop(key, None)

    def set(self, key: str, value: Any, **_: Any) -> None:
        self.rows[key] = value


class _FakeService:
    """Minimal stand-in for ``SettingsService`` used by the wizard paths."""

    def __init__(self, rows: dict[str, Any] | None = None, *, password_set: bool = True) -> None:
        self.db = _FakeSettingsDB(rows)
        self.providers: list[str] = []
        self._password_set = password_set

    def set_database_provider(self, provider: str, actor: str = "cli") -> None:
        self.providers.append(provider)
        self.db.rows["database.provider"] = provider

    def set_postgres_config(self, cfg: dict[str, Any], actor: str = "cli") -> None:
        payload = {k: v for k, v in cfg.items() if k != "password"}
        payload.setdefault("provider", "postgresql")
        payload.setdefault("domain", "audit")
        self.db.rows["database.postgresql_config"] = payload

    def postgres_password_set(self) -> bool:
        """Stands in for the OS secret store lookup."""
        return self._password_set


def _validated_pg_row() -> dict[str, Any]:
    return {
        "provider": "postgresql",
        "domain": "audit",
        "host": "localhost",
        "port": 5432,
        "database": "nexusdb",
        "username": "postgres",
        "ssl_mode": "",
        "command_timeout_sec": 30,
        "migrate_on_startup": True,
        "pooling_enabled": True,
        "connect_timeout_sec": 10,
        "sqlite_path": "",
        "sqlite_uri": "",
        "password_secret": "nexus.pg.password",
    }


# ---------------------------------------------------------------------------
# _postgres_config_was_validated
# ---------------------------------------------------------------------------


def test_validated_row_has_secret_reference() -> None:
    assert _postgres_config_was_validated(_FakeRow(_validated_pg_row()), _FakeService()) is True


def test_never_validated_row_has_no_secret_reference() -> None:
    raw = _validated_pg_row()
    raw.pop("password_secret")
    svc = _FakeService(password_set=False)
    assert _postgres_config_was_validated(_FakeRow(raw), svc) is False


def test_sqlite_row_is_not_a_validated_pg_config() -> None:
    assert _postgres_config_was_validated(_FakeRow({"provider": "sqlite"}), _FakeService()) is False


def test_malformed_row_is_treated_as_not_validated() -> None:
    # Never raises: the clearing path keeps its original behaviour.
    assert _postgres_config_was_validated(_FakeRow("not-a-dict"), _FakeService()) is False
    assert _postgres_config_was_validated(_FakeRow(None), _FakeService()) is False


def test_a_dict_row_is_accepted_directly() -> None:
    assert _postgres_config_was_validated(_validated_pg_row(), _FakeService()) is True


def test_pg_row_without_stored_password_is_not_validated() -> None:
    """A PostgreSQL row is only 'validated' when the secret store has a
    password for it (the signal that a live connection once succeeded)."""
    assert _postgres_config_was_validated(_validated_pg_row(), _FakeService(password_set=False)) is False


# ---------------------------------------------------------------------------
# _persist_sqlite_choice
# ---------------------------------------------------------------------------


def test_sqlite_choice_clears_a_never_validated_pg_row() -> None:
    raw = _validated_pg_row()
    raw.pop("password_secret")
    svc = _FakeService({"database.postgresql_config": raw}, password_set=False)
    _persist_sqlite_choice(svc)
    assert svc.db.deleted == ["database.postgresql_config"]
    assert svc.providers == ["sqlite"]


def test_sqlite_choice_preserves_a_validated_pg_row() -> None:
    """The regression: this row was validated against a live server and must
    survive an explicit later SQLite choice."""
    svc = _FakeService({"database.postgresql_config": _validated_pg_row()})
    _persist_sqlite_choice(svc)
    assert svc.db.deleted == []
    assert svc.providers == ["sqlite"]
    assert svc.db.rows["database.postgresql_config"]["host"] == "localhost"


# ---------------------------------------------------------------------------
# run_first_run_database_choice — Gate 1b
# ---------------------------------------------------------------------------


def _answered_sqlite(prompt: str, **_: Any) -> str:
    return "SQLITE"


def test_gate1b_accepts_validated_pg_config_without_prompting() -> None:
    """Provider row absent but a validated PG config present: the config wins,
    no prompt is issued, and the operator's config is not clobbered."""
    svc = _FakeService({"database.postgresql_config": _validated_pg_row()})
    out = run_first_run_database_choice(svc, prompt_fn=_answered_sqlite)
    assert out["provider"] == "postgresql"
    assert out["prompted"] is False
    assert out["reason"] == "postgres_config_present_provider_row_absent"
    assert svc.providers == ["postgresql"]
    # The config row is intact.
    assert "database.postgresql_config" in svc.db.rows


def test_gate1_still_short_circuits_when_provider_present() -> None:
    svc = _FakeService(
        {"database.provider": "postgresql", "database.postgresql_config": _validated_pg_row()}
    )
    out = run_first_run_database_choice(svc, prompt_fn=_answered_sqlite)
    assert out["reason"] == "already_configured"
    assert out["prompted"] is False


def test_gate1b_never_prompts_even_when_prompt_fn_would_say_sqlite() -> None:
    """The whole point of the gate: it must not reach the prompt loop."""
    calls: list[str] = []

    def _record(prompt: str, **_: Any) -> str:
        calls.append(prompt)
        return "SQLITE"

    svc = _FakeService({"database.postgresql_config": _validated_pg_row()})
    run_first_run_database_choice(svc, prompt_fn=_record)
    assert calls == []


def test_no_config_rows_still_prompts() -> None:
    """Sanity: with nothing configured the wizard still asks (no silent PG)."""
    svc = _FakeService()

    def _sqlite(prompt: str, **_: Any) -> str:
        return "SQLITE"

    out = run_first_run_database_choice(svc, prompt_fn=_sqlite)
    assert out["prompted"] is True
    assert out["provider"] == "sqlite"


def test_never_validated_row_does_not_trigger_gate1b() -> None:
    """A leftover, never-validated row must NOT be auto-accepted as the
    provider — the operator still chooses (and may validly clear it)."""
    raw = _validated_pg_row()
    raw.pop("password_secret")
    svc = _FakeService({"database.postgresql_config": raw}, password_set=False)

    def _sqlite(prompt: str, **_: Any) -> str:
        return "SQLITE"

    out = run_first_run_database_choice(svc, prompt_fn=_sqlite)
    assert out["prompted"] is True
    assert out["provider"] == "sqlite"
    # The never-validated row was cleared by the explicit SQLite choice.
    assert svc.db.deleted == ["database.postgresql_config"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q", "--no-header"]))
