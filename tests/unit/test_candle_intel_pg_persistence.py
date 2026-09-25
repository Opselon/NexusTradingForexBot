"""candle_intel domain PostgreSQL persistence (DATABASE PORTABILITY).

WHAT THIS PINS
==============
``CandleIntelStore`` used to open SQLite directly: its constructor resolved
the provider itself and, on a box the operator had switched to PostgreSQL via
``nexus db-portability switch`` (the persisted ``database.provider`` +
``database.postgresql_config`` settings), still built a SQLite
``DatabaseConfig`` from the relative ``artifacts/candle_intel.db`` default and
wrote 12 tables of candle/pattern/regime/decision evidence into a local file
that nothing else read. The domain had authored DDL
(``schema_snapshot.candle_intel_schema_statements``,
``DatabaseDomain.CANDLE_INTEL``) and the fabric knew how to provision it — the
store just never asked.

The fix mirrors the proven audit-domain pattern (``resolve_audit_db_url`` /
``AuditRepository._build_pooled_write_backend``):

  * the store resolves the ACTIVE provider through
    ``load_database_config('candle_intel')`` and routes to the fabric's pooled
    backends (``get_domain_backend`` / ``provision_domain``) when that provider
    is PostgreSQL;
  * SQLite stays the default and byte-identical in behavior;
  * DDL is translated through the repo's own ``pg_schema.translate_ddl`` so the
    store's declared schema and a ``nexus db connect`` provision converge on
    the same physical schema.

ISOLATION CONTRACT
==================
Every test runs against a THROWAWAY database created and dropped per test —
NEVER the live ``nexusdb``. The base connection URL comes from
``NSE_PG_TEST_URL`` (the suite's existing PostgreSQL arm convention, see
``tests/unit/test_database_portability.py``); the scratch database name is
unique to this module so a concurrent lane cannot collide. The module skips
cleanly when ``NSE_PG_TEST_URL`` is unset. No credential is written into this
file or any log line.
"""

from __future__ import annotations

import contextlib
import os
import re
import sys
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

import pytest

psycopg = pytest.importorskip("psycopg")

from nexus_scalp.candle_intelligence.config import (  # noqa: E402
    CandleIntelligenceConfig,
)
from nexus_scalp.candle_intelligence.models import (  # noqa: E402
    CandleCloseClass,
    CandleCloseSummary,
    CandleDecision,
    DecisionType,
    PatternDetection,
    RegimeState,
    RiskEvaluation,
    RiskState,
    TradeBias,
)
from nexus_scalp.candle_intelligence.store import (  # noqa: E402
    TABLES,
    CandleIntelStore,
)
from nexus_scalp.database.config import PG_PASSWORD_SECRET_KEY, DatabaseConfig  # noqa: E402
from nexus_scalp.database.migration import _DOMAIN_STATEMENTS  # noqa: E402
from nexus_scalp.database.provider import DatabaseProvider  # noqa: E402

PG_URL = os.environ.get("NSE_PG_TEST_URL", "")
needs_pg = pytest.mark.skipif(not PG_URL, reason="NSE_PG_TEST_URL not set (PostgreSQL CI test arm)")

#: The PostgreSQL role and secret key the fabric itself uses — the pooled
#: backends reconnect lazily, so a config that names another role (or another
#: secret key) turns the first reconnect into a fatal auth failure. The role is
#: resolved from the instance the suite was pointed at (``NSE_PG_TEST_URL``),
#: because a scratch role exists only on a box that created it: the default is
#: the superuser every PostgreSQL install ships.
_LIVE_TABLES = frozenset(t for t in TABLES)


def _pg_user() -> str:
    """The instance's connecting role, URL or libpq DSN shaped.

    Defaults to ``postgres`` — the superuser every install ships — because a
    scratch role only exists on a box that created it, while the suite must
    run wherever ``NSE_PG_TEST_URL`` points.
    """
    try:
        from psycopg.conninfo import conninfo_to_dict

        parts = conninfo_to_dict(PG_URL)
    except Exception:  # pragma: no cover - an unparseable value is a config error
        return "postgres"
    user = parts.get("user")
    return str(user) if user else "postgres"


