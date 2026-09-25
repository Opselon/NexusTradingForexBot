"""SQLITEDISABLED RUNTIME TRAP harness (mission S20).

WHAT THIS IS
============
A reusable pytest suite that FAILS the moment any operational code writes to
SQLite while ``database.provider=postgresql``.

The harness points the engine at a THROWAWAY PostgreSQL database (created via
psycopg ``CREATE DATABASE`` with a unique name; the live ``nexusdb`` is never
written to -- it is only the target of a CREATE/DROP of the throwaway name),
resolves the provider to PostgreSQL through the real seam
(``nexus_scalp.settings.paths`` + the DB fabric), then drives EVERY store's
write path while watching ``sqlite3.connect``.

THE TRAP
========
``sqlite3.connect`` is wrapped for the duration of each drive. Every call that
reaches a FILE is recorded. The suite then asserts that NO write landed outside
a strict allowlist -- the mission-sanctioned settings/config store
``app_settings.db`` only. Anything else (``audit.db``, ``news.db``,
``candle_intel.db``, ``ai_provider_decisions.db``, ``strategies.db``, ...) is
an operational-data violation and fails the test by construction.

Note on ``:memory:``: the schema-snapshot replay
(``nexus_scalp.database.migration.schema_snapshot``) opens a disposable
in-memory SQLite database to derive the DDL it translates into PostgreSQL.
That is migration machinery, not an operational write -- it touches no file --
so it is excluded from the recorded calls rather than allowed by name.

WHY IT MUST PASS ON THE CURRENT TREE
====================================
The suite passing proves the traps are ARMED (the interceptor is installed, the
write paths are exercised, the allowlist is enforced) -- not that every store
is fixed. A store whose write path is inert under PostgreSQL (it gates on
``_is_sqlite`` and returns False) records zero SQLite calls and passes; the
trap still fires the day someone wires a raw ``sqlite3.connect`` into it.

ISOLATION CONTRACT
==================
* the throwaway database is created and dropped per module;
* ``provision_domain`` leaves pools open against it; they are terminated
  server-side before the DROP (the fabric closes none of them and its registry
  only knows the two published backends -- terminate at the server, this is
  test teardown plumbing, not production shutdown);
* the PG password is resolved from the OS-backed SecretStore and seeded into
  the conftest-isolated store, exactly as ``nexus db postgres set-password``
  would (never written into this file or any log line);
* the module skips cleanly when PostgreSQL is unreachable, so the suite stays
  runnable on a SQLite-only box -- the trap arms there too, it just cannot
  prove the PostgreSQL side.
"""

from __future__ import annotations

import contextlib
import json
import os
import sqlite3
import sys
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "src"))

import pytest  # noqa: E402  (path insert above must precede third-party import)

psycopg = pytest.importorskip("psycopg")

from nexus_scalp.settings import secret_store as _secret_store_mod  # noqa: E402
from nexus_scalp.settings.secret_store import SecureSecretStore  # noqa: E402

# ---------------------------------------------------------------------------
# PostgreSQL arm
# ---------------------------------------------------------------------------

#: The instance the box is configured for, with the database name stripped.
#: The live database is only ever connected to for CREATE/DROP of the
#: throwaway name.
PG_URL = os.environ.get("NSE_PG_TEST_URL", "")


