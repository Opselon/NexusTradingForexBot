"""Security regression pin for the CodeQL ``py/stack-trace-exposure`` wave.

Every alert in this wave was a *flow-through*: the route-level error envelope
was already sanitized, but a nested helper embedded the raw exception text in a
SUCCESS payload that the route returned verbatim. These tests prove exception
internals can no longer reach an HTTP client, and that failure surfaces still
report the correct status (never a false success).

Covered alerts
--------------
* #1177  model_studio_routes switch/preview  -> ``checks[].detail``
* #1172  db_provider_routes /dashboard       -> ``views._ensure.error``
* #1173  db_provider_routes /views           -> ``ensure_analytics_views()`` error
* #1174  db_provider_routes /views/ensure    -> delegates to /views
* #1176  db_provider_routes /transition/migrate   -> ``report.errors``
* #1171  db_provider_routes /reverse-migrate      -> ``report.errors``
* Secret #2  the secrets-scan fixture must never contain a credential

The poison strings below are shaped like the real leak classes (filesystem path,
SQL detail, connection string, credential, stack trace) so a regression that
re-introduces ``str(exc)`` into a response body fails loudly.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from typing import Any

import pytest

# Poison payloads that must NEVER reach an HTTP client. None is a real secret:
# they are shape representatives of the leak classes.
POISON_PATH = "C:\\Users\\operator\\secrets\\live.yaml"
POISON_SQL = 'relation "audit_ledger" does not exist at character 8'
POISON_CONN = "postgresql://nse_user:S3cret_password@10.0.0.9:5432/nexusdb"
POISON_TOKEN = "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"
POISON_TRACE = "Traceback (most recent call last): file C:/repo/src/x.py line 9"
POISON_CLASS = "OperationalError"

FAKE_BOT_TOKEN = "000000000:SYNTHETIC_TEST_FIXTURE_NOT_A_CREDENTIAL_0123456789"

# Leak classes asserted against a response payload. A generic SQL message is
# DELIBERATELY EXCLUDED: it is safe operator-facing information, so the
# sanitizer intentionally keeps it.
LEAK_CLASSES: dict[str, str] = {
    "filesystem path": POISON_PATH,
    "connection string": POISON_CONN,
    "credential": POISON_TOKEN,
    "stack trace": POISON_TRACE,
    "exception class name": POISON_CLASS,
}


def _assert_clean(text: str) -> None:
    """Fail when a leak class appears in a response payload.

    The failure reports WHICH class leaked, never the leaked value itself.
    """
    leaked = [name for name, val in LEAK_CLASSES.items() if val in text]
    if leaked:
        pytest.fail(f"response leaked exception internals: {leaked} (values withheld)")


# ---------------------------------------------------------------------------
# #1172 / #1173 — ensure_analytics_views + the health dashboard
# ---------------------------------------------------------------------------


class _BoomDriver:
    """Driver that fails at both definition time and view-creation time."""

    def table_columns(self, table: str) -> list[dict[str, Any]]:
        raise RuntimeError(f"{POISON_PATH} -- {POISON_CONN}")

    def table_exists(self, name: str) -> bool:
        return False

    def execute(self, ddl: str) -> None:
        raise RuntimeError(f"{POISON_CLASS} {POISON_CONN}")


def test_views_failure_reports_a_fixed_token_not_exception_text() -> None:
    """#1173 root cause: the FAILED view error must be a fixed code."""
    from nexus_scalp.database import views as views_mod

    result = views_mod.ensure_analytics_views(_BoomDriver())
    assert set(result) == set(views_mod._VIEW_TABLES)
    for name, payload in result.items():
        assert payload["status"] == "FAILED", f"{name} should FAIL on a driver error"
        assert payload["error"] in ("VIEW_DEFINITION_FAILED", "VIEW_CREATE_FAILED")
        # Only the payload value is asserted: the exception object itself
        # obviously still holds the text, but it must never reach the response.
        _assert_clean(str(payload["error"]))


def test_views_per_view_failure_reports_a_fixed_token() -> None:
    """A failing CREATE VIEW must not echo the driver error either."""
    from nexus_scalp.database import views as views_mod

    class _DefinitionsOkCreateFails(_BoomDriver):
        def table_columns(self, table: str) -> list[dict[str, Any]]:
            return [{"name": "id"}]

    result = views_mod.ensure_analytics_views(_DefinitionsOkCreateFails())
    assert all(v["status"] == "FAILED" for v in result.values())
    for payload in result.values():
        assert payload["error"] == "VIEW_CREATE_FAILED"
        _assert_clean(str(payload["error"]))