#: The PostgreSQL role the fabric connects as, resolved from the instance the
#: suite was pointed at (see ``_pg_user``).
PG_USER = _pg_user()

#: Throwaway database — created/dropped per test. NEVER the live nexusdb.
#: Lowercase on purpose: PostgreSQL folds unquoted identifiers to lowercase and
#: this suite matches on ``datname`` for the connection cleanup, so a mixed-case
#: name would never terminate its own sessions.
SCRATCH_DB = "nse_cintel_pg_test"


def _pg_password() -> str:
    """The instance password from ``NSE_PG_TEST_URL``, URL or libpq DSN shaped.

    ``conninfo_to_dict`` is libpq's own connection-string parser, so it reads
    the CI URL form (``postgresql://user:pw@host``) and the keyword/value form
    (``host=... password=[REDACTED] alike. Returning ``""`` when the credential is
    absent keeps the fixture a no-op rather than a crash on a box that exports
    an instance with no password.
    """
    try:
        from psycopg.conninfo import conninfo_to_dict

        parts = conninfo_to_dict(PG_URL)
    except Exception:  # pragma: no cover - an unparseable value is a config error
        return ""
    pw = parts.get("password")
    return str(pw) if pw else ""


def _base_dsn() -> str:
    """The configured instance with the database name stripped (for CREATE/DROP).

    ``NSE_PG_TEST_URL`` is a URL in the CI convention but may arrive as a libpq
    keyword/value DSN. ``rsplit('/', 1)`` strips the database only for the URL
    form; on a DSN it keeps the ``dbname`` token and then appending the scratch
    name yields a value libpq cannot parse. ``conninfo_to_dict`` reads either
    shape and ``make_conninfo`` rebuilds it without the database segment.
    """
    assert PG_URL, "NSE_PG_TEST_URL must be set"
    try:
        from psycopg.conninfo import conninfo_to_dict, make_conninfo

        parts = conninfo_to_dict(PG_URL)
    except Exception:  # pragma: no cover - unparseable, not ours to fix
        return PG_URL.rsplit("/", 1)[0]
    parts.pop("dbname", None)
    parts.pop("database", None)
    return make_conninfo("", **parts)


def _scratch_dsn() -> str:
    """The throwaway database as a connectable DSN for either input shape."""
    return _base_dsn() + f" dbname={SCRATCH_DB}"


@pytest.fixture(autouse=True)
def _seed_pg_secret_in_isolated_store() -> None:
    """Seed the PG password into the conftest-isolated secret store.

    The session-scoped ``_isolate_web_auth_secret_store`` autouse fixture
    redirects ``secret_store.app_data_root`` to a per-run tmp dir (so no test
    ever touches the operator's real DPAPI keystore). The password never lives
    in this file: it is lifted from ``NSE_PG_TEST_URL`` and written into the
    isolated store, exactly as ``nexus db postgres set-password`` would.

    ``NSE_PG_TEST_URL`` carries the instance URL in the repo's CI convention
    (``postgresql://user:password@host:port``), but the box's environment may
    instead export a libpq keyword/value DSN (``host=... password=[REDACTED] Both
    shapes reach the same credential through ``conninfo_to_dict``, which is
    the parser libpq itself uses — so a URL is read as a URL and a DSN as a
    DSN, instead of assuming the one the caller happened to set.
    """
    if not PG_URL:
        return
    pw = _pg_password()
    if not pw:
        return
    from nexus_scalp.settings.secret_store import SecureSecretStore

    store = SecureSecretStore()
    if not store.has_secret("db.postgresql.password"):
        store.set_secret("db.postgresql.password", pw)