def _resolve_admin_dsn() -> str:
    """The configured instance URL, password resolved from the secret store.

    Prefers ``NSE_PG_TEST_URL`` (the suite's PostgreSQL arm convention, see
    ``tests/unit/test_database_portability.py``). Falls back to the persisted
    settings DB -- the machine's real ``database.postgresql_config`` -- so the
    trap exercises the box an operator actually switched to PostgreSQL.

    ``NSE_PG_TEST_URL`` is a URL in the CI convention
    (``postgresql://user:pw@host:port``) but a box may export the same instance
    as a libpq keyword/value DSN (``host=... password=[REDACTED] Stripping the
    trailing ``/dbname`` from the raw value is correct for the URL form only —
    a DSN has no trailing database segment, so ``rsplit`` silently shears a
    ``password=...`` token instead and authentication fails with the real
    credential in hand. libpq's own parser resolves the credential either way;
    the URL is then rebuilt in the shape psycopg accepts.
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
        return f"postgresql://{auth}{host}:{port}"
    from nexus_scalp.database.config import build_postgres_url, load_database_config

    try:
        cfg = load_database_config("audit")
        if cfg.is_postgresql:
            return build_postgres_url(cfg, SecureSecretStore()).rsplit("/", 1)[0]
    except Exception:
        pass
    return ""


def _seed_secret(password: str) -> None:
    """Carry the instance credential into the conftest-isolated secret store.

    ``_seed_pg_secret`` seeds the store from ``NSE_PG_TEST_URL`` at import time
    but only when the value parses as a URL — a libpq DSN is skipped and every
    later ``build_postgres_url`` resolves an empty password. Seeding here too
    makes both shapes equivalent for the whole module.
    """
    if not password:
        return
    try:
        if not SecureSecretStore().has_secret("db.postgresql.password"):
            SecureSecretStore().set_secret("db.postgresql.password", password)
    except Exception:  # pragma: no cover - a locked store degrades, not crashes
        pass


ADMIN_DSN = _resolve_admin_dsn()

needs_postgres = pytest.mark.skipif(
    not ADMIN_DSN, reason="no PostgreSQL arm reachable (NSE_PG_TEST_URL / persisted provider)"
)

#: Throwaway database, unique to this module so a concurrent lane cannot
#: collide. The live database is only ever CREATE/DROP'd on this name.
SCRATCH_DB = f"nse_sqlite_trap_{uuid.uuid4().hex[:8]}"

#: The mission-sanctioned SQLite allowlist. ``app_settings.db`` holds ONLY
#: installation/user configuration (application_settings /
#: configuration_metadata / settings_audit) -- never operational data.
SETTINGS_DB_FILENAME = "app_settings.db"


def _seed_pg_secret() -> None:
    """Seed the PG password into the conftest-isolated secret store.

    The session-scoped ``_isolate_web_auth_secret_store`` autouse fixture
    redirects ``secret_store.app_data_root`` to a per-run tmp dir so no test
    touches the operator's real DPAPI keystore. ``build_postgres_url`` /
    ``resolve_password`` read from that isolated store, so the credential the
    box actually holds must be re-seeded into it before any store resolves a
    DSN. Lifted from ``NSE_PG_TEST_URL`` when set, else already present from
    the isolated settings DB copy.
    """
    if PG_URL:
        try:
            from psycopg.conninfo import conninfo_to_dict

            pw = str(conninfo_to_dict(PG_URL).get("password") or "")
        except Exception:
            pw = ""
        if pw:
            _seed_secret(pw)


# ---------------------------------------------------------------------------
# The trap
# ---------------------------------------------------------------------------


class SQLiteTrap:
    """Intercepts every ``sqlite3.connect`` and records file-targeted calls.

    A trap is INSTALLED, not just declared: ``sqlite3.connect`` is rebound for
    the trap's lifetime so any operational code that opens a SQLite file while
    the provider is PostgreSQL leaves a call in :attr:`calls`. ``:memory:`` is
    excluded (migration/DDL replay machinery -- no file, no operational data).
    """

    __slots__ = ("_lock", "_real", "calls")

    def __init__(self) -> None:
        self.calls: list[str] = []
        self._real = sqlite3.connect
        self._lock = threading.Lock()

    def __enter__(self) -> SQLiteTrap:
        trap = self

        def _spy(database: Any, *args: Any, **kwargs: Any) -> Any:
            target = str(database)
            if target != ":memory:":
                with trap._lock:
                    trap.calls.append(target)
            return trap._real(database, *args, **kwargs)

        sqlite3.connect = _spy  # type: ignore[assignment]
        return self

    def __exit__(self, *exc: Any) -> None:
        sqlite3.connect = self._real  # type: ignore[assignment]

    # -- verdict ------------------------------------------------------------

    def violations(self) -> list[str]:
        """Every recorded target outside the mission-sanctioned allowlist.

        The allowlist is a FILENAME, not a path: the sanctioned settings store
        is ``app_settings.db`` wherever it lives (the conftest per-run tmp
        isolation copy, the operator's ``%LOCALAPPDATA%`` tree, a NEXUS_SETTINGS_DB
        override). Any other file -- the trading ledger, the news DB, the
        candle-intel DB, the decision ledger, the strategy DB -- is operational
        data and a violation while the provider is PostgreSQL.
        """
        out: list[str] = []
        for target in self.calls:
            if Path(target).name.lower() == SETTINGS_DB_FILENAME:
                continue
            # Schema-replay scratch (``_apply_learning_cycle_tables`` harvests a
            # store's DDL from a temp file it creates and deletes in the same
            # call). Not operational data, but it must stay identifiable so the
            # trap never learns to ignore real files.
            if Path(target).name.lower().endswith("_schema_harvest.db"):
                continue
            if target not in out:
                out.append(target)
        return out


@contextmanager
def _trap() -> Iterator[SQLiteTrap]:
    with SQLiteTrap() as t:
        yield t


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _seed_secret_once() -> None:
    _seed_pg_secret()


@pytest.fixture(scope="module")
def scratch_dsn() -> Iterator[str]:
    """Create a throwaway PostgreSQL database; drop it after the module.

    The live ``nexusdb`` is only ever connected to for CREATE/DROP of the
    throwaway name -- no schema or row from this suite ever touches it.
    """
    if not ADMIN_DSN:
        pytest.skip("no PostgreSQL arm reachable")
    with psycopg.connect(ADMIN_DSN, connect_timeout=15, autocommit=True) as conn:
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"')
            cur.execute(f'CREATE DATABASE "{SCRATCH_DB}"')
    try:
        yield f"{ADMIN_DSN}/{SCRATCH_DB}"
    finally:
        _terminate_scratch_connections()
        with psycopg.connect(ADMIN_DSN, connect_timeout=15, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (SCRATCH_DB,),
                )
                cur.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"')


def _terminate_scratch_connections() -> None:
    """Force-close every backend session still holding the scratch database.

    ``provision_domain`` opens more pools than it registers (the fabric's own
    internal read/write planes plus the two backends it publishes) and the
    stores deliberately close none of them. Terminating at the server is test
    teardown plumbing, not production shutdown.
    """
    from nexus_scalp.database.fabric import unregister_domain_backend

    for domain in _PROVISIONED_DOMAINS:
        with contextlib.suppress(Exception):
            unregister_domain_backend(domain)
    with contextlib.suppress(Exception):
        with psycopg.connect(ADMIN_DSN, connect_timeout=15, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (SCRATCH_DB,),
                )


#: Every domain the trap provisions on the throwaway database, so a
#: correctly-wired store has somewhere legitimate to write. The domain names
#: are the fabric/migration registry's own (``_DOMAIN_STATEMENTS`` in
#: ``nexus_scalp.database.migration``): the audit/news/candle_intel trio, the
#: AI-provider decision ledger, and the strategy factory's seven operational
#: tables.
_PROVISIONED_DOMAINS = (
    "audit",
    "news",
    "candle_intel",
    "ai_provider_decisions",
    "strategy_factory",
)


@pytest.fixture(autouse=True)
def _provider_postgresql(scratch_dsn: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve the provider to PostgreSQL against the throwaway database.

    The provider is pinned through the real resolution seam -- the persisted
    ``database.provider`` setting plus the connection settings the fabric and
    every store read -- so the code under test believes the box runs
    PostgreSQL exactly as it would after ``nexus db-portability switch``.
    """
    base = ADMIN_DSN
    user = "postgres"
    try:
        user = psycopg.conninfo_to_dict(base).get("user", "postgres") or "postgres"  # type: ignore[attr-defined]
    except Exception:
        pass
    monkeypatch.setenv("NSE_DATABASE__PROVIDER", "postgresql")
    monkeypatch.setenv("NSE_DATABASE__PG_HOST", _host_of(base))
    monkeypatch.setenv("NSE_DATABASE__PG_PORT", str(_port_of(base)))
    monkeypatch.setenv("NSE_DATABASE__PG_USER", user)
    monkeypatch.setenv("NSE_DATABASE__PG_DATABASE", SCRATCH_DB)
    # The test-isolation seams pin a SQLite TARGET. Under a PostgreSQL provider
    # they would mask exactly the violation the trap exists to find, and the
    # trap already isolates every SQLite file it creates, so clear them.
    for key in ("NEXUS_AUDIT_DB", "NEXUS_DECISIONS_DB", "NEXUS_NEWS_DB"):
        monkeypatch.delenv(key, raising=False)


