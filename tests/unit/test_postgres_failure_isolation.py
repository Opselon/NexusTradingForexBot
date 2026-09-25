"""MISSION S21 — PostgreSQL-failure isolation contract.

When the configured PostgreSQL database is UNREACHABLE the application must
report an explicit database failure and must NEVER silently redirect
operational data into a SQLite file.

This suite points the persistence fabric at a deliberately unreachable
PostgreSQL DSN (``port=5433`` with ``connect_timeout=2`` — nothing listens
there, so every pooled checkout fails after a hard, bounded timeout) and
exercises the three operational write paths:

  1. ``AuditRepository`` write (the trading ledger),
  2. ``ProviderDecisionStore.record`` (the AI-provider decision ledger),
  3. ``provision_domain`` (the fabric's own domain bootstrap).

For each path it asserts the failure is *loud* — an error is logged and/or a
durability counter moves — and that no SQLite file belonging to this run
received the row instead.

Real network IS used, deliberately: a connection attempt to a port nothing
answers is the cheapest faithful reproduction of "PostgreSQL is down". The
cost is bounded and worth stating because it shapes the deadlines:

  * ``provision_domain`` bootstraps the domain schema by replaying one DDL
    statement per pooled checkout (128 statements for the audit domain, 4 for
    ``ai_provider_decisions``);
  * a dead DSN makes every checkout wait one pool timeout before failing, so
    a fresh audit-domain bootstrap takes minutes (~21 min with the default
    pool timeout) while the small ``ai_provider_decisions`` domain resolves
    in seconds;
  * enqueued writes go to the background write plane, whose queue + dead-letter
    sink only fail once the worker flushes.

Every probe therefore carries its own wall-clock assertion so a future
regression that turns this bounded failure into a hang fails loudly instead
of stalling CI. The audit-domain probe is marked ``slow`` (~21 min); the rest
run in the fast gate.
"""

from __future__ import annotations

import logging
import sqlite3
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from nexus_scalp.observability.logging import configure_logging
from nexus_scalp.settings.secret_store import SecureSecretStore

#: A port where nothing listens. The DSN carries the secret so the failure is
#: purely reachability, not authentication — an auth failure would prove much
#: less about the failure path being audited.
DEAD_PORT = 5433
CONNECT_TIMEOUT = 2

#: Wall-clock budget for the full audit-domain provisioning pass against the
#: dead DSN. Schema replay now fast-fails on the first connection error
#: (``apply_schema`` aborts instead of waiting out the pool timeout per
#: statement), so the pass is one connect timeout — seconds, not minutes.
#: Slopleft generous on purpose: CI runners with slow TCP failure still pass.
_AUDIT_PROVISION_BUDGET_SEC = 240.0

configure_logging(log_to_file=False)


def _dead_dsn(database: str = "nexusdb") -> str:
    """A libpq DSN pointing at a port nothing answers, secret included.

    The password is resolved from the OS-backed secret store exactly as the
    production DSN builders do (``build_postgres_url`` / ``_build_dsn``), so
    the only thing this DSN gets wrong is reachability. It is never embedded
    in the source, so nothing has to be redacted here.
    """
    store = SecureSecretStore()
    key = "db.postgresql." + "password"
    secret = store.get_secret(key) or ""
    parts = [
        "host=localhost",
        f"port={DEAD_PORT}",
        f"dbname={database}",
        "user=postgres",
        "password=" + secret,
        f"connect_timeout={CONNECT_TIMEOUT}",
    ]
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def trap_root(tmp_path: Path) -> Path:
    """Every SQLite file this run could touch, under one disposable directory.

    ``NEXUS_AUDIT_DB`` / ``NEXUS_SETTINGS_DB`` / ``NEXUS_DECISIONS_DB`` /
    ``NEXUS_DATA_ROOT`` are the app's own isolation seams; pointing them here
    means a stray SQLite write lands in a file the suite can count, and never
    in the operator's real user-data tree.
    """
    root = tmp_path / "s21_trap"
    for name in ("db", "data"):
        (root / name).mkdir(parents=True, exist_ok=True)
    mon = pytest.MonkeyPatch()
    mon.setenv("NEXUS_AUDIT_DB", str(root / "db" / "audit.db"))
    mon.setenv("NEXUS_SETTINGS_DB", str(root / "db" / "app_settings.db"))
    mon.setenv("NEXUS_DECISIONS_DB", str(root / "db" / "ai_provider_decisions.db"))
    mon.setenv("NEXUS_DATA_ROOT", str(root / "data"))
    yield root
    mon.undo()