@pytest.fixture
def pg_dsn() -> Iterator[str]:
    """Create an isolated throwaway database; drop it after the test.

    The live ``nexusdb`` is only ever connected to for CREATE/DROP of the
    scratch database — no schema or row from this suite ever touches it.
    Pooled connections to the scratch database are terminated first
    (``provision_domain`` registers pools the store never closes; a leased
    connection would otherwise keep the database in use and block DROP).
    """
    with psycopg.connect(_base_dsn(), connect_timeout=10, autocommit=True) as conn:
        with conn.cursor() as cur:
            _terminate_scratch_connections()
            cur.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"')
            cur.execute(f'CREATE DATABASE "{SCRATCH_DB}"')
    try:
        yield _scratch_dsn()
    finally:
        _terminate_scratch_connections()
        with psycopg.connect(_base_dsn(), connect_timeout=10, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(f'DROP DATABASE IF EXISTS "{SCRATCH_DB}"')


#: Every domain the fabric knows how to provision. Teardown closes the pooled
#: backends for all of them (provisioning one opens pools against the shared
#: instance), matching the sibling test_pg_schema_convergence suite.
_ALL_FABRIC_DOMAINS: tuple[str, ...] = tuple(_DOMAIN_STATEMENTS)


def _terminate_scratch_connections() -> None:
    """Release every pooled backend still holding the scratch database.

    ``provision_domain`` registers pooled read+write backends in a
    process-global registry and the store deliberately never closes them (they
    are shared resources). Closing them by domain — for EVERY domain, because
    ``provision_domain`` for one domain opens pools keyed on the instance, not
    the domain — then terminating the survivors is the pattern the sibling
    ``test_pg_schema_convergence`` suite settled on; without it ``DROP
    DATABASE`` fails with ObjectInUse.
    """
    from nexus_scalp.database.fabric import get_domain_backend, unregister_domain_backend

    for domain in _ALL_FABRIC_DOMAINS:
        for readonly in (False, True):
            backend = get_domain_backend(domain, readonly=readonly)
            if backend is None:
                continue
            close = getattr(backend, "close", None)
            if callable(close):
                with contextlib.suppress(Exception):
                    close()
        unregister_domain_backend(domain)
    with contextlib.suppress(Exception):
        with psycopg.connect(_base_dsn(), connect_timeout=10, autocommit=True) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                    "WHERE datname = %s AND pid <> pg_backend_pid()",
                    (SCRATCH_DB.lower(),),
                )


@pytest.fixture
def scratch_config(pg_dsn: str) -> DatabaseConfig:
    """A candle_intel DatabaseConfig pointed at the throwaway database.

    Built through ``for_postgres`` so the username (``nse_user``) and the
    password-secret key match the fabric's own DSN construction — a hand-rolled
    config would make the pool reconnect as a role PostgreSQL rejects.
    """
    return DatabaseConfig.for_postgres(
        domain="candle_intel",
        host="localhost",
        port=5432,
        database=SCRATCH_DB,
        username=PG_USER,
        password_secret=PG_PASSWORD_SECRET_KEY,
    )


def _clear_domain_backend() -> None:
    """Unregister any candle_intel backend a previous test left in the fabric.

    ``provision_domain`` is idempotent across processes but the registry is
    process-global, and one test's pool must not be handed to the next (it
    points at a database that no longer exists).
    """
    from nexus_scalp.database.fabric import unregister_domain_backend

    unregister_domain_backend("candle_intel")


# ---------------------------------------------------------------------------
# provider resolution
# ---------------------------------------------------------------------------


@needs_pg
def test_explicit_db_config_routes_to_postgresql(scratch_config: DatabaseConfig) -> None:
    """An explicit PostgreSQL db_config is honored (no silent SQLite downgrade)."""
    _clear_domain_backend()
    store = CandleIntelStore(db_config=scratch_config)
    try:
        assert store._config.is_postgresql, "store resolved to SQLite, not PostgreSQL"
        assert store._config.database == SCRATCH_DB
    finally:
        store.close()
        _clear_domain_backend()