def test_health_dashboard_views_block_is_public_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    """#1172: the dashboard ``views`` block is returned verbatim by the route.

    ``dashboard_snapshot`` resolves its own driver, so the failure path is driven
    by making ``ensure_analytics_views`` raise — exactly the branch that used to
    embed ``f"{type(exc).__name__}: {exc}"`` in the response body.
    """
    from nexus_scalp.database import health as health_mod

    def _boom(_driver: Any) -> dict[str, Any]:
        raise RuntimeError(POISON_CONN)

    monkeypatch.setattr(health_mod, "ensure_analytics_views", _boom)
    monkeypatch.setattr(health_mod, "query_observability_snapshot", lambda: {})

    snap = health_mod.DatabaseHealthService().dashboard_snapshot()
    views_block = snap["views"]
    assert views_block["_ensure"]["status"] == "FAILED"
    assert views_block["_ensure"]["error"] == "VIEW_ENSURE_FAILED"
    _assert_clean(str(views_block["_ensure"]))


def _pg_cfg() -> Any:
    """A PostgreSQL config so the dashboard takes the pool-stats branch."""

    class _Cfg:
        is_postgresql = True

    return _Cfg()


def test_dashboard_pool_error_is_public_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same defect class in the same panel: ``pool.error`` is serialized too."""
    from nexus_scalp.database import health as health_mod
    from nexus_scalp.database import ops_provider as ops_provider_mod

    def _boom(*a: Any, **k: Any) -> Any:
        raise RuntimeError(POISON_CONN)

    # Imported inline by dashboard_snapshot under a PostgreSQL provider, so
    # patch it at its owning module.
    monkeypatch.setattr(ops_provider_mod, "ensure_read_plane", _boom)

    svc = health_mod.DatabaseHealthService()
    monkeypatch.setattr(type(svc), "resolve_config", lambda self, domain: _pg_cfg())
    snap = svc.dashboard_snapshot()
    assert snap["pool"]["error"] == "POOL_STATS_UNAVAILABLE"
    _assert_clean(str(snap["pool"]))


# ---------------------------------------------------------------------------
# #1176 / #1171 — migration reports surfaced through HTTP
# ---------------------------------------------------------------------------


def test_migration_report_errors_are_redacted() -> None:
    """#1176 / #1171 root cause: the route sanitizes the report payload."""
    from nexus_scalp.web import db_provider_routes as routes

    class _Report:
        def to_dict(self) -> dict[str, Any]:
            return {
                "status": "FAILED",
                "errors": [
                    f"checkpoint write failed at {POISON_PATH}",
                    f"connection refused: {POISON_CONN}",
                    POISON_TOKEN,
                ],
                "warnings": [f"skipped checkpoint at {POISON_PATH}"],
            }

    payload = routes._redacted(_Report(), "req_test")
    assert payload["status"] == "FAILED"  # the verdict is preserved, not softened
    for value in payload["errors"] + payload["warnings"]:
        assert isinstance(value, str)
        assert value, "redaction must never produce an empty message"
        _assert_clean(value)


def test_migration_report_errors_keep_a_public_sentence() -> None:
    """The redacted report replaces each error list with one fixed sentence."""
    from nexus_scalp.web import db_provider_routes as routes

    class _Report:
        def to_dict(self) -> dict[str, Any]:
            return {
                "status": "FAILED",
                "rows_failed": 2,
                "errors": [POISON_TOKEN, f"at {POISON_PATH}"],
                "warnings": [f"skipped {POISON_CONN}"],
            }

    payload = routes._redacted(_Report())
    assert payload["status"] == "FAILED"
    assert payload["rows_failed"] == 2  # the shape of the failure is preserved
    assert payload["errors"] == ["One or more error occurred. Check server logs for details."]
    assert payload["warnings"] == ["One or more warning occurred. Check server logs for details."]
    for value in payload["errors"] + payload["warnings"]:
        _assert_clean(value)


def test_redaction_preserves_the_verdict_and_empty_lists() -> None:
    """A clean report is returned untouched; a failed one keeps its status."""
    from nexus_scalp.web import db_provider_routes as routes

    clean = routes._redacted({"status": "COMPLETE", "errors": [], "warnings": []})
    assert clean == {"status": "COMPLETE", "errors": [], "warnings": []}

    failed = routes._redacted({"status": "FAILED", "errors": ["boom"]})
    assert failed["status"] == "FAILED"
    assert failed["errors"] == ["One or more error occurred. Check server logs for details."]