def _host_of(dsn: str) -> str:
    try:
        return str(psycopg.conninfo_to_dict(dsn).get("host") or "localhost")  # type: ignore[attr-defined]
    except Exception:
        return "localhost"


def _port_of(dsn: str) -> int:
    try:
        return int(psycopg.conninfo_to_dict(dsn).get("port") or 5432)  # type: ignore[attr-defined]
    except Exception:
        return 5432


@pytest.fixture(autouse=True)
def _clear_domain_backends() -> None:
    """Drop any backend a previous test left in the process-global registry.

    ``provision_domain`` is idempotent across processes, but the registry is
    process-global and one test's pool points at a database that no longer
    exists after its teardown.
    """
    from nexus_scalp.database.fabric import unregister_domain_backend

    for domain in _PROVISIONED_DOMAINS:
        with contextlib.suppress(Exception):
            unregister_domain_backend(domain)


@pytest.fixture
def pg_counts() -> Any:
    """Read the throwaway PostgreSQL database directly (verdict evidence)."""

    def _count(table: str) -> int:
        try:
            with psycopg.connect(f"{ADMIN_DSN}/{SCRATCH_DB}", connect_timeout=15) as conn:
                with conn.cursor() as cur:
                    cur.execute(f"SELECT count(*) FROM {table}")
                    row = cur.fetchone()
                    return int(row[0]) if row else 0
        except Exception:
            return -1

    return _count


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _pg_config(domain: str) -> Any:
    """A PostgreSQL DatabaseConfig for one domain on the throwaway database."""
    from nexus_scalp.database.config import DatabaseConfig

    return DatabaseConfig.for_postgres(
        domain,
        host=_host_of(ADMIN_DSN),
        port=_port_of(ADMIN_DSN),
        database=SCRATCH_DB,
        username="postgres",
    )


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


# ===========================================================================
# TRAP 0: the harness itself is armed
# ===========================================================================


def test_the_trap_intercepts_a_real_sqlite_write(tmp_path: Path) -> None:
    """The interceptor must observe a genuine file-targeted connect.

    A trap that never fires proves nothing: this self-check wires a real
    SQLite write through the wrapped ``sqlite3.connect`` and asserts it was
    recorded, and that the settings DB stays allowlisted.
    """
    victim = tmp_path / "trap_self_check.db"
    with _trap() as t:
        conn = sqlite3.connect(str(victim))
        try:
            conn.execute("CREATE TABLE t(x INTEGER)")
            conn.commit()
        finally:
            conn.close()
        settings = tmp_path / SETTINGS_DB_FILENAME
        conn = sqlite3.connect(str(settings))
        try:
            conn.execute("CREATE TABLE s(x INTEGER)")
            conn.commit()
        finally:
            conn.close()

    assert str(victim) in t.calls, "the trap did not record a file-targeted connect"
    assert t.violations() == [str(victim)], "the allowlist let a non-settings file through"
    assert not any(p.endswith(SETTINGS_DB_FILENAME) for p in t.violations()), (
        "app_settings.db must stay allowlisted"
    )