@needs_pg
def test_store_resolves_the_active_provider_from_settings(
    pg_dsn: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ACTIVE provider wins without an explicit db_config.

    This is the defect's exact shape: a bare ``CandleIntelStore()`` on a
    PostgreSQL-configured box used to open SQLite. The persisted provider is
    what the operator switched, so it must be what the store uses.
    """
    _clear_domain_backend()
    monkeypatch.setenv("NSE_DATABASE__PROVIDER", "postgresql")
    monkeypatch.setenv("NSE_DATABASE__PG_DATABASE", SCRATCH_DB)

    store = CandleIntelStore()
    try:
        assert store._config.is_postgresql, (
            "a bare CandleIntelStore() ignored the active provider and fell back to SQLite"
        )
        assert store._config.database == SCRATCH_DB
    finally:
        store.close()
        _clear_domain_backend()


# ---------------------------------------------------------------------------
# schema provisioning
# ---------------------------------------------------------------------------


@needs_pg
def test_store_provisions_every_domain_table(pg_dsn: str) -> None:
    """Construction lands all 12 candle_intel tables on PostgreSQL."""
    _clear_domain_backend()
    cfg = DatabaseConfig.for_postgres(
        domain="candle_intel",
        host="localhost",
        port=5432,
        database=SCRATCH_DB,
        username=PG_USER,
        password_secret=PG_PASSWORD_SECRET_KEY,
    )
    store = CandleIntelStore(db_config=cfg)
    try:
        with psycopg.connect(pg_dsn, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1")
                created = frozenset(str(r[0]) for r in cur.fetchall())
    finally:
        store.close()
        _clear_domain_backend()

    missing = sorted(_LIVE_TABLES - created)
    assert not missing, f"tables the store did not provision on PostgreSQL: {missing}"


@needs_pg
def test_provisioned_schema_matches_the_authored_ddl(pg_dsn: str) -> None:
    """The store's own DDL path and the domain's authored snapshot agree.

    ``schema_snapshot.candle_intel_schema_statements()`` is the canonical list
    ``nexus db connect`` provisions from; the store must land the same tables
    so the two paths cannot drift.
    """
    from nexus_scalp.database.migration.schema_snapshot import (
        candle_intel_schema_statements,
    )

    _CREATE_TABLE = re.compile(r"(?is)^\s*CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)(\"?)(\w+)\1")
    authored = frozenset(
        m.group(2) for s in candle_intel_schema_statements() if (m := _CREATE_TABLE.match(s))
    )

    _clear_domain_backend()
    cfg = DatabaseConfig.for_postgres(
        domain="candle_intel",
        host="localhost",
        port=5432,
        database=SCRATCH_DB,
        username=PG_USER,
        password_secret=PG_PASSWORD_SECRET_KEY,
    )
    store = CandleIntelStore(db_config=cfg)
    try:
        with psycopg.connect(pg_dsn, connect_timeout=10) as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1")
                created = frozenset(str(r[0]) for r in cur.fetchall())
    finally:
        store.close()
        _clear_domain_backend()

    assert _LIVE_TABLES <= created
    assert _LIVE_TABLES <= authored
    assert created >= authored - {"schema_meta", "schema_migrations"}


# ---------------------------------------------------------------------------
# write + read roundtrip (the durability contract)
# ---------------------------------------------------------------------------


def _make_decision(ts: datetime) -> CandleDecision:
    summary = CandleCloseSummary(
        timestamp=ts,
        symbol="EURUSD",
        timeframe="M1",
        open=1.10000,
        high=1.10060,
        low=1.09980,
        close=1.10040,
        range=0.00080,
        body=0.00040,
        upper_wick=0.00020,
        lower_wick=0.00020,
        body_ratio=0.5,
        upper_wick_ratio=0.25,
        lower_wick_ratio=0.25,
        close_position_in_range=0.75,
        open_to_close_direction="bullish",
        close_strength=0.8,
        rejection_score=0.1,
        continuation_score=0.7,
        reversal_score=0.2,
        indecision_score=0.1,
        momentum_decay_score=0.05,
        close_class=CandleCloseClass.BULLISH_CONTINUATION,
    )
    regime = RegimeState(
        timestamp=ts,
        symbol="EURUSD",
        timeframe="M1",
        regime="TREND",
        volatility_state="HIGH",
        atr=0.00045,
        spread=0.00012,
    )
    risk = RiskEvaluation(
        risk_state=RiskState.SAFE,
        risk_allowed=True,
        reason_codes=["LIQUIDITY_OK"],
    )
    return CandleDecision(
        timestamp=ts,
        symbol="EURUSD",
        timeframe="M1",
        close_summary=summary,
        regime_state=regime,
        risk_evaluation=risk,
        detected_patterns=[
            PatternDetection(
                pattern_name="ENGULFING",
                direction="bullish",
                raw_score=0.82,
                context_weight=1.0,
                confidence_score=0.77,
                requires_confirmation=False,
                reason_codes=["ENGULFING"],
            )
        ],
        trade_bias=TradeBias.BULLISH,
        confidence_score=0.71,
        entry_allowed=True,
        hold_allowed=True,
        fast_exit_required=False,
        exit_required=False,
        modify_order=False,
        cancel_pending=False,
        no_trade_reason="",
        decision_type=DecisionType.ENTRY,
        reason_codes=["ENGULFING", "TREND"],
        raw_payload={"tick_bid": 1.10040, "tick_ask": 1.10042},
        computed_payload={"atr_pips": 4.5},
    )


@needs_pg
def test_write_and_read_roundtrip_on_postgres(pg_dsn: str) -> None:
    """Every record_* method writes durably and reads back on PostgreSQL.

    The ring buffer serves the hot read path on both providers; the DB fallback
    (``query_recent`` with an empty ring) is the path that used to be
    SQLite-only and is what this exercises by clearing the rings first.
    """
    _clear_domain_backend()
    cfg = DatabaseConfig.for_postgres(
        domain="candle_intel",
        host="localhost",
        port=5432,
        database=SCRATCH_DB,
        username=PG_USER,
        password_secret=PG_PASSWORD_SECRET_KEY,
    )
    store = CandleIntelStore(db_config=cfg)
    try:
        base = datetime(2026, 9, 25, 12, 0, 0, tzinfo=UTC)
        ts = base
        assert store.record_candle(
            "EURUSD", "M1", ts, 1.10000, 1.10060, 1.09980, 1.10040, volume=1.5
        )
        assert store.record_candle_closure(_make_decision(ts).close_summary, regime="TREND")
        assert (
            store.record_patterns(
                "EURUSD",
                "M1",
                ts,
                [
                    PatternDetection(
                        pattern_name="ENGULFING",
                        direction="bullish",
                        raw_score=0.82,
                        context_weight=1.0,
                        confidence_score=0.77,
                        requires_confirmation=False,
                        reason_codes=["ENGULFING"],
                    )
                ],
                regime="TREND",
            )
            == 1
        )
        assert store.record_regime(
            RegimeState(
                timestamp=ts,
                symbol="EURUSD",
                timeframe="M1",
                regime="TREND",
                volatility_state="HIGH",
                atr=0.00045,
                spread=0.00012,
            )
        )
        assert store.record_risk(
            RiskEvaluation(
                risk_state=RiskState.SAFE, risk_allowed=True, reason_codes=["LIQUIDITY_OK"]
            ),
            "EURUSD",
            "M1",
            ts,
        )
        assert store.record_decision(_make_decision(ts))
        assert store.record_veto(
            "EURUSD", "M1", ts, level=2, rule="RULE_NEWS_SPIKE_FADE", reason="cooldown"
        )
        assert store.record_audit_log("EURUSD", "M1", ts, "DECISION_RECORDED", detail="probe")

        # Every queued row must be durable before the read-back assertions.
        assert store.flush(timeout=5.0) == 0, "the async writer did not drain"

        # Force the DB fallback: empty the rings so query_recent must hit PG.
        for ring in store._rings.values():
            ring.clear()

        decisions = store.query_recent("trade_decisions", 10)
        assert len(decisions) == 1
        d = decisions[0]
        assert d["symbol"] == "EURUSD"
        assert d["timeframe"] == "M1"
        assert d["trade_bias"] == "BULLISH"
        assert d["decision_type"] == "ENTRY"
        assert d["entry_allowed"] in (1, True)
        # JSON columns round-trip as decoded objects (the SQLite path decodes
        # them too — both providers must agree).
        assert d["reason_codes"] == ["ENGULFING", "TREND"]
        assert d["raw_payload"] == {"tick_bid": 1.10040, "tick_ask": 1.10042}
        assert d["computed_payload"] == {"atr_pips": 4.5}

        assert len(store.query_recent("candles", 10)) == 1
        assert len(store.query_recent("candle_closures", 10)) == 1
        assert len(store.query_recent("candle_patterns", 10)) == 1
        assert len(store.query_recent("market_regimes", 10)) == 1
        assert len(store.query_recent("risk_evaluations", 10)) == 1
        assert len(store.query_recent("rule_vetoes", 10)) == 1
        vetoes = store.query_recent("rule_vetoes", 10)
        assert vetoes[0]["veto_rule"] == "RULE_NEWS_SPIKE_FADE"
        assert len(store.query_recent("audit_log", 10)) == 1
        logs = store.query_recent("audit_log", 10)
        assert logs[0]["event"] == "DECISION_RECORDED"
        assert logs[0]["detail"] == "probe"
    finally:
        store.close()
        _clear_domain_backend()


@needs_pg
def test_writes_are_idempotent_under_replay(pg_dsn: str) -> None:
    """Replaying the same bar does not duplicate rows (ON CONFLICT DO NOTHING).

    The SQLite path uses INSERT OR IGNORE for this; the PostgreSQL path must
    offer the identical guarantee or a restarted/replayed engine doubles every
    row.
    """
    _clear_domain_backend()
    cfg = DatabaseConfig.for_postgres(
        domain="candle_intel",
        host="localhost",
        port=5432,
        database=SCRATCH_DB,
        username=PG_USER,
        password_secret=PG_PASSWORD_SECRET_KEY,
    )
    store = CandleIntelStore(db_config=cfg)
    try:
        ts = datetime(2026, 9, 25, 12, 1, 0, tzinfo=UTC)
        for _ in range(3):
            store.record_candle("EURUSD", "M1", ts, 1.10000, 1.10060, 1.09980, 1.10040, volume=1.5)
            store.record_audit_log("EURUSD", "M1", ts, "REPLAY", detail="x")
        assert store.flush(timeout=5.0) == 0
        for ring in store._rings.values():
            ring.clear()
        assert len(store.query_recent("candles", 100)) == 1, "UNIQUE(bar_ts) not enforced on PG"
        # ``audit_log`` declares no unique key, so it legitimately keeps one row
        # per insert — the point is that the count is IDENTICAL to SQLite
        # (INSERT OR IGNORE / ON CONFLICT DO NOTHING behave the same here), not
        # that it is 1.
        assert len(store.query_recent("audit_log", 100)) == 3
    finally:
        store.close()
        _clear_domain_backend()


@needs_pg
def test_batch_flush_groups_statements_into_one_transaction(pg_dsn: str) -> None:
    """A batch of mixed tables commits atomically through the pooled backend."""
    _clear_domain_backend()
    cfg = DatabaseConfig.for_postgres(
        domain="candle_intel",
        host="localhost",
        port=5432,
        database=SCRATCH_DB,
        username=PG_USER,
        password_secret=PG_PASSWORD_SECRET_KEY,
    )
    store = CandleIntelStore(db_config=cfg)
    try:
        base = datetime(2026, 9, 25, 12, 2, 0, tzinfo=UTC)
        for i in range(5):
            ts = base + timedelta(minutes=i)
            assert store.enqueue(
                "audit_log",
                ["bar_ts", "event", "detail", "ts", "symbol", "timeframe"],
                [ts.isoformat(), f"EVT{i}", f"d{i}", ts.isoformat(), "EURUSD", "M1"],
            )
        assert store.flush(timeout=5.0) == 0
        for ring in store._rings.values():
            ring.clear()
        rows = store.query_recent("audit_log", 100)
        assert len(rows) == 5
        assert {r["event"] for r in rows} == {f"EVT{i}" for i in range(5)}
    finally:
        store.close()
        _clear_domain_backend()


# ---------------------------------------------------------------------------
# SQLite stays the default and unchanged
# ---------------------------------------------------------------------------


def test_sqlite_remains_the_default(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No persisted provider + no env => SQLite, unchanged from before the fix."""
    monkeypatch.delenv("NSE_DATABASE__PROVIDER", raising=False)
    monkeypatch.delenv("NSE_DATABASE__PG_DATABASE", raising=False)
    monkeypatch.setenv("NEXUS_SETTINGS_DB", str(tmp_path / "settings.db"))
    monkeypatch.setenv("NEXUS_AUDIT_DB", str(tmp_path / "audit.db"))
    monkeypatch.chdir(tmp_path)

    store = CandleIntelStore()
    try:
        assert store._config.is_sqlite, "the SQLite default was lost"
        assert store._db_path
        store.record_audit_log("EURUSD", "M1", datetime.now(UTC), "SQLITE_DEFAULT", detail="ok")
        assert store.flush(timeout=3.0) == 0
        assert len(store.query_recent("audit_log", 5)) == 1
    finally:
        store.close()


def test_explicit_sqlite_path_is_honored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller-supplied SQLite path wins over any provider resolution."""
    monkeypatch.setenv("NEXUS_SETTINGS_DB", str(tmp_path / "settings.db"))
    monkeypatch.setenv("NEXUS_AUDIT_DB", str(tmp_path / "audit.db"))
    target = tmp_path / "explicit" / "candle_intel.db"

    store = CandleIntelStore(config=CandleIntelligenceConfig(db_path=str(target)))
    try:
        assert store._config.is_sqlite
        assert Path(store._db_path).is_absolute()
        assert Path(store._db_path).parent == target.parent
        store.record_audit_log("EURUSD", "M1", datetime.now(UTC), "EXPLICIT_PATH")
        assert store.flush(timeout=3.0) == 0
        assert len(store.query_recent("audit_log", 5)) == 1
    finally:
        store.close()


@needs_pg
def test_a_postgres_provider_never_silently_falls_back_to_sqlite(
    pg_dsn: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The SQLite default path cannot resurrect itself under a PG provider.

    This is the regression that would reappear if the resolution order were
    flipped back to "SQLite unless explicitly told otherwise": the store would
    resolve SQLite, happily write to a local file, and PostgreSQL would stay
    empty with no signal.
    """
    _clear_domain_backend()
    monkeypatch.setenv("NSE_DATABASE__PROVIDER", "postgresql")
    monkeypatch.setenv("NSE_DATABASE__PG_DATABASE", SCRATCH_DB)
    # Run from a scratch cwd: the SQLite file the old defect created is looked
    # up relative to the process cwd, so this keeps the repo tree clean and
    # makes the "no fallback file" assertion meaningful.
    monkeypatch.chdir(tmp_path)

    store = CandleIntelStore()
    try:
        assert not store._config.is_sqlite
        assert store._config.database == SCRATCH_DB
        store.record_audit_log("EURUSD", "M1", datetime.now(UTC), "NO_FALLBACK")
        assert store.flush(timeout=5.0) == 0
        for ring in store._rings.values():
            ring.clear()
        rows = store.query_recent("audit_log", 10)
        assert len(rows) == 1 and rows[0]["event"] == "NO_FALLBACK"

        # The SQLite file the old defect wrote must NOT have been created —
        # neither at the workspace-anchored default nor in the scratch cwd.
        assert not Path("artifacts/candle_intel.db").exists()
        assert not (tmp_path / "artifacts" / "candle_intel.db").exists()
    finally:
        store.close()
        _clear_domain_backend()


# ---------------------------------------------------------------------------
# the live database is never touched
# ---------------------------------------------------------------------------


@needs_pg
def test_the_live_nexusdb_is_never_written(pg_dsn: str) -> None:
    """This suite's writes land in the scratch database, never in nexusdb."""
    _clear_domain_backend()
    cfg = DatabaseConfig.for_postgres(
        domain="candle_intel",
        host="localhost",
        port=5432,
        database=SCRATCH_DB,
        username=PG_USER,
        password_secret=PG_PASSWORD_SECRET_KEY,
    )
    store = CandleIntelStore(db_config=cfg)
    try:
        store.record_audit_log("EURUSD", "M1", datetime.now(UTC), "ISOLATION")
        assert store.flush(timeout=5.0) == 0
    finally:
        store.close()
        _clear_domain_backend()

    live_dsn = _base_dsn() + " dbname=nexusdb"
    with psycopg.connect(live_dsn, connect_timeout=10) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM information_schema.tables "
                "WHERE table_name = 'candle_patterns'"
            )
            # The live domain may legitimately already hold the candle_intel
            # tables (provisioned by an earlier boot), but it must hold ZERO
            # rows written by this suite.
            cur.execute(
                "SELECT count(*) FROM information_schema.tables WHERE table_name='candle_patterns'"
            )
            has_table = cur.fetchone()[0]
            if has_table:
                cur.execute(
                    "SELECT count(*) FROM candle_patterns WHERE pattern_name = %s",
                    ("ENGULFING",),
                )
                assert cur.fetchone()[0] == 0, (
                    "this suite wrote rows into the live nexusdb — isolation broken"
                )
