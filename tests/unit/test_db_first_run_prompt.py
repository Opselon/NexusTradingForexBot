"""First-run DATABASE PROVIDER CHOICE — the dual-entry prompt contract.

WHERE/WHY: user-reported defect "NSE never asked me PostgreSQL-or-SQLite (nor
for connection details)" while app_settings.db silently carried
database.provider=postgresql and sent the runtime at a dead server. The shared
step under test lives in cli/wizard.py::run_first_run_database_choice and is
wired into all three entry surfaces: `nexus setup` (_wizard_flow),
`nexus start` (engine_boot.start_cmd) and the double-click launcher
(NexusTradingForexBot.prompt_first_run_database_choice).

Contracts verified here:
  * first run (no database.provider row) → the provider IS asked;
  * already-configured install → ZERO new prompts (idempotency);
  * non-interactive session → deferred, never blocks a boot;
  * PostgreSQL details are validated BEFORE persisting; a failure is
    categorized (UNREACHABLE / AUTHENTICATION FAILED / DATABASE NOT FOUND) and
    persists NOTHING — retry or EXPLICIT SQLite only, never an automatic
    fallback;
  * the password reaches ONLY the OS secret store: prompted with hide_input,
    absent from every settings row and from stdout;
  * wiring present on all three entry surfaces.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from nexus_scalp.cli import engine_boot, wizard
from nexus_scalp.database.config import (
    PG_CONFIG_SETTING_KEY,
    PG_PASSWORD_SECRET_KEY,
    PROVIDER_SETTING_KEY,
    DatabaseConfig,
)
from nexus_scalp.settings.secret_store import SecureSecretStore
from nexus_scalp.settings.service import SettingsDatabase, SettingsService

PW = "Sup3r-Secret-PW"


class ScriptedPrompt:
    """Deterministic stand-in for typer.prompt: replays scripted answers."""

    def __init__(self, answers: list) -> None:
        self.answers = list(answers)
        self.calls: list[dict] = []

    def __call__(self, text: str, default=None, hide_input: bool = False):
        self.calls.append({"text": text, "default": default, "hide_input": hide_input})
        if not self.answers:
            raise AssertionError(f"unexpected prompt: {text!r}")
        ans = self.answers.pop(0)
        if isinstance(ans, BaseException):
            raise ans
        if ans is None:
            ans = default if default is not None else ""
        return ans

    def provider_calls(self) -> list[dict]:
        return [c for c in self.calls if c["text"] == wizard.PROVIDER_PROMPT_LABEL]


class BombPrompt:
    """Records every call; the gate must prevent any prompt from firing."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, text: str, default=None, hide_input: bool = False):
        self.calls.append({"text": text, "default": default, "hide_input": hide_input})
        return default if default is not None else ""


def _svc(tmp_path: Path) -> SettingsService:
    """Isolated settings DB + secret store (never the real user data)."""
    return SettingsService(
        db=SettingsDatabase(db_path=Path(tmp_path) / "app_settings.db"),
        secret_store=SecureSecretStore(root=Path(tmp_path) / "secrets"),
    )


def test_sqlite_first_run_prompts_once_then_gated(tmp_path):
    svc = _svc(tmp_path)
    first_prompt = ScriptedPrompt(["SQLITE"])
    first = wizard.run_first_run_database_choice(svc, prompt_fn=first_prompt)

    assert first["prompted"] is True
    assert first["provider"] == "sqlite"
    assert first["persisted"] is True
    assert len(first_prompt.provider_calls()) == 1
    assert svc.db.get(PROVIDER_SETTING_KEY).value == "sqlite"

    # Second run: provider already persisted → ZERO prompts.
    bomb = BombPrompt()
    second = wizard.run_first_run_database_choice(svc, prompt_fn=bomb)
    assert second["prompted"] is False
    assert second["reason"] == "already_configured"
    assert second["provider"] == "sqlite"
    assert bomb.calls == []


def test_already_postgresql_install_never_prompts(tmp_path):
    svc = _svc(tmp_path)
    svc.db.set(PROVIDER_SETTING_KEY, "postgresql", value_type="str")
    bomb = BombPrompt()

    res = wizard.run_first_run_database_choice(svc, prompt_fn=bomb)

    assert res["prompted"] is False
    assert res["reason"] == "already_configured"
    assert res["provider"] == "postgresql"
    assert bomb.calls == []