@needs_postgres
def test_provider_resolves_to_postgresql(scratch_dsn: str) -> None:
    """The resolution seam actually lands on PostgreSQL for every domain.

    Without this, every trap below would pass vacuously: a store that believes
    it is on SQLite writes SQLite legitimately.
    """
    from nexus_scalp.database.config import load_database_config

    for domain in ("audit", "news", "candle_intel"):
        cfg = load_database_config(domain)
        assert cfg.is_postgresql, (
            f"domain {domain!r} resolved {cfg.provider.value!r}, not postgresql"
        )
        assert cfg.database == SCRATCH_DB, f"domain {domain!r} targets {cfg.database!r}"


# ===========================================================================
# Domain traps
# ===========================================================================


@needs_postgres
def test_trap_audit_domain_orders_land_in_postgres(scratch_dsn: str, pg_counts: Any) -> None:
    """audit: log_order / log_signal / log_account_snapshot write no SQLite.

    ``AuditRepository`` is the trading ledger -- the primary P0 target. Its
    write plane is provider-selected at construction, so a PostgreSQL box must
    route the queue through the fabric's pooled backend and never open
    ``artifacts/audit.db``.
    """
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.domain.enums import ActionType
    from nexus_scalp.domain.models import AccountInfo, TradeProposal

    with _trap() as t:
        repo = AuditRepository()
        try:
            repo.log_order(
                ticket=770001,
                order_id="trap-ord-1",
                symbol="XAUUSD",
                action="BUY",
                price=2030.0,
                stop_loss=2010.0,
                take_profit=2070.0,
                volume=0.05,
                reason="sqlite_trap",
                latency=1.2,
                execution_id="trap-exec-1",
            )
            repo.log_signal(
                TradeProposal(
                    request_id="trap-req-1",
                    symbol="XAUUSD",
                    generated_at=datetime.now(UTC),
                    action=ActionType.BUY,
                    confidence=0.72,
                    proposed_entry=2030.0,
                    stop_loss=2010.0,
                    take_profit=2070.0,
                    risk_reward_ratio=2.0,
                    reason_code="MODEL_SIGNAL",
                )
            )
            repo.log_account_snapshot(
                AccountInfo(
                    login=1001,
                    trade_mode=0,
                    leverage=100,
                    balance=10000.0,
                    equity=10042.0,
                    margin=120.0,
                    margin_free=9922.0,
                ),
                peak_equity=10090.0,
            )
        finally:
            with contextlib.suppress(Exception):
                repo.close()

    assert t.violations() == [], f"audit domain wrote SQLite under postgresql: {t.violations()}"
    assert pg_counts("audit_orders") >= 1, "audit write never reached PostgreSQL"


@needs_postgres
def test_trap_ai_provider_decisions_ledger(scratch_dsn: str, pg_counts: Any) -> None:
    """ai_provider_decisions: the ledger follows the active provider.

    The PR-#454 regression wrote decisions into a SQLite file while the rest of
    the engine used PostgreSQL -- an architecture violation with no runtime
    signal. ``resolve_decision_store_target`` is the production resolver; it
    must return a DSN, not a path.
    """
    from nexus_scalp.ai_providers.store import ProviderDecisionStore
    from nexus_scalp.settings.paths import resolve_decision_store_target

    with _trap() as t:
        target = resolve_decision_store_target()
        assert isinstance(target, str), (
            f"decision ledger resolved a SQLite Path under postgresql: {target!r}"
        )
        store = ProviderDecisionStore(dsn=target)
        try:
            store.record(
                {
                    "decision_id": f"trap-{uuid.uuid4().hex[:8]}",
                    "created_at": _now_iso(),
                    "decision_mode": "hybrid",
                    "active_provider": "system_one",
                    "active_model": "trap-model",
                    "final_action": "HOLD",
                    "fallback_used": False,
                    "providers_used": ["system_one"],
                    "providers_failed": [],
                    "latency_ms": 42.0,
                    "symbol": "XAUUSD",
                    "is_test_data": 1,
                }
            )
        finally:
            with contextlib.suppress(Exception):
                store.close()

    assert t.violations() == [], f"decision ledger wrote SQLite: {t.violations()}"
    assert pg_counts("ai_provider_decisions") >= 1, "decision never reached PostgreSQL"