@pytest.fixture()
def clean_domain_registry(monkeypatch: pytest.MonkeyPatch) -> None:
    """A private fabric backend registry per test.

    The domain-backend registry is a process-wide singleton; an audit domain
    provisioned by an earlier test would let these probes resolve a *working*
    backend and never touch the dead DSN. Swapping the registry dict (the same
    seam ``test_audit_read_plane_registration_chg0067.py`` uses) removes that
    escape hatch, and monkeypatch restores the real one afterwards.
    """
    from nexus_scalp.database import fabric as fabric_mod

    monkeypatch.setattr(fabric_mod, "_DOMAIN_BACKENDS", {})


@pytest.fixture()
def log_capture():
    """Collect WARNING+ records emitted while a probe runs.

    structlog's DEFAULT ``PrintLoggerFactory`` writes straight to stdout and
    NEVER reaches stdlib handlers — so a plain root handler sees nothing until
    ``configure_logging()`` has run. And once it has, the ConsoleRenderer
    writes through a StreamHandler bound to the captured stdout, so pytest's
    own capture still misses the messages (order-dependent flake, the pattern
    ``test_model_lifecycle_phase10._capture_champion_logs`` solved). Configure
    first (idempotent), then attach a capture handler to root: structlog's
    stdlib routing emits to the logger's own handlers AND propagates to root.
    """
    from nexus_scalp.observability.logging import configure_logging

    records: list[logging.LogRecord] = []

    class _CaptureHandler(logging.Handler):
        def __init__(self) -> None:
            super().__init__(level=logging.DEBUG)
            self.records: list[logging.LogRecord] = records

        def emit(self, record: logging.LogRecord) -> None:
            self.format(record)
            self.records.append(record)

    handler = _CaptureHandler()
    root = logging.getLogger()
    original_level = root.level
    original_handlers = list(root.handlers)
    configure_logging(log_to_file=False)
    root.setLevel(logging.WARNING)
    root.addHandler(handler)
    yield records
    root.removeHandler(handler)
    root.setLevel(original_level)
    root.handlers[:] = original_handlers


def _render(rec: logging.LogRecord) -> str:
    """A log record as text, whichever serializer the pipeline ended up using.

    ``configure_logging()`` installs structlog's ``ConsoleRenderer``, which
    formats the event dict through ``logging`` as a *string* — but a
    structlog ``BytesJsonRenderer``/dict-serializer higher in the stack (or a
    plain stdlib formatter) can instead hand the record a ``dict`` ``message``
    object, whose ``str()`` is the dict repr (``{'event': "...", ...}``),
    not the event text. Matching on ``getMessage()`` alone then silently
    misses every message. Flattening both shapes makes the witness
    serializer-agnostic.
    """
    try:
        text = rec.getMessage()
    except Exception:  # pragma: no cover - a malformed record, not ours to fix
        text = ""
    payload: object = getattr(rec, "msg", None)
    if isinstance(payload, dict):
        # structlog's dict route: the visible text lives under "event".
        text += " " + str(payload.get("event", ""))
    elif not isinstance(payload, str):
        # A non-dict, non-str msg (e.g. a json-serialized object) — its repr
        # can still carry the substring being matched.
        text += " " + repr(payload)
    return text


def _messages(records: list[logging.LogRecord]) -> list[str]:
    return [_render(rec) for rec in records]


# ---------------------------------------------------------------------------
# SQLite witnesses
# ---------------------------------------------------------------------------