def test_non_interactive_session_never_blocks(tmp_path, monkeypatch):
    svc = _svc(tmp_path)
    # No injected prompt_fn and no TTY (CI / --json / piped stdin).
    monkeypatch.setattr(wizard, "sys", SimpleNamespace(stdin=SimpleNamespace(isatty=lambda: False)))

    res = wizard.run_first_run_database_choice(svc)

    assert res["prompted"] is False
    assert res["reason"] == "non_interactive"
    assert svc.db.get(PROVIDER_SETTING_KEY) is None


def test_unknown_provider_answer_reprompts(tmp_path, capsys):
    svc = _svc(tmp_path)
    prompt = ScriptedPrompt(["mysql", "sqlite"])

    res = wizard.run_first_run_database_choice(svc, prompt_fn=prompt)

    assert res["provider"] == "sqlite" and res["persisted"] is True
    assert len(prompt.provider_calls()) == 2
    assert "Please answer SQLITE or POSTGRESQL" in capsys.readouterr().out


def test_cancelled_prompt_persists_nothing(tmp_path):
    svc = _svc(tmp_path)
    prompt = ScriptedPrompt([EOFError()])

    res = wizard.run_first_run_database_choice(svc, prompt_fn=prompt)

    assert res["prompted"] is True
    assert res["reason"] == "cancelled"
    assert res["persisted"] is False
    assert svc.db.get(PROVIDER_SETTING_KEY) is None


def test_postgresql_success_persists_password_only_in_secret_store(tmp_path, monkeypatch, capsys):
    svc = _svc(tmp_path)

    def fake_validate(cfg, password, *, timeout=8):
        assert cfg.host == "dbhost" and cfg.port == 5433
        return True, "", "postgresql://nse_user:***@dbhost:5433/nse_audit"

    monkeypatch.setattr(wizard, "_validate_postgres_connection", fake_validate)
    prompt = ScriptedPrompt(
        ["POSTGRES", "dbhost", "5433", "nse_audit", "nse_user", PW],
    )

    res = wizard.run_first_run_database_choice(svc, prompt_fn=prompt)

    assert res["prompted"] and res["validated"] and res["persisted"]
    assert res["provider"] == "postgresql"
    assert svc.db.get(PROVIDER_SETTING_KEY).value == "postgresql"

    # The password prompt was hidden.
    pw_calls = [c for c in prompt.calls if "password" in c["text"].lower()]
    assert pw_calls and all(c["hide_input"] for c in pw_calls)

    # Settings rows (current values + audit trail) NEVER carry the password.
    rows = json.dumps({k: sv.value for k, sv in svc.db.all().items()}, default=str)
    audit = json.dumps(svc.db.audit_log(limit=50), default=str)
    assert PW not in rows
    assert PW not in audit
    pg_row = svc.db.get(PG_CONFIG_SETTING_KEY)
    assert pg_row is not None and pg_row.value["host"] == "dbhost"

    # The password lives ONLY in the (isolated) OS secret store.
    assert svc.secrets.get_secret(PG_PASSWORD_SECRET_KEY) == PW

    # Never echoed to stdout.
    assert PW not in capsys.readouterr().out


def test_failed_validation_persists_nothing_and_requires_explicit_sqlite(
    tmp_path, monkeypatch, capsys
):
    svc = _svc(tmp_path)
    # A stale, never-validated row from an earlier wave: an explicit SQLite
    # choice must not be hijacked by it at runtime.
    svc.db.set(
        PG_CONFIG_SETTING_KEY,
        {"provider": "postgresql", "host": "dead.example", "port": 5432},
        value_type="json",
    )
    calls = {"n": 0}

    def fake_validate(cfg, password, *, timeout=8):
        calls["n"] += 1
        return (
            False,
            "UNREACHABLE",
            'connection to server at "127.0.0.1", port 5432 failed: Connection refused',
        )

    monkeypatch.setattr(wizard, "_validate_postgres_connection", fake_validate)
    prompt = ScriptedPrompt(
        ["POSTGRESQL", "127.0.0.1", "5432", "nse_audit", "nse_user", PW, "S"],
    )

    res = wizard.run_first_run_database_choice(svc, prompt_fn=prompt)

    assert calls["n"] == 1
    assert res["validated"] is False
    assert res["category"] == "UNREACHABLE"
    assert res["provider"] == "sqlite"
    assert res["reason"] == "explicit_sqlite_after_validation_failure"

    # Nothing broken persisted: no postgresql provider, no config row, no secret.
    assert svc.db.get(PROVIDER_SETTING_KEY).value == "sqlite"
    assert svc.db.get(PG_CONFIG_SETTING_KEY) is None
    assert not svc.secrets.has_secret(PG_PASSWORD_SECRET_KEY)

    out = capsys.readouterr().out
    assert PW not in out  # password never echoed, even on failure
    assert "UNREACHABLE" in out  # categorized error IS shown