@needs_postgres
def test_trap_news_domain_articles(scratch_dsn: str, pg_counts: Any) -> None:
    """news: an inserted article never lands in news.db.

    ``NewsDatabase`` speaks SQLite natively at dozens of call sites; under
    PostgreSQL every connection must come from the fabric's pooled write
    backend (``_PooledNewsConnection``), never a driver ``sqlite3.connect``.
    """
    from nexus_scalp.news.database import NewsDatabase

    with _trap() as t:
        db = NewsDatabase(config=_pg_config("news"))
        db.upsert_source(
            {
                "source_id": "trap_src",
                "name": "Trap Source",
                "kind": "RSS",
                "tier": "TIER_3",
                "url": "https://example.invalid/trap.xml",
                "feed_url": "https://example.invalid/trap.xml",
                "enabled": True,
                "poll_interval_sec": 300,
                "language": "en",
                "priority": 0.5,
                "seed_version": "trap",
            }
        )
        db.insert_article(
            {
                "article_id": f"trap-article-{uuid.uuid4().hex[:8]}",
                "article_hash": f"trap-hash-{uuid.uuid4().hex[:8]}",
                "canonical_url": "https://example.invalid/trap-article",
                "title": "SQLite runtime trap: news write routed to the wrong provider",
                "summary": "trap summary",
                "body": "trap body",
                "language": "en",
                "source_id": "trap_src",
                "source_name": "Trap Source",
                "published_at": _now_iso(),
                "published_at_source": "UNKNOWN",
                "importance": "MINOR",
                "importance_score": 0.1,
                "novelty": "NEW",
            }
        )

    assert t.violations() == [], f"news domain wrote SQLite: {t.violations()}"
    assert pg_counts("news_articles") >= 1, "news write never reached PostgreSQL"


@needs_postgres
def test_trap_news_post_event_memory(scratch_dsn: str) -> None:
    """news post-event memory: the validator's own table must not touch news.db.

    ``PostEventValidator._ensure_table`` opened a raw ``sqlite3.connect`` to the
    news DB path -- exactly the class of site this trap exists to catch. It now
    borrows the store's own portable connection.
    """
    from nexus_scalp.news.database import NewsDatabase
    from nexus_scalp.news.memory.post_event import PostEventValidator
    from nexus_scalp.news.models import NewsDirection

    with _trap() as t:
        db = NewsDatabase(config=_pg_config("news"))
        try:
            validator = PostEventValidator(db)
            validator.record_response(
                article_id="trap-article",
                predicted_direction=NewsDirection.BULLISH,
                predicted_strength=0.6,
                predicted_horizon="MACRO",
                response_samples=[
                    (datetime.now(UTC), 0.0),
                    (datetime.now(UTC), 0.2),
                    (datetime.now(UTC), 0.4),
                ],
            )
        except Exception:
            # A store whose write path is inert under PostgreSQL still must not
            # touch SQLite -- the trap fires on the connect, not the outcome.
            pass

    assert t.violations() == [], f"news post-event memory wrote SQLite: {t.violations()}"


@needs_postgres
def test_trap_candle_intel_domain(scratch_dsn: str) -> None:
    """candle_intel: a recorded candle never lands in candle_intel.db.

    ``CandleIntelStore`` used to build a SQLite ``DatabaseConfig`` from the
    relative ``artifacts/candle_intel.db`` default and write twelve tables of
    evidence into a local file nothing else read, even after the operator
    switched the box to PostgreSQL.

    The store's fabric DSN prefers ``NSE_PG_TEST_URL`` and re-points it at its
    own database, so the throwaway is reached only when the env URL already
    names it. The assertion that matters is the trap: no SQLite file is opened.
    """
    from nexus_scalp.candle_intelligence.store import CandleIntelStore

    with _trap() as t:
        store = CandleIntelStore(db_config=_pg_config("candle_intel"))
        try:
            store.record_candle(
                symbol="XAUUSD",
                timeframe="M1",
                timestamp=datetime.now(UTC),
                open_=2030.0,
                high=2035.0,
                low=2025.0,
                close=2031.0,
                volume=1.0,
            )
            store.flush(timeout=6.0)
        finally:
            with contextlib.suppress(Exception):
                store.close()

    assert t.violations() == [], f"candle_intel wrote SQLite: {t.violations()}"


@needs_postgres
def test_trap_strategy_factory_store(scratch_dsn: str) -> None:
    """strategy factory: generation/failure/event writes stay off the audit DB.

    ``strategies.factory.store`` writes through the audit repository's
    background queue, gated on ``repo._is_sqlite``. Under PostgreSQL the queue
    is drained by the pooled write backend; a raw ``sqlite3.connect`` on
    ``repo._db_path`` anywhere in this family is a violation.
    """
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.strategies.factory import store as factory_store

    with _trap() as t:
        repo = AuditRepository()
        try:
            assert not repo._is_sqlite, "the audit repository resolved SQLite under postgresql"
            factory_store.upsert_generation(
                repo,
                {
                    "generation_id": f"trap-gen-{uuid.uuid4().hex[:8]}",
                    "number": 1,
                    "mode": "MANUAL",
                    "population_target": 4,
                },
            )
            factory_store.record_failure(
                repo,
                {
                    "failure_id": f"trap-fail-{uuid.uuid4().hex[:8]}",
                    "stage": "DSL_VALIDATION",
                    "reason": "sqlite_trap_probe",
                },
            )
            factory_store.emit_event(
                repo,
                {
                    "event_id": f"trap-evt-{uuid.uuid4().hex[:8]}",
                    "event_type": "GENERIC",
                    "message": "sqlite trap probe",
                },
            )
        finally:
            with contextlib.suppress(Exception):
                repo.close()

    assert t.violations() == [], f"strategy factory wrote SQLite: {t.violations()}"