def _sqlite_row_count(path: Path, table: str) -> int:
    """Rows in <table>; 0 when the file or table does not exist."""
    if not path.exists() or path.stat().st_size == 0:
        return 0
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.OperationalError:
        return 0
    try:
        try:
            return int(con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        except sqlite3.OperationalError:
            return 0
    finally:
        con.close()


def _all_sqlite_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.db") if p.is_file())


def _operational_rows(root: Path) -> dict[str, int]:
    """Count of operational rows in every SQLite file of the run."""
    out: dict[str, int] = {}
    for path in _all_sqlite_files(root):
        for table in (
            "audit_signals",
            "audit_ledger",
            "audit_dead_letter",
            "ai_provider_decisions",
        ):
            n = _sqlite_row_count(path, table)
            if n:
                out[f"{path.name}:{table}"] = n
    return out


# ===========================================================================
# 1. ProviderDecisionStore — the AI-provider decision ledger
# ===========================================================================


def test_provider_decision_store_records_failure_not_sqlite(
    trap_root: Path,
    clean_domain_registry: None,
    log_capture: list[logging.LogRecord],
) -> None:
    """``ProviderDecisionStore.record`` must never fall back to SQLite.

    Given a ``dsn`` the store provisions the ``ai_provider_decisions`` domain
    and writes through the pooled backend. With the DSN dead, the four schema
    statements each fail at checkout, the write is refused and announced, and
    nothing lands in a SQLite file — the row is neither persisted nor faked.
    """
    from nexus_scalp.ai_providers.store import DECISION_DOMAIN, ProviderDecisionStore

    t0 = time.monotonic()
    store = ProviderDecisionStore(dsn=_dead_dsn())
    elapsed = time.monotonic() - t0
    try:
        assert store._sqlite is None, "a dsn must never open a SQLite connection"
        assert store._dsn is not None, "the dsn must be retained for the write path"
        assert not hasattr(store, "_mem"), "a dsn must not degrade to in-memory recording"

        store.record(
            {
                "decision_id": "s21-decision-down-0001",
                "created_at": datetime.now(UTC).isoformat(),
                "decision_mode": "test",
                "active_provider": "system_one",
                "active_model": "probe",
                "final_action": "HOLD",
                "fallback_used": False,
                "providers_used": ["system_one"],
                "providers_failed": [],
                "decision": {"p_hold": 0.7, "confidence": 0.7},
                "policy": {"winner": "HOLD", "scores": {"HOLD": 0.7}},
                "risk": {"allowed": True, "rejections": []},
                "versions": {"template": "v1", "policy": "v1", "contract": "v1"},
                "latency_ms": 1.0,
                "symbol": "XAUUSD",
                "is_test_data": 1,
            }
        )

        # The ledger must not claim the row landed on the dead provider.
        assert store.recent(limit=5) == [], "no row should be readable from a dead provider"

        # The loss was announced, not swallowed: provisioning failed and/or the
        # write failed — whichever fired, the operator must see it. Both
        # surfaces are matched because either alone is enough (a dead DSN can
        # fail at checkout during provisioning or at write time, and the
        # store's null-backend path announces the loss with the same prefix
        # as its write failure, so the ledger never drops a row in silence).
        messages = _messages(log_capture)
        assert any(
            ("domain provisioning failed" in m and DECISION_DOMAIN in m)
            or ("failed to record decision" in m)
            or ("decision write failed" in m)
            for m in messages
        ), f"the store must log the write failure explicitly; saw {messages}"

        # And no operational row escaped into a SQLite file.
        assert _operational_rows(trap_root) == {}, "a dead provider must not push rows into SQLite"
    finally:
        store.close()

    # Bounded: 4 statements x the pool timeout at most, never a hang.
    assert elapsed < 300.0, f"decision-store provisioning hung for {elapsed:.1f}s"


# ===========================================================================
# 2. Fabric domain provisioning
# ===========================================================================


def test_fabric_provisioning_reports_unreachable_provider(
    clean_domain_registry: None,
    log_capture: list[logging.LogRecord],
) -> None:
    """``provision_domain`` must fail loudly, never create a SQLite substitute.

    Schema bootstrap happens on the pooled write backend; with the DSN dead
    each translated DDL statement fails at checkout. Provisioning records the
    per-statement errors and returns a backend that cannot write — never
    silently swaps in a SQLite file for the domain it was asked to provision.

    Uses the small ``ai_provider_decisions`` domain (4 statements) so the
    whole failure resolves in seconds.
    """
    from nexus_scalp.database.fabric import provision_domain

    domain = "ai_provider_decisions"
    t0 = time.monotonic()
    try:
        backend = provision_domain(domain, _dead_dsn(), min_size=1, max_size=4)
    except BaseException as exc:
        backend = None
        pytest.fail(f"provision_domain must surface the failure in the log, not raise: {exc!r}")
    elapsed = time.monotonic() - t0

    try:
        # The backend object exists but every use must raise — the failure is
        # surfaced at write time, never hidden behind a silent no-op.
        assert backend is not None
        with pytest.raises(Exception):  # noqa: B017 - any provider error
            backend.execute("SELECT 1")

        # And the schema bootstrap said exactly what went wrong.
        messages = _messages(log_capture)
        assert any("statement failed" in m or "couldn't get a connection" in m for m in messages), (
            f"provisioning must log per-statement errors; saw {messages}"
        )
    finally:
        close = getattr(backend, "close", None)
        if callable(close):
            close()

    # Bounded: 4 statements x the pool timeout at most.
    assert elapsed < 300.0, f"provisioning against a dead DSN hung for {elapsed:.1f}s"
    _ = log_capture


# ===========================================================================
# 3. AuditRepository — the trading ledger (slow: full domain bootstrap)
# ===========================================================================


@pytest.mark.slow()
def test_audit_repository_write_fails_loudly_without_sqlite_escape(
    trap_root: Path,
    clean_domain_registry: None,
    log_capture: list[logging.LogRecord],
) -> None:
    """A signal written while PostgreSQL is down must not reach SQLite.

    ``AuditRepository`` builds its write plane through ``provision_domain``,
    which replays the audit domain's 128 DDL statements — one pooled checkout
    each — against the configured DSN. Every checkout fails after the pool
    timeout, so construction is slow but bounded (minutes, not a hang) and
    surfaces a per-statement ERROR per failure. The enqueued row is then
    dead-lettered with a CRITICAL log: never silently dropped, and never
    redirected into the SQLite file the isolation seam points at.
    """
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.domain.models import ActionType, TradeProposal

    t0 = time.monotonic()
    repo = AuditRepository(db_url=_dead_dsn())
    elapsed = time.monotonic() - t0
    try:
        assert repo._is_sqlite is False, "a postgres DSN must not be read back as sqlite"
        assert repo._write_plane is not None

        proposal = TradeProposal(
            request_id="s21-audit-down-0001",
            symbol="XAUUSD",
            generated_at=datetime.now(UTC),
            action=ActionType.BUY,
            confidence=0.62,
            proposed_entry=2000.0,
            stop_loss=1995.0,
            take_profit=2010.0,
            risk_reward_ratio=2.0,
            reason_code="MODEL_SIGNAL",
            buy_probability=0.62,
            sell_probability=0.28,
            no_trade_probability=0.10,
            risk_checks={"allowed": True},
            risk_allowed=True,
        )
        t1 = time.monotonic()
        repo.log_signal(proposal)
        # Enqueueing is queue-local, so it returns immediately; the failure
        # surfaces when the plane's worker flushes.
        assert time.monotonic() - t1 < 60.0, "log_signal must not block on the dead provider"

        repo.flush(timeout_sec=90.0)

        # The failure is observable somewhere: a durability counter moved, or
        # the worker/dead-letter path announced the loss.
        failed = (
            repo.financial_events_failed
            + repo.audit_batch_failures
            + repo.audit_dead_letter_rows
            + repo.financial_overflow_failed
        )
        messages = _messages(log_capture)
        assert failed > 0 or any(
            "Audit batch insert failed" in m or "DEAD-LETTER" in m for m in messages
        ), f"an unreachable PostgreSQL must produce a visible failure signal; saw {messages}"

        # No operational row leaked into any SQLite file of this run — this is
        # the mission's core invariant.
        assert _operational_rows(trap_root) == {}, (
            f"operational rows escaped into SQLite while PostgreSQL was down: "
            f"{_operational_rows(trap_root)}"
        )
    finally:
        repo.close()

    # Bounded: the provisioning pass dominates (statements x pool timeout).
    assert elapsed < _AUDIT_PROVISION_BUDGET_SEC, (
        f"audit provisioning against a dead DSN hung for {elapsed:.1f}s"
    )


# ===========================================================================
# 4. Refusal paths that must stay loud, not silently degrade
# ===========================================================================


def test_unprovisioned_domain_refuses_to_write_instead_of_using_sqlite(
    trap_root: Path,
    clean_domain_registry: None,
) -> None:
    """A PostgreSQL-configured domain with no backend must refuse writes.

    The write plane's dead-letter sink is what keeps failure evidence durable
    on a PostgreSQL domain. When the domain has no backend (PostgreSQL never
    came up) the sink must report failure rather than fake a successful write,
    and must never substitute a SQLite file for the domain it was asked to
    persist to.
    """
    from nexus_scalp.adapters.database.audit_repository import AuditRepository

    repo = object.__new__(AuditRepository)
    repo.__dict__["_is_sqlite"] = False
    repo.__dict__["_db_url"] = _dead_dsn()
    repo.__dict__["_db_path"] = ""
    sink = repo._dead_letter_write_sink()

    # The sink exists but cannot succeed against a domain with no backend —
    # it reports failure instead of faking a write.
    assert sink is not None, "an unprovisioned domain must still have somewhere to put failures"
    assert sink("INSERT INTO audit_dead_letter (x) VALUES (?)", (1,)) is False

    # Nothing was written anywhere on disk for this domain.
    assert not (trap_root / "db" / "audit.db").exists()


def test_provider_decision_store_without_dsn_stays_in_memory_and_logs(
    trap_root: Path, log_capture: list[logging.LogRecord]
) -> None:
    """Neither argument given: the documented in-memory degradation, logged.

    This is NOT a SQLite fallback — the store records why durable storage is
    absent and keeps the decisions in-process, which is the contract the
    orchestrator boots under. The message is what makes it observable.
    """
    from nexus_scalp.ai_providers.store import ProviderDecisionStore

    store = ProviderDecisionStore()
    try:
        assert store._sqlite is None, "no dsn must not open a SQLite file"
        assert store._dsn is None
        assert hasattr(store, "_mem"), "the documented degradation is in-memory"
        assert any("in-memory" in m for m in _messages(log_capture)), (
            "the store must announce that durable storage is absent"
        )
    finally:
        store.close()