def test_validation_auth_failure_then_retry_succeeds(tmp_path, monkeypatch, capsys):
    svc = _svc(tmp_path)
    outcomes = [
        (
            False,
            "AUTHENTICATION FAILED",
            'password authentication failed for user "nse_user"',
        ),
        (True, "", "postgresql://nse_user:***@localhost:5432/nse_audit"),
    ]
    seen: list[str] = []

    def fake_validate(cfg, password, *, timeout=8):
        seen.append(password)
        return outcomes.pop(0)

    monkeypatch.setattr(wizard, "_validate_postgres_connection", fake_validate)
    prompt = ScriptedPrompt(
        [
            "POSTGRES",
            "localhost",
            "5432",
            "nse_audit",
            "nse_user",
            "pw-first-try",
            "R",  # explicit retry
            "localhost",
            "5432",
            "nse_audit",
            "nse_user",
            PW,
        ],
    )

    res = wizard.run_first_run_database_choice(svc, prompt_fn=prompt)

    assert seen == ["pw-first-try", PW]
    assert res["validated"] is True and res["persisted"] is True
    assert res["provider"] == "postgresql"
    assert svc.secrets.get_secret(PG_PASSWORD_SECRET_KEY) == PW

    out = capsys.readouterr().out
    assert "AUTHENTICATION FAILED" in out
    assert "pw-first-try" not in out
    assert PW not in out


class _PgError(Exception):
    def __init__(self, msg: str, sqlstate: str = "") -> None:
        super().__init__(msg)
        self.sqlstate = sqlstate


def test_error_categorization_three_buckets():
    cat, _ = wizard._categorize_postgres_error(
        _PgError('password authentication failed for user "nse_user"', "28P01")
    )
    assert cat == "AUTHENTICATION FAILED"

    cat, _ = wizard._categorize_postgres_error(
        _PgError('database "nse_audit" does not exist', "3D000")
    )
    assert cat == "DATABASE NOT FOUND"

    cat, _ = wizard._categorize_postgres_error(
        ConnectionRefusedError(
            "[WinError 10061] No connection could be made because the target "
            "machine actively refused it"
        )
    )
    assert cat == "UNREACHABLE"

    cat, _ = wizard._categorize_postgres_error(OSError("getaddrinfo failed"))
    assert cat == "UNREACHABLE"


def test_real_validation_dead_endpoint_restores_secret_store(tmp_path, monkeypatch):
    """REAL driver path (psycopg): dead endpoint → UNREACHABLE, store restored.

    Both SecureSecretStore bindings are redirected to a temp root, so the
    machine's real secret store is never touched by this test.
    """
    from nexus_scalp.database import config as dbconfig
    from nexus_scalp.settings import secret_store as ss

    def _mk(*_args, **_kwargs):
        return SecureSecretStore(root=Path(tmp_path) / "secrets")

    monkeypatch.setattr(ss, "SecureSecretStore", _mk)
    monkeypatch.setattr(dbconfig, "SecureSecretStore", _mk)

    store = SecureSecretStore(root=Path(tmp_path) / "secrets")
    store.set_secret(PG_PASSWORD_SECRET_KEY, "previous-pw")

    cfg = DatabaseConfig.for_postgres(
        host="127.0.0.1", port=1, database="nse_audit", username="nse_user"
    )
    ok, category, detail = wizard._validate_postgres_connection(cfg, "typed-pw", timeout=2)

    assert ok is False
    assert category == "UNREACHABLE"
    assert "typed-pw" not in detail
    # Failed attempt left the store exactly as it was found.
    assert store.get_secret(PG_PASSWORD_SECRET_KEY) == "previous-pw"


def test_all_three_entry_surfaces_wire_the_step():
    # `nexus setup` wizard flow (cli/wizard.py::_wizard_flow)
    assert "run_first_run_database_choice" in wizard._wizard_flow.__code__.co_names
    # `nexus start` (cli/engine_boot.py::start_cmd)
    assert "run_first_run_database_choice" in engine_boot.start_cmd.__code__.co_names
    # double-click launcher (NexusTradingForexBot.py::main)
    repo_root = Path(__file__).resolve().parents[2]
    launcher_src = (repo_root / "NexusTradingForexBot.py").read_text(encoding="utf-8")
    assert "prompt_first_run_database_choice" in launcher_src
    assert launcher_src.count("prompt_first_run_database_choice()") >= 1