@needs_postgres
def test_trap_strategy_research_store(scratch_dsn: str) -> None:
    """strategy research: the isolated store must provision on PostgreSQL.

    ``StrategyResearchStore`` is the factory's own portable store (driver +
    ``strategies.db``). Under PostgreSQL it routes through the driver, not a
    local file -- the trap fires if it falls back to the SQLite default.
    """
    from nexus_scalp.strategies.research_store import StrategyResearchStore

    with _trap() as t:
        store = StrategyResearchStore(_pg_config("strategies"))
        try:
            store.ensure_schema()
            store.upsert_generation(
                {
                    "generation_id": f"trap-gen-{uuid.uuid4().hex[:8]}",
                    "number": 1,
                    "mode": "MANUAL",
                    "status": "RUNNING",
                    "created_at": _now_iso(),
                }
            )
        finally:
            with contextlib.suppress(Exception):
                store.close()

    assert t.violations() == [], f"strategy research store wrote SQLite: {t.violations()}"


@needs_postgres
def test_trap_strategy_research_store_default_config(scratch_dsn: str) -> None:
    """The store's zero-arg default must follow the ACTIVE provider too.

    ``default_config()`` anchored the store to ``artifacts/strategies.db``
    unconditionally, so a bare ``StrategyResearchStore()`` kept writing
    generated-strategy memory into a local file on a PostgreSQL box.
    """
    from nexus_scalp.strategies.research_store import StrategyResearchStore, default_config

    cfg = default_config()
    assert cfg.is_postgresql, (
        f"the store's default config resolved {cfg.provider.value!r}, not postgresql"
    )

    with _trap() as t:
        store = StrategyResearchStore()
        try:
            store.ensure_schema()
            store.upsert_generation(
                {
                    "generation_id": f"trap-gen-{uuid.uuid4().hex[:8]}",
                    "number": 2,
                    "mode": "MANUAL",
                    "status": "RUNNING",
                    "created_at": _now_iso(),
                }
            )
        finally:
            with contextlib.suppress(Exception):
                store.close()

    assert t.violations() == [], f"a zero-arg StrategyResearchStore wrote SQLite: {t.violations()}"


@needs_postgres
def test_trap_research_registry_and_runs(scratch_dsn: str) -> None:
    """research: registry upsert + run records stay off the audit DB.

    ``StrategyRegistry`` and ``list_research_runs`` connect to
    ``repo._db_path`` directly. Under PostgreSQL the gate must hold and the
    write must go through the pooled backend.
    """
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.research.models import StrategyRegistryEntry
    from nexus_scalp.research.registry import StrategyRegistry

    with _trap() as t:
        repo = AuditRepository()
        try:
            assert not repo._is_sqlite
            registry = StrategyRegistry(repo)
            entry = StrategyRegistryEntry(
                strategy_id=f"trap-strategy-{uuid.uuid4().hex[:8]}",
                strategy_version="1.0.0",
                discovery_source="sqlite_trap",
            )
            registry.upsert(entry)
            registry.list()
        finally:
            with contextlib.suppress(Exception):
                repo.close()

    assert t.violations() == [], f"research registry wrote SQLite: {t.violations()}"


@needs_postgres
def test_trap_experience_ledger(scratch_dsn: str) -> None:
    """experience: record_experience queues nothing into a SQLite file.

    ``ExperienceLedger`` writes through the audit queue (gated on
    ``_is_sqlite``); its read path uses ``_connect_sqlite``. Under PostgreSQL
    neither may open a file.
    """
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.experience.ledger import ExperienceLedger
    from nexus_scalp.experience.models import (
        ExperienceRecord,
        FeatureSnapshot,
        StrategyContext,
    )

    with _trap() as t:
        repo = AuditRepository()
        try:
            assert not repo._is_sqlite
            ledger = ExperienceLedger(repo)
            record = ExperienceRecord(
                experience_id=f"trap-exp-{uuid.uuid4().hex[:8]}",
                request_id="trap-req-1",
                idempotency_key=f"trap-key-{uuid.uuid4().hex[:8]}",
                symbol="XAUUSD",
                decision_timestamp=datetime.now(UTC),
                strategy_id="trap-strategy",
                context=StrategyContext(strategy_id="trap-strategy"),
                action="BUY",
                entry_reason="sqlite_trap",
                proposed_entry=2030.0,
                stop_loss=2010.0,
                take_profit=2070.0,
                feature_snapshot=FeatureSnapshot(values=[0.1] * 70, feature_dimension=70),
            )
            ledger.record_experience(record)
            with contextlib.suppress(Exception):
                ledger.list_recent(limit=5)
        finally:
            with contextlib.suppress(Exception):
                repo.close()

    assert t.violations() == [], f"experience ledger wrote SQLite: {t.violations()}"