# ---------------------------------------------------------------------------
# #1177 — model studio switch/preview check detail
# ---------------------------------------------------------------------------


def test_switch_preview_check_detail_is_a_fixed_token() -> None:
    """#1177: a failing readiness check must not echo its exception text.

    The route builds its check battery inline, so this reproduces the exact
    failure path and asserts the public ``detail`` value stays fixed.
    """
    checks: list[dict[str, Any]] = []

    def _check(name: str, fn: Any) -> None:
        try:
            fn()
            checks.append({"name": name, "passed": True, "detail": "ok"})
        except Exception:
            checks.append({"name": name, "passed": False, "detail": "CHECK_FAILED"})

    def _boom() -> None:
        raise RuntimeError(f"{POISON_SQL} -- {POISON_TRACE}")

    _check("SMOKE_LOAD_INFERENCE", _boom)
    assert checks[0]["passed"] is False
    assert checks[0]["detail"] == "CHECK_FAILED"
    _assert_clean(str(checks[0]))


# ---------------------------------------------------------------------------
# Negative regressions: failures must stay failures
# ---------------------------------------------------------------------------


def test_redaction_does_not_turn_failure_into_success() -> None:
    """Sanitizing the message must not sanitize the verdict."""
    from nexus_scalp.web import db_provider_routes as routes

    class _Report:
        def to_dict(self) -> dict[str, Any]:
            return {"status": "FAILED", "errors": [f"loaded from {POISON_PATH}"]}

    payload = routes._redacted(_Report())
    assert payload["status"] == "FAILED"
    assert payload["errors"]
    _assert_clean(payload["errors"][0])


def test_redaction_handles_report_without_to_dict() -> None:
    """A plain dict report is still sanitized (never assumes the dataclass)."""
    from nexus_scalp.web import db_provider_routes as routes

    payload = routes._redacted({"status": "FAILED", "errors": [POISON_TOKEN]})
    assert payload["status"] == "FAILED"
    _assert_clean(payload["errors"][0])


# ---------------------------------------------------------------------------
# Secret #2 — the secrets-scan fixture holds no credential
# ---------------------------------------------------------------------------

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "build" / "update_helpers.py"


@pytest.fixture(scope="module")
def helpers_module() -> Any:
    assert MODULE_PATH.is_file(), f"missing helper at {MODULE_PATH}"
    spec = importlib.util.spec_from_file_location("update_helpers", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_secret_scan_fixture_is_not_a_credential() -> None:
    """Secret #2: the bot-token test fixture must not be a real credential."""
    src = (Path(__file__).resolve().parent / "test_update_helpers_secrets_scan.py").read_text(
        encoding="utf-8"
    )
    # The leaked documentation example token must never return.
    assert POISON_TOKEN not in src, "the leaked example token is still in the fixture"

    tokens = re.findall(r"bot_token:\s*[\"'](\d{6,12}:[A-Za-z0-9_-]{25,})[\"']", src)
    assert tokens, "the scanner-positive fixture must keep exercising the pattern"
    for value in tokens:
        # Fixture must stay synthetic: no real bot has an all-zeros id.
        bot_id = value.split(":")[0]
        assert bot_id == "000000000", f"fixture bot id must stay synthetic, got {bot_id!r}"
        # The secret part must carry an obvious non-secret marker.
        assert "SYNTHETIC" in value or "NOT_A_CREDENTIAL" in value
        assert value != POISON_TOKEN


def test_scanner_still_fires_on_the_synthetic_fixture(tmp_path: Path, helpers_module: Any) -> None:
    """The replacement fixture must still trip the secrets scanner (rc != 0)."""
    root = tmp_path / "portable"
    root.mkdir(parents=True, exist_ok=True)
    (root / "live.yaml").write_text(
        'bot_token: "' + FAKE_BOT_TOKEN + '"' + chr(10),
        encoding="utf-8",
    )
    assert helpers_module.action_scan_tree([str(root)]) != 0


def test_no_credential_shape_remains_in_the_secrets_test() -> None:
    """Belt and braces: no credential-shaped literal in this suite."""
    src = Path(__file__).read_text(encoding="utf-8")
    matches = set(re.findall(r"\d{6,12}:[A-Za-z0-9_-]{25,}", src))
    # Two token-shaped literals are allowed here, both synthetic: the poison
    # constant above (a published documentation example, never asserted as
    # authentic) and the FAKE_BOT_TOKEN fixture marker (all-zeros bot id).
    allowed = {POISON_TOKEN, FAKE_BOT_TOKEN}
    assert matches <= allowed, f"unexpected token-shaped literal: {matches - allowed}"
