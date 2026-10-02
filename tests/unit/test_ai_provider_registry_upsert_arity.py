"""Regression: the registry upsert must bind parameters on both backends.

INCIDENT (Wave 8): configuring an AI provider while the persisted application
database provider is PostgreSQL crashed with::

    psycopg.ProgrammingError: the query has 0 placeholders but 4 parameters were passed

at ``ai_providers/registry.py`` (config upsert). Root cause: the store authored
qmark (``?``) SQL and the PostgreSQL driver's upsert-verb translator only
recognised ``INSERT OR REPLACE`` -- ``INSERT INTO ... ON CONFLICT`` fell
through untranslated, so the executed statement still held literal ``?``
markers (0 placeholders) while 4 parameters were supplied.

The fix moved statement generation into the driver: ``execute_upsert`` rebuilds
the dialect-correct INSERT from the same validated column list that produced the
parameter tuple, so the placeholder count and the parameter count cannot
diverge. The store's own SQL string now only names table + columns + conflict
key; it is never the text that reaches the server.

This suite pins that contract on SQLite and on a REAL PostgreSQL connection
(not a mocked psycopg): insert, update, idempotent repeat, reload, activation,
the query/parameter arity itself, and the API route through the orchestrator.
"""

from __future__ import annotations

import contextlib
import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

try:
    import psycopg
except ImportError:  # pragma: no cover - the [postgres] extra is optional in CI
    psycopg = None  # type: ignore[assignment]

from nexus_scalp.settings.secret_store import SecureSecretStore

# ---------------------------------------------------------------------------
# PostgreSQL arm -- reuse the suite's established convention
# ---------------------------------------------------------------------------

PG_URL = os.environ.get("NSE_PG_TEST_URL", "")


def _resolve_admin_dsn() -> str:
    """Instance URL with the database segment pinned to ``postgres``.

    Mirrors ``tests/unit/test_sqlite_runtime_trap.py``: ``NSE_PG_TEST_URL`` in
    the CI convention, falling back to the box's persisted PostgreSQL config.
    libpq defaults a missing dbname to the USER name; pinning avoids landing on
    a database named after the role.
    """
    if PG_URL:
        try:
            from psycopg.conninfo import conninfo_to_dict

            parts = conninfo_to_dict(PG_URL)
        except Exception:  # pragma: no cover - unparseable, not ours to fix
            return ""
        if parts.get("password"):
            _seed_secret(str(parts["password"]))
        host = parts.get("host", "localhost")
        port = parts.get("port", "5432")
        user = parts.get("user", "postgres")
        pw = parts.get("password", "")
        auth = f"{user}:{pw}@" if pw else f"{user}@"
        dbname = parts.get("dbname") or parts.get("database") or "postgres"
        return f"postgresql://{auth}{host}:{port}/{dbname}"
    from nexus_scalp.database.config import build_postgres_url, load_database_config

    try:
        cfg = load_database_config("audit")
        if cfg.is_postgresql:
            return f"{build_postgres_url(cfg, SecureSecretStore()).rsplit('/', 1)[0]}/postgres"
    except Exception:
        pass
    return ""


def _seed_secret(password: str) -> None:
    """Carry the instance credential into the conftest-isolated secret store.

    ``build_postgres_url`` reads ``db.postgresql.password`` from the store, so
    a ``NSE_PG_TEST_URL`` carrying the password must seed it before the store
    resolves its DSN. This helper never logs the credential — the import guard
    above is the only psycopg dependency (optional).
    """
    if not password or psycopg is None:  # pragma: no cover - guarded by needs_postgres
        return
    try:
        if not SecureSecretStore().has_secret("db.postgresql.password"):
            SecureSecretStore().set_secret("db.postgresql.password", password)
    except Exception:  # pragma: no cover - a locked store degrades, not crashes
        pass


ADMIN_DSN = _resolve_admin_dsn()  # resolved once for the module-level docs; the
# fixture-time path (admin_dsn) is authoritative.

needs_postgres = pytest.mark.skipif(
    psycopg is None,
    reason="psycopg is not installed (the optional [postgres] extra)",
)