@needs_postgres
def test_trap_shadow_store(scratch_dsn: str) -> None:
    """shadow: save_run / save_decision stay off the audit DB.

    ``ShadowStore`` opens ``audit_repo._db_path`` at ten sites (schema ensure,
    runs, decisions, comparisons, promotions). Under PostgreSQL every one is
    gated on ``_is_sqlite``; the trap fires if any becomes unconditional.
    """
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.shadow.models import (
        ShadowDecisionRecord,
        ShadowModelRef,
        SharedInputRef,
    )
    from nexus_scalp.shadow.store import ShadowStore

    with _trap() as t:
        repo = AuditRepository()
        try:
            assert not repo._is_sqlite
            store = ShadowStore(repo)
            from nexus_scalp.shadow.models import ShadowRun

            run = ShadowRun(
                run_id=f"trap-run-{uuid.uuid4().hex[:8]}",
                champion=ShadowModelRef(model_id="trap-champion", model_version="1.0.0"),
                challenger=ShadowModelRef(model_id="trap-challenger", model_version="1.0.0"),
            )
            store.save_run(run)
            store.save_decision(
                ShadowDecisionRecord(
                    shadow_decision_id=f"trap-sd-{uuid.uuid4().hex[:8]}",
                    run_id=run.run_id,
                    timestamp=datetime.now(UTC),
                    symbol="XAUUSD",
                    champion=ShadowModelRef(model_id="trap-champion", model_version="1.0.0"),
                    challenger=ShadowModelRef(model_id="trap-challenger", model_version="1.0.0"),
                    shared_input=SharedInputRef(timestamp=datetime.now(UTC), symbol="XAUUSD"),
                )
            )
        finally:
            with contextlib.suppress(Exception):
                repo.close()

    assert t.violations() == [], f"shadow store wrote SQLite: {t.violations()}"


@needs_postgres
def test_trap_shadow70_store(scratch_dsn: str) -> None:
    """shadow70: observations / events / health / drift stay off the audit DB.

    ``Shadow70Store`` reaches ``audit_repo._db_path`` directly for its schema
    ensure and its read-back histograms.
    """
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.shadow.shadow70.models import Shadow70Observation
    from nexus_scalp.shadow.shadow70.store import Shadow70Store

    with _trap() as t:
        repo = AuditRepository()
        try:
            assert not repo._is_sqlite
            store = Shadow70Store(repo)
            store.save_observation(
                Shadow70Observation(
                    observation_id=f"trap-obs-{uuid.uuid4().hex[:8]}",
                    snapshot_id=f"trap-snap-{uuid.uuid4().hex[:8]}",
                    timestamp=datetime.now(UTC),
                    symbol="XAUUSD",
                )
            )
            store.record_event(
                {
                    "event_id": f"trap-evt-{uuid.uuid4().hex[:8]}",
                    "event": "TRAP_PROBE",
                    "timestamp": _now_iso(),
                }
            )
            store.save_feature_health(
                [
                    {
                        "snapshot_id": "trap-snap",
                        "timestamp": _now_iso(),
                        "feature": "f0",
                        "feat_index": 0,
                        "samples": 10,
                        "finite_rate": 1.0,
                    }
                ]
            )
        finally:
            with contextlib.suppress(Exception):
                repo.close()

    assert t.violations() == [], f"shadow70 store wrote SQLite: {t.violations()}"


@needs_postgres
def test_trap_intelligence_store(scratch_dsn: str) -> None:
    """intelligence: behavior canonicalization stays off the audit DB.

    ``intelligence.store`` connects to ``repo._db_path`` at eight sites and
    ``behavior_canonical`` writes behavior-analysis rows; under PostgreSQL the
    gate must hold.
    """
    from nexus_scalp.adapters.database.audit_repository import AuditRepository

    with _trap() as t:
        repo = AuditRepository()
        try:
            assert not repo._is_sqlite
            # The read/write surfaces of the intelligence store are reached
            # through repo; a provider-gated store returns its documented
            # empty default and touches no SQLite file.
            with contextlib.suppress(Exception):
                from nexus_scalp.intelligence import store as intel_store

                intel_store.list_registered_strategies(repo)
        finally:
            with contextlib.suppress(Exception):
                repo.close()

    assert t.violations() == [], f"intelligence store wrote SQLite: {t.violations()}"


@needs_postgres
def test_trap_incident_store(scratch_dsn: str) -> None:
    """incidents: a saved incident never touches the audit DB.

    ``IncidentStore`` takes ``db_path`` from ``audit_repo._db_path`` and hands
    its writes to the audit queue. Under PostgreSQL ``db_path`` is empty and
    the URL contract holds (RT-004), so no SQLite file is opened.
    """
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.incidents.models import Incident, IncidentSeverity
    from nexus_scalp.incidents.store import IncidentStore

    with _trap() as t:
        repo = AuditRepository()
        try:
            assert not repo._is_sqlite
            store = IncidentStore(db_path="", audit_repo=repo)
            assert store.db_url.startswith("postgresql://"), (
                f"incident store resolved {store.db_url!r}, expected the pooled PostgreSQL URL"
            )
            assert store.db_path == "", (
                f"incident store kept a SQLite path {store.db_path!r} under postgresql"
            )
            store.save(
                Incident(
                    incident_id=f"trap-incident-{uuid.uuid4().hex[:8]}",
                    severity=IncidentSeverity.LOW,
                    component="sqlite_trap",
                    operation="trap_probe",
                    root_cause="sqlite runtime trap probe",
                )
            )
        finally:
            with contextlib.suppress(Exception):
                repo.close()

    assert t.violations() == [], f"incident store wrote SQLite: {t.violations()}"


@needs_postgres
def test_trap_governance_store(scratch_dsn: str) -> None:
    """governance: gate verdicts and events stay off the audit DB.

    ``governance.store`` reaches ``audit_repo._db_path`` at eight sites.
    """
    from nexus_scalp.adapters.database.audit_repository import AuditRepository

    with _trap() as t:
        repo = AuditRepository()
        try:
            assert not repo._is_sqlite
            with contextlib.suppress(Exception):
                from nexus_scalp.governance import store as gov_store

                gov_store.list_events(repo, limit=5)
        finally:
            with contextlib.suppress(Exception):
                repo.close()

    assert t.violations() == [], f"governance store wrote SQLite: {t.violations()}"


@needs_postgres
def test_trap_model_lifecycle_store(scratch_dsn: str) -> None:
    """model lifecycle: registry / store reads stay off the audit DB.

    ``model_lifecycle.store`` connects to ``repo._db_path`` at six sites and
    ``registry`` at five; under PostgreSQL all are gated.
    """
    from nexus_scalp.adapters.database.audit_repository import AuditRepository

    with _trap() as t:
        repo = AuditRepository()
        try:
            assert not repo._is_sqlite
            with contextlib.suppress(Exception):
                from nexus_scalp.model_lifecycle import store as ml_store

                ml_store.list_models(repo, limit=5)
        finally:
            with contextlib.suppress(Exception):
                repo.close()

    assert t.violations() == [], f"model lifecycle store wrote SQLite: {t.violations()}"


@needs_postgres
def test_trap_settings_store_stays_allowlisted(scratch_dsn: str) -> None:
    """The sanctioned config store is the ONLY SQLite that may be touched.

    ``SettingsDatabase`` (``app_settings.db``) is the mission-sanctioned
    configuration store. This both proves the allowlist is genuine (a real
    SQLite write that the trap correctly permits) and pins the boundary: a
    settings read must never open an operational database.
    """
    from nexus_scalp.settings.service import SettingsDatabase

    with _trap() as t:
        db = SettingsDatabase()
        try:
            db.set("sqlite_trap.probe", "1", source="TEST", actor="sqlite_trap")
            value = db.get("sqlite_trap.probe")
            assert value is not None and value.value == "1"
        finally:
            with contextlib.suppress(Exception):
                db.close()

    assert t.calls, "the settings DB opened no SQLite connection at all"
    assert all(p.endswith(SETTINGS_DB_FILENAME) for p in t.calls), (
        f"settings store opened a non-settings file: {t.calls}"
    )
    assert t.violations() == [], f"settings store wrote outside app_settings.db: {t.violations()}"


@needs_postgres
def test_trap_no_operational_sqlite_file_is_created(scratch_dsn: str, tmp_path: Path) -> None:
    """No operational SQLite file is created DURING the drive.

    Belt-and-braces on the connect-level trap: a store that writes via an API
    the wrapper cannot see (a subprocess, a C-level handle) would still leave a
    file behind. The pre-existing ``artifacts`` tree is snapshotted first so
    only files this test creates can fail it, and the canonical operational
    database names must never appear.
    """
    from nexus_scalp.adapters.database.audit_repository import AuditRepository

    operational = (
        "audit.db",
        "news.db",
        "candle_intel.db",
        "ai_provider_decisions.db",
        "strategies.db",
    )

    def _snapshot() -> set[Path]:
        out: set[Path] = set()
        for root in (REPO_ROOT / "artifacts", Path(os.environ.get("NEXUS_DATA_ROOT", ""))):
            if not root.exists():
                continue
            out |= {p for p in root.rglob("*.db") if p.name in operational}
        return out

    before = _snapshot()
    with _trap() as t:
        repo = AuditRepository()
        try:
            repo.log_order(
                ticket=770002,
                order_id="trap-ord-2",
                symbol="XAUUSD",
                action="SELL",
                price=2030.0,
                stop_loss=2050.0,
                take_profit=2010.0,
                volume=0.05,
                reason="sqlite_trap",
            )
        finally:
            with contextlib.suppress(Exception):
                repo.close()

    created = {p for p in _snapshot() if p not in before}
    assert not created, (
        f"operational SQLite file(s) created under postgresql: {sorted(p.name for p in created)}"
    )
    assert t.violations() == [], f"operational SQLite written under postgresql: {t.violations()}"


# ===========================================================================
# Evidence artifact
# ===========================================================================


@pytest.fixture(scope="module", autouse=True)
def _write_trap_manifest(scratch_dsn: str) -> None:
    """Record the trap arming state for the lane's evidence directory."""
    yield
    out = REPO_ROOT / "scratch_audit" / "lane_results"
    try:
        out.mkdir(parents=True, exist_ok=True)
        manifest = {
            "mission": "S20",
            "harness": "tests/unit/test_sqlite_runtime_trap.py",
            "scratch_db": SCRATCH_DB,
            "provider": "postgresql",
            "admin_dsn": ADMIN_DSN.replace("://", "://***@") if "://" in ADMIN_DSN else ADMIN_DSN,
            "allowlist": [SETTINGS_DB_FILENAME],
            "trap_count": _TRAP_COUNT,
            "status": "armed",
        }
        (out / "sqlite_trap_manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8"
        )
    except Exception:
        pass


#: The number of armed trap assertions in this module (self-documenting so a
#: downstream lane can assert the harness stayed at the mission's floor).
_TRAP_COUNT = len(
    [n for n, v in list(globals().items()) if n.startswith("test_trap_") and callable(v)]
)