@pytest.fixture(autouse=True)
def _seed_pg_secret_in_isolated_store() -> None:
    """Seed the PostgreSQL credential into the conftest-isolated store.

    ``tests/conftest.py`` redirects ``SecureSecretStore``'s root to a session
    temp dir, so any credential seeded at import time is invisible at fixture
    time. ``build_postgres_url`` reads ``db.postgresql.password`` from that
    isolated store, so the password the ``NSE_PG_TEST_URL`` arm carries has to
    be re-seeded here, after isolation is installed. The value is never logged.
    """
    if not PG_URL or psycopg is None:
        return
    try:
        from psycopg.conninfo import conninfo_to_dict

        pw = conninfo_to_dict(PG_URL).get("password")
    except Exception:  # pragma: no cover - unparseable, not ours to fix
        pw = None
    if not pw:
        return
    try:
        SecureSecretStore().set_secret("db.postgresql.password", str(pw))
    except Exception:  # pragma: no cover - a locked store degrades, not crashes
        pass


@pytest.fixture(scope="module")
def admin_dsn() -> str:
    """The PostgreSQL instance URL, resolved at fixture time.

    Deferred from module scope on purpose: ``_resolve_admin_dsn`` reaches the
    settings DB / secret store, and the conftest isolation fixtures are not
    installed at import time. Resolving here keeps a no-PG box on the skip
    branch instead of erroring the fixture setup.
    """
    dsn = _resolve_admin_dsn()
    if not dsn:
        pytest.skip("no PostgreSQL arm reachable (NSE_PG_TEST_URL / persisted provider)")
    return dsn


#: Throwaway database, unique to this module so a concurrent lane cannot
#: collide. The live database is only ever CREATE/DROP'd on this name.
SCRATCH_DB = f"nse_ai_registry_pg_{uuid.uuid4().hex[:8]}"


@pytest.fixture(scope="module")
def scratch_dsn(admin_dsn: str) -> Iterator[str]:
    admin = admin_dsn
    with psycopg.connect(admin, connect_timeout=15, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"')
            cur.execute(f'CREATE DATABASE "{SCRATCH_DB}"')
    try:
        yield f"{admin}/{SCRATCH_DB}"
    finally:
        with contextlib.suppress(Exception):
            with psycopg.connect(admin, connect_timeout=15, autocommit=True) as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        "WHERE datname = %s AND pid <> pg_backend_pid()",
                        (SCRATCH_DB,),
                    )
        with psycopg.connect(admin, connect_timeout=15, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"')


def _host_of(dsn: str) -> str:
    try:
        return str(psycopg.conninfo_to_dict(dsn).get("host") or "localhost")
    except Exception:
        return "localhost"


def _port_of(dsn: str) -> int:
    try:
        return int(psycopg.conninfo_to_dict(dsn).get("port") or 5432)
    except Exception:
        return 5432


# ---------------------------------------------------------------------------
# Shared config builders -- the same assertions on both backends
# ---------------------------------------------------------------------------


def _make_config(provider_id: str, model: str = "m1", version: str = "1") -> Any:
    from nexus_scalp.ai_providers.registry import ProviderConfig

    return ProviderConfig(
        provider_id=provider_id,
        provider_name=provider_id.upper(),
        endpoint="https://example.invalid",
        default_model=model,
        configuration_version=version,
    )


def _row_count(store: object, table: str) -> int:
    rows = store._driver.query(f"SELECT count(*) AS c FROM {table}")
    return int(next(iter(rows[0].values()))) if rows else 0


# ===========================================================================
# TEST A / C / D / E: SQLite upsert, insert, update, idempotent repeat
# ===========================================================================


@pytest.fixture()
def sqlite_store(tmp_path: Path) -> Iterator[object]:
    from nexus_scalp.ai_providers.registry import ProviderRegistryStore

    store = ProviderRegistryStore(tmp_path / "ai_registry.db")
    try:
        yield store
    finally:
        with contextlib.suppress(Exception):
            store.close()


def test_sqlite_upsert_persists_and_reloads(sqlite_store: object) -> None:
    """TEST A: configure, then reload returns the same configuration."""
    assert sqlite_store.upsert(_make_config("openrouter", model="claude-3.5")) is True
    assert sqlite_store.list_provider_ids() == ["openrouter"]
    assert _row_count(sqlite_store, "ai_provider_config") == 1

    back = sqlite_store.get_config("openrouter")
    assert back is not None
    assert back.default_model == "claude-3.5"
    assert back.endpoint == "https://example.invalid"


def test_sqlite_upsert_does_not_duplicate_rows(sqlite_store: object) -> None:
    """TEST D + E: repeated saves keep exactly one row, newest values win."""
    for i in range(5):
        assert sqlite_store.upsert(_make_config("openrouter", model=f"v{i}")) is True
    assert _row_count(sqlite_store, "ai_provider_config") == 1
    back = sqlite_store.get_config("openrouter")
    assert back is not None
    assert back.default_model == "v4"


def test_sqlite_update_of_existing_provider(sqlite_store: object) -> None:
    """TEST C: a second write updates the row in place."""
    sqlite_store.upsert(_make_config("openrouter", model="old"))
    sqlite_store.upsert(_make_config("openrouter", model="new", version="2"))
    ids = sqlite_store.list_provider_ids()
    assert ids.count("openrouter") == 1
    back = sqlite_store.get_config("openrouter")
    assert back is not None
    assert back.default_model == "new"
    assert back.configuration_version == "2"


def test_sqlite_activation_round_trip(sqlite_store: object) -> None:
    """The single-row activation table: repeated writes stay one row."""
    from nexus_scalp.ai_providers.registry import ActivationState, DecisionMode

    for primary in ("openrouter", "internal_nse_ml", "openrouter"):
        assert sqlite_store.set_activation(
            ActivationState(primary_provider=primary, decision_mode=DecisionMode.HYBRID)
        )
    assert _row_count(sqlite_store, "ai_provider_activation") == 1
    act = sqlite_store.get_activation()
    assert act is not None
    assert act.primary_provider == "openrouter"


# ===========================================================================
# TEST B: PostgreSQL upsert through the REAL psycopg binding path
# ===========================================================================


@pytest.fixture()
def pg_store(admin_dsn: str, scratch_dsn: str, monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    """A registry store whose driver is a real psycopg connection.

    No mocking: the store resolves PostgreSQL exactly as it does after
    ``nexus db-portability switch``. The registry is APPLICATION STATE, so it
    follows the persisted application database provider.
    """
    from nexus_scalp.ai_providers.registry import ProviderRegistryStore
    from nexus_scalp.database.config import DatabaseConfig, build_postgres_url

    user = "postgres"
    try:
        user = psycopg.conninfo_to_dict(admin_dsn).get("user", "postgres") or "postgres"
    except Exception:
        pass
    monkeypatch.setenv("NSE_DATABASE__PROVIDER", "postgresql")
    monkeypatch.setenv("NSE_DATABASE__PG_HOST", _host_of(scratch_dsn))
    monkeypatch.setenv("NSE_DATABASE__PG_PORT", str(_port_of(scratch_dsn)))
    monkeypatch.setenv("NSE_DATABASE__PG_USER", user)
    monkeypatch.setenv("NSE_DATABASE__PG_DATABASE", SCRATCH_DB)

    cfg = DatabaseConfig.for_postgres(
        "settings",
        host=_host_of(scratch_dsn),
        port=_port_of(scratch_dsn),
        database=SCRATCH_DB,
        username=user,
    )
    store = ProviderRegistryStore(build_postgres_url(cfg, SecureSecretStore()))
    try:
        yield store
    finally:
        with contextlib.suppress(Exception):
            store.close()


@needs_postgres
def test_pg_upsert_persists_and_reloads(pg_store: object) -> None:
    """TEST B: the exact path that raised the ProgrammingError.

    Before the fix the executed statement carried literal ``?`` markers and
    psycopg reported 0 placeholders against 4 parameters. A successful return
    here means the driver generated placeholders psycopg understands AND the
    parameters bound and persisted.
    """
    assert pg_store.upsert(_make_config("openrouter", model="claude-3.5")) is True
    assert pg_store.list_provider_ids() == ["openrouter"]
    assert _row_count(pg_store, "ai_provider_config") == 1

    back = pg_store.get_config("openrouter")
    assert back is not None
    assert back.default_model == "claude-3.5"


@needs_postgres
def test_pg_upsert_does_not_duplicate_rows(pg_store: object) -> None:
    """TEST E on PostgreSQL: repeated saves stay one row, no growth."""
    for i in range(5):
        assert pg_store.upsert(_make_config("openrouter", model=f"v{i}")) is True
    assert _row_count(pg_store, "ai_provider_config") == 1
    back = pg_store.get_config("openrouter")
    assert back is not None
    assert back.default_model == "v4"


@needs_postgres
def test_pg_update_of_existing_provider(pg_store: object) -> None:
    """TEST C on PostgreSQL: update-in-place via ON CONFLICT."""
    pg_store.upsert(_make_config("openrouter", model="old"))
    pg_store.upsert(_make_config("openrouter", model="new", version="2"))
    assert _row_count(pg_store, "ai_provider_config") == 1
    back = pg_store.get_config("openrouter")
    assert back is not None
    assert back.default_model == "new"
    assert back.configuration_version == "2"


@needs_postgres
def test_pg_activation_round_trip(pg_store: object) -> None:
    """The activation upsert path through psycopg (the 8-column statement)."""
    from nexus_scalp.ai_providers.registry import ActivationState, DecisionMode

    assert pg_store.set_activation(
        ActivationState(primary_provider="openrouter", decision_mode=DecisionMode.HYBRID)
    )
    assert pg_store.set_activation(
        ActivationState(primary_provider="internal_nse_ml", decision_mode=DecisionMode.HYBRID)
    )
    assert _row_count(pg_store, "ai_provider_activation") == 1
    act = pg_store.get_activation()
    assert act is not None
    assert act.primary_provider == "internal_nse_ml"


@needs_postgres
def test_pg_prev_activation_snapshot_round_trip(pg_store: object) -> None:
    """The third upsert path: the previous-activation snapshot table."""
    from nexus_scalp.ai_providers.registry import ActivationState, DecisionMode

    pg_store.set_activation(
        ActivationState(primary_provider="openrouter", decision_mode=DecisionMode.HYBRID)
    )
    pg_store.set_activation(
        ActivationState(primary_provider="internal_nse_ml", decision_mode=DecisionMode.HYBRID)
    )
    rows = pg_store._driver.query("SELECT * FROM ai_provider_prev_activation")
    assert len(rows) == 1, f"expected one snapshot row, got {len(rows)}"
    row = rows[0]
    assert row["primary_provider"] == "openrouter"
    assert row["decision_mode"] == DecisionMode.HYBRID.value


# ===========================================================================
# TEST 6: query/parameter arity certification
# ===========================================================================


def _generated_arity(store: object) -> tuple[int, int, str]:
    """Placeholders in the driver-generated SQL vs the parameter count.

    Reproduces the exact statement ``execute_upsert`` builds for the config
    upsert, so the certification is against the text that reaches the server,
    not the store's qmark template (whose ``?`` markers are intentionally not
    the executed placeholders).
    """
    from nexus_scalp.database.drivers.base import (
        _table_of,
        bind_placeholder_count,
        build_upsert_statement,
        parse_upsert_columns,
    )
    from nexus_scalp.database.upsert import upsert_columns

    store_sql = (
        "INSERT INTO ai_provider_config (provider_id, blob,"
        " configuration_version, updated_at) VALUES (?, ?, ?, ?)"
    )
    columns = parse_upsert_columns(store_sql)
    assert columns is not None
    statement = build_upsert_statement(
        _table_of(store_sql) or "ai_provider_config",
        columns,
        conflict_target=upsert_columns("ai_provider_config"),
        paramstyle=store._driver.paramstyle,
    )
    params = ("openrouter", "{}", "1", "2026-01-01T00:00:00Z")
    return bind_placeholder_count(statement, store._driver.name), len(params), statement


def test_sqlite_config_upsert_arity_matches(sqlite_store: object) -> None:
    """SQLite: the number of SQL placeholders equals the number of params."""
    placeholders, params, _ = _generated_arity(sqlite_store)
    assert placeholders == params, (
        f"SQLite config upsert arity mismatch: {placeholders} placeholders vs {params} params"
    )


@needs_postgres
def test_pg_config_upsert_arity_matches(pg_store: object) -> None:
    """PostgreSQL: the number of SQL placeholders equals the number of params.

    This is the assertion that would have caught the original defect: the SQL
    held 4 qmark markers psycopg does not treat as placeholders (0) while the
    caller passed 4 parameters.
    """
    placeholders, params, statement = _generated_arity(pg_store)
    assert placeholders == params, (
        f"PostgreSQL config upsert arity mismatch: {placeholders} placeholders "
        f"vs {params} params in {statement!r}"
    )
    assert "?" not in statement, f"qmark marker leaked into psycopg SQL: {statement!r}"


@needs_postgres
def test_pg_direct_binding_rejects_arity_mismatch(pg_store: object) -> None:
    """The raw binding seam itself rejects parameter-count mismatches.

    psycopg's own error is the contract: passing 4 parameters against a query
    with 3 real placeholders raises rather than silently no-ops or corrupts. A
    driver that regressed to dropping or mis-binding args would fail here
    rather than silently writing junk. The storage-path upserts are separately
    guarded by the A/B/C/D paths above; this is the driver-level backstop.
    """
    with pytest.raises(psycopg.errors.ProgrammingError) as excinfo:
        pg_store._driver.execute(
            "INSERT INTO ai_provider_config (provider_id, blob, configuration_version,"
            " updated_at) VALUES (%s, %s, %s)",
            ("openrouter", "{}", "1", "2026-01-01T00:00:00Z"),
        )
    assert "placeholder" in str(excinfo.value).lower() or "parameter" in str(excinfo.value).lower()


# ===========================================================================
# TEST F: API integration through the route/orchestrator/registry chain
# ===========================================================================


@pytest.fixture()
def orch(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    """An orchestrator over an isolated registry + secret store."""
    from nexus_scalp.ai_providers.orchestrator import ProviderOrchestrator
    from nexus_scalp.ai_providers.registry import ProviderRegistryStore

    monkeypatch.setenv("NEXUS_SETTINGS_DB", str(tmp_path / "settings_api.db"))
    store = ProviderRegistryStore(tmp_path / "ai_registry_api.db")
    try:
        yield ProviderOrchestrator(registry=store, secret_store=SecureSecretStore())
    finally:
        with contextlib.suppress(Exception):
            store.close()


@pytest.fixture()
def api_client(orch: Any, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """The FastAPI test client pointed at the isolated orchestrator.

    ``get_ai_provider_orchestrator`` is the route's only seam to the engine, so
    patching it keeps the whole chain -- route -> orchestrator -> registry ->
    driver -- intact while isolating the persistence target.
    """
    from fastapi import FastAPI

    import nexus_scalp.web.ai_providers_routes as routes

    monkeypatch.setattr(routes, "get_ai_provider_orchestrator", lambda: orch)
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app)


def test_api_configure_persists_and_reloads(api_client: TestClient, orch: Any) -> None:
    """TEST F: route_configure -> orchestrator -> registry -> persistence.

    The route must not bypass the registry layer: it returns OK only when the
    row really landed, and a later read reflects the persisted state.
    """
    resp = api_client.post(
        "/api/ai-providers/providers/openrouter/configure",
        json={"endpoint": "https://example.invalid", "model": "claude-3.5"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "OK"
    assert body["provider_id"] == "openrouter"

    # The orchestrator (not a second HTTP call) is the persistence verdict.
    back = orch._registry.get_config("openrouter")
    assert back is not None, "the route returned OK but no row was persisted"
    assert back.endpoint == "https://example.invalid"
    assert back.default_model == "claude-3.5"


def test_api_repeated_configure_is_idempotent(api_client: TestClient, orch: Any) -> None:
    """Repeated UI/API saves produce one row, not one row per save."""
    for i in range(4):
        resp = api_client.post(
            "/api/ai-providers/providers/openrouter/configure",
            json={"endpoint": "https://example.invalid", "model": f"v{i}"},
        )
        assert resp.status_code == 200, resp.text

    rows = orch._registry._driver.query("SELECT count(*) AS c FROM ai_provider_config")
    count = int(next(iter(rows[0].values()))) if rows else -1
    assert count == 1, f"repeated configure produced {count} rows"
    back = orch._registry.get_config("openrouter")
    assert back is not None
    assert back.default_model == "v3"


def test_api_configure_does_not_echo_secrets(api_client: TestClient) -> None:
    """An API key supplied to the route never appears in the response."""
    resp = api_client.post(
        "/api/ai-providers/providers/openrouter/configure",
        json={"endpoint": "https://example.invalid", "model": "m1", "api_key": "sk-never-echo"},
    )
    assert resp.status_code == 200, resp.text
    assert "sk-never-echo" not in resp.text


def test_api_configure_reports_failure_when_persistence_fails(
    api_client: TestClient, orch: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TEST 7: a failed write must surface, not report success.

    ``configure_provider`` returns False when the registry refuses the row, and
    the route turns that into a 400. A swallowed exception here would let the
    UI claim a provider is configured while no row exists.
    """
    monkeypatch.setattr(orch._registry, "upsert", lambda *a, **k: False)
    resp = api_client.post(
        "/api/ai-providers/providers/openrouter/configure",
        json={"endpoint": "https://example.invalid", "model": "m1"},
    )
    assert resp.status_code == 400, resp.text
    back = orch._registry.get_config("openrouter")
    assert back is None, "a failed configure must leave no row behind"
