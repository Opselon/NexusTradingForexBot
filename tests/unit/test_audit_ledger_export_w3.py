"""A10 audit-ledger export validator (wave 3) — tests + reusable validator.

PURPOSE
-------
This module ships a reusable, pure-stdlib validator for the REAL operator
export of the production 158-trade paper ledger (TASK-EXIT-LEDGER-EXPORT):
the ``audit_experience_outcomes`` table from the prod Windows host's
``artifacts/audit.db``.  When the operator export arrives, run
:func:`validate_audit_export` on it BEFORE any replay/analysis consumes it:

    python tests/unit/test_audit_ledger_export_w3.py <export.db-or-.sql> \
        --source-host PROD-WINDOWS-HOST --expected-rows 158

WHAT THE FIXTURES ARE
---------------------
Every sqlite database / dump built in this test file is a tiny SYNTHETIC
fixture, created in ``tmp_path`` purely to exercise the validator's own
logic (well-formed pass, catch empty table, catch missing column, catch
duplicate ids, catch future timestamps, ...).  SYNTHETIC FIXTURES ARE NEVER
LEDGER EVIDENCE and share no bytes with the production database.

VALIDATOR GUARANTEES
--------------------
* pure functions, stdlib only, no network, no repo imports;
* opens sqlite sources READ-ONLY (``file:...?mode=ro`` URI) so pointing it
  at the real ``artifacts/audit.db`` cannot mutate it;
* ``.sql`` dumps (``.mode insert`` / ``.dump`` style) are loaded into an
  in-memory scratch database, never written back to disk;
* never fabricates or imputes ledger values — it only reports what is there.

Column contract encoded below mirrors two sources (verified 2026-09-09):
* repo schema: ``src/nexus_scalp/adapters/database/audit_repository.py``
  CREATE TABLE audit_experience_outcomes (lines 1004-1031), cross-checked by
  PRAGMA against the local artifacts/audit.db (0 rows, same 26 columns);
* replay harness: ``scripts/forensics/exit_policy_counterfactual.py``
  CANDIDATE_COLUMNS (lines 124-133) + load_trades hard requirements
  (realized_r, mfe_r, mae_r after candidate fallback; ``WHERE is_closed = 1``).
"""

from __future__ import annotations

import itertools
import json
import os
import re
import sqlite3
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Contract constants (documented copies of the two sources above)
# ---------------------------------------------------------------------------
TABLE = "audit_experience_outcomes"

#: Exact columns the repo creates for the outcomes table (order as committed).
REPO_SCHEMA_COLUMNS: tuple[str, ...] = (
    "id",
    "idempotency_key",
    "execution_id",
    "outcome_timestamp",
    "is_executed",
    "is_closed",
    "exit_reason",
    "realized_pnl_usd",
    "realized_r_multiple",
    "approved_volume",
    "mae_points",
    "mfe_points",
    "mae_usd",
    "mfe_usd",
    "mae_r",
    "mfe_r",
    "holding_duration_seconds",
    "slippage_points",
    "execution_latency_ms",
    "strategy_quality",
    "entry_quality",
    "execution_quality",
    "management_quality",
    "exit_quality",
    "behavioral_flags",
    "payload",
)

#: Hard-required by the replay harness: at least one candidate per logical
#: field MUST resolve to a physical column, else the harness fail-closes
#: (NO_DATA) or the ``WHERE is_closed = 1`` filter cannot even run.
REPLAY_REQUIRED_CANDIDATES: dict[str, tuple[str, ...]] = {
    "realized_r": ("realized_r_multiple", "realized_r"),
    "mfe_r": ("mfe_r",),
    "mae_r": ("mae_r",),
    "is_closed": ("is_closed",),
}

#: Optional harness inputs with graceful fallbacks (harness substitutes
#: ''/NULL when absent).  Their absence is reported as a WARNING, not an
#: error, so a faithful repo-schema export still validates.
REPLAY_OPTIONAL_CANDIDATES: dict[str, tuple[str, ...]] = {
    "exit_reason": ("exit_reason",),
    "exit_mechanism": ("exit_mechanism", "exit_mechanism_raw", "mechanism"),
    "entry_price": ("entry_price", "price_open", "open_price"),
    "planned_risk": ("planned_risk_usd", "risk_usd", "planned_risk", "risk_amount_usd"),
    "execution_id": ("execution_id", "ticket", "position_id"),
}

#: Columns the missing-value census always covers (plus any resolved optional).
CENSUS_COLUMNS: tuple[str, ...] = (
    "idempotency_key",
    "execution_id",
    "outcome_timestamp",
    "is_closed",
    "exit_reason",
    "realized_pnl_usd",
    "realized_r_multiple",
    "mae_r",
    "mfe_r",
)

#: Known drift the operator export is ALLOWED to carry without failing
#: (documented so unexpected extras stand out instead of hiding in noise).
KNOWN_EXTRA_COLUMNS: frozenset[str] = frozenset(
    {
        "exit_mechanism",
        "exit_mechanism_raw",
        "planned_risk_usd",
        "risk_usd",
        "entry_price",
        "open_price",
        "ticket",
        "position_id",
    }
)


# ---------------------------------------------------------------------------
# Validator (pure functions; the only I/O is READ-ONLY opens of the source)
# ---------------------------------------------------------------------------
def _looks_like_sql_dump(path: Path) -> bool:
    """True when the file is a text SQL export rather than a sqlite db file."""
    with open(path, "rb") as fh:
        head = fh.read(16)
    if head.startswith(b"SQLite format 3"):
        return False
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    return "INSERT INTO" in text.upper() or "BEGIN TRANSACTION" in text.upper()


def _connect_readonly_sqlite(path: Path) -> sqlite3.Connection:
    """Open a sqlite db strictly read-only via URI mode (cannot create/mutate)."""
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    return sqlite3.connect(uri, uri=True)


def _load_sql_dump_in_memory(path: Path) -> sqlite3.Connection:
    """Load a .sql INSERT dump into an in-memory scratch db (read-only usage).

    A real sqlite3 CLI ``.mode insert``/``.dump`` export contains the CREATE
    TABLE statement; a bare INSERT stream (sqlite3's insert mode omits DDL)
    is still loadable because the validator creates a compatible empty table
    from REPO_SCHEMA_COLUMNS first.  Any SQL error raises sqlite3.Error so
    the caller fail-closes with dump_unloadable.
    """
    text = path.read_text(encoding="utf-8", errors="strict")
    conn = sqlite3.connect(":memory:")
    has_ddl = re.search(
        r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[\"'\[.]?" + re.escape(TABLE),
        text,
        re.IGNORECASE,
    )
    if not has_ddl:
        cols = ",\n    ".join(_ddl_for(c) for c in REPO_SCHEMA_COLUMNS)
        conn.executescript(f"CREATE TABLE {TABLE} (\n    {cols}\n);")
    conn.executescript(text)
    if not _table_columns(conn, TABLE):
        raise sqlite3.OperationalError(f"dump loaded but {TABLE} missing/uncreatable")
    return conn


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def _err(code: str, detail: str) -> dict[str, str]:
    return {"code": code, "detail": detail}


def _resolve_candidates(
    present: set[str],
    mapping: dict[str, tuple[str, ...]],
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Map logical harness field -> physical column, mirroring the harness's
    candidate fallback order exactly (first candidate present wins)."""
    resolved: dict[str, str] = {}
    unresolved: dict[str, list[str]] = {}
    for logical, candidates in mapping.items():
        hit = next((c for c in candidates if c in present), None)
        if hit is not None:
            resolved[logical] = hit
        else:
            unresolved[logical] = list(candidates)
    return resolved, unresolved


def _parse_ts(value: Any) -> datetime | None:
    """Parse ISO-8601-ish timestamps ('Z' suffix or naive treated as UTC)."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        ts = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts


def validate_audit_export(
    db_path: str | os.PathLike[str],
    *,
    source_host: str,
    now_utc: datetime | None = None,
    future_horizon_days: int = 7,
    expected_row_count: int | None = None,
    table: str = TABLE,
) -> dict[str, Any]:
    """Validate a future operator export of ``audit_experience_outcomes``.

    Parameters
    ----------
    db_path : path to the export — either a sqlite ``.db`` file (opened
        READ-ONLY via URI mode) or a ``.sql`` INSERT dump (loaded into an
        in-memory scratch db).
    source_host : provenance label for where the export came from (e.g.
        ``"PROD-WINDOWS-HOST"``).  Required; recorded in the result header.
    now_utc : reference "now" for the future-timestamp horizon (defaults to
        real UTC now; pass a fixed value for reproducible runs).
    future_horizon_days : timestamps more than this far in the future fail.
    expected_row_count : if given, a mismatch is reported as a WARNING
        (never fabricated/adjusted — the actual count is authoritative).

    Returns a JSON-serializable report dict; ``ok`` is True iff ``errors``
    is empty.  Blocking errors: missing_provenance, file_unreadable,
    dump_unloadable, table_missing, missing_columns, empty_table,
    duplicate_ids, future_timestamps, unparseable_timestamps,
    no_replayable_rows.
    """
    now_utc = now_utc or datetime.now(UTC)
    horizon = now_utc + timedelta(days=future_horizon_days)
    errors: list[dict[str, str]] = []
    warnings: list[dict[str, str]] = []
    checks: dict[str, Any] = {}

    if not source_host or not str(source_host).strip():
        errors.append(
            _err("missing_provenance", "source_host label is required for the export header")
        )

    path = Path(db_path)
    if not path.is_file():
        return {
            "source_host": str(source_host),
            "db_path": str(path),
            "format": "unknown",
            "ok": False,
            "errors": [*errors, _err("file_unreadable", f"{path} is not a file")],
            "warnings": warnings,
            "checks": checks,
            "provenance": {"source_host": str(source_host), "validated_utc": now_utc.isoformat()},
        }

    fmt = "sql_dump" if _looks_like_sql_dump(path) else "sqlite"
    try:
        conn = (
            _load_sql_dump_in_memory(path) if fmt == "sql_dump" else _connect_readonly_sqlite(path)
        )
    except (sqlite3.Error, UnicodeDecodeError, ValueError) as exc:
        return {
            "source_host": str(source_host),
            "db_path": str(path),
            "format": fmt,
            "ok": False,
            "errors": [*errors, _err("dump_unloadable", f"{type(exc).__name__}: {exc}")],
            "warnings": warnings,
            "checks": checks,
            "provenance": {"source_host": str(source_host), "validated_utc": now_utc.isoformat()},
        }

    try:
        try:
            tables = {
                r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        except sqlite3.DatabaseError as exc:
            errors.append(
                _err(
                    "dump_unloadable",
                    f"file is neither a valid sqlite db nor a SQL dump: "
                    f"{type(exc).__name__}: {exc}",
                )
            )
            return _finalize(source_host, path, fmt, errors, warnings, checks, now_utc)
        if table not in tables:
            errors.append(
                _err("table_missing", f"{table} not found; tables={sorted(tables)[:12]}...")
            )
            return _finalize(source_host, path, fmt, errors, warnings, checks, now_utc)

        columns = _table_columns(conn, table)
        present = set(columns)
        checks["columns_present"] = columns
        checks["extra_columns_vs_repo_schema"] = sorted(present - set(REPO_SCHEMA_COLUMNS))
        checks["unexpected_extra_columns"] = sorted(
            present - set(REPO_SCHEMA_COLUMNS) - KNOWN_EXTRA_COLUMNS
        )

        resolved_req, unresolved_req = _resolve_candidates(present, REPLAY_REQUIRED_CANDIDATES)
        missing_hard = sorted(unresolved_req)
        if missing_hard:
            errors.append(
                _err(
                    "missing_columns",
                    f"no candidate column found for {missing_hard}; have {sorted(present)}",
                )
            )
        resolved_opt, unresolved_opt = _resolve_candidates(present, REPLAY_OPTIONAL_CANDIDATES)
        for logical, candidates in sorted(unresolved_opt.items()):
            warnings.append(
                _err(
                    "optional_column_absent",
                    f"'{logical}' has no physical column (tried {candidates}); "
                    f"replay harness will fall back to its ''/NULL default",
                )
            )
        checks["resolved_replay_columns"] = {**resolved_req, **resolved_opt}
        checks["unresolved_replay_fields"] = {**unresolved_req, **unresolved_opt}

        row_count = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        checks["row_count"] = row_count
        if row_count == 0:
            errors.append(_err("empty_table", f"{table} has 0 rows (harness would exit NO_DATA)"))
            return _finalize(source_host, path, fmt, errors, warnings, checks, now_utc)
        if expected_row_count is not None and row_count != expected_row_count:
            warnings.append(
                _err(
                    "row_count_mismatch",
                    f"expected ~{expected_row_count} rows, export has {row_count} "
                    f"(actual count is authoritative)",
                )
            )

        # --- trade-id uniqueness -------------------------------------------
        dup_keys = conn.execute(
            f"SELECT idempotency_key, COUNT(*) n FROM {table} "
            f"WHERE idempotency_key IS NOT NULL AND idempotency_key != '' "
            f"GROUP BY idempotency_key HAVING COUNT(*) > 1 LIMIT 20"
        ).fetchall()
        checks["duplicate_idempotency_keys"] = [{"idempotency_key": k, "n": n} for k, n in dup_keys]
        if dup_keys:
            errors.append(
                _err(
                    "duplicate_ids",
                    f"{sum(n for _, n in dup_keys)} extra rows share "
                    f"a non-empty idempotency_key with another row "
                    f"(first: {dup_keys[0][0]!r})",
                )
            )
        empty_keys = conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE idempotency_key IS NULL OR idempotency_key = ''"
        ).fetchone()[0]
        checks["rows_with_empty_idempotency_key"] = empty_keys
        if empty_keys:
            errors.append(
                _err(
                    "empty_idempotency_keys",
                    f"{empty_keys} rows have an empty idempotency_key "
                    f"(UNIQUE anchor is NOT NULL in the repo schema)",
                )
            )

        dup_exec = conn.execute(
            f"SELECT execution_id, COUNT(*) n FROM {table} "
            f"WHERE execution_id IS NOT NULL AND execution_id != '' "
            f"GROUP BY execution_id HAVING COUNT(*) > 1 LIMIT 20"
        ).fetchall()
        checks["duplicate_execution_ids"] = [{"execution_id": k, "n": n} for k, n in dup_exec]
        if dup_exec:
            warnings.append(
                _err(
                    "duplicate_execution_ids",
                    f"broker ticket(s) appear on multiple rows (first: "
                    f"{dup_exec[0][0]!r}); join to audit_ledger.ticket will fan out",
                )
            )

        # --- missing-value census ------------------------------------------
        census: dict[str, int] = {}
        tracked = [c for c in CENSUS_COLUMNS if c in present]
        tracked += [c for c in resolved_opt.values() if c not in tracked]
        for col in tracked:
            n_missing = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE {col} IS NULL OR {col} = ''"
            ).fetchone()[0]
            if n_missing:
                census[col] = n_missing
        checks["missing_value_census"] = census

        closed = conn.execute(f"SELECT COUNT(*) FROM {table} WHERE is_closed = 1").fetchone()[0]
        checks["rows_closed"] = closed
        checks["rows_open_or_ambiguous"] = row_count - closed
        if "mae_r" in present and "mfe_r" in present:
            replayable = conn.execute(
                f"SELECT COUNT(*) FROM {table} WHERE is_closed = 1 AND "
                f"{resolved_req.get('realized_r', 'realized_r_multiple')} IS NOT NULL "
                f"AND mfe_r IS NOT NULL AND mae_r IS NOT NULL"
            ).fetchone()[0]
            checks["replayable_rows"] = replayable
            if replayable == 0:
                errors.append(
                    _err(
                        "no_replayable_rows",
                        "0 closed rows carry a complete (realized_r, mfe_r, mae_r) "
                        "triple; the harness would skip every row",
                    )
                )

        # --- timestamp sanity ----------------------------------------------
        bad_ts = future = 0
        ts_values: list[tuple[int, datetime]] = []
        for rowid, raw in conn.execute(
            f"SELECT rowid, outcome_timestamp FROM {table} ORDER BY rowid"
        ):
            ts = _parse_ts(raw)
            if ts is None:
                bad_ts += 1
                continue
            ts_values.append((rowid, ts))
            if ts > horizon:
                future += 1
        checks["unparseable_timestamps"] = bad_ts
        checks["future_timestamps"] = future
        checks["timestamp_min"] = (
            min((t for _, t in ts_values), default=None).isoformat() if ts_values else None
        )
        checks["timestamp_max"] = (
            max((t for _, t in ts_values), default=None).isoformat() if ts_values else None
        )
        if bad_ts:
            errors.append(
                _err(
                    "unparseable_timestamps",
                    f"{bad_ts} outcome_timestamp values are not ISO-8601 parseable",
                )
            )
        if future:
            errors.append(
                _err(
                    "future_timestamps",
                    f"{future} outcome_timestamp values lie more than "
                    f"{future_horizon_days}d in the future "
                    f"(horizon {horizon.isoformat()})",
                )
            )

        out_of_order = sum(1 for a, b in itertools.pairwise(ts_values) if b[1] < a[1])
        checks["out_of_order_adjacent_pairs"] = out_of_order
        if out_of_order:
            warnings.append(
                _err(
                    "timestamps_out_of_order",
                    f"{out_of_order} adjacent rowid-ordered pairs have a decreasing "
                    f"outcome_timestamp (unexpected for an append-only ledger, "
                    f"non-blocking)",
                )
            )
    finally:
        conn.close()

    return _finalize(source_host, path, fmt, errors, warnings, checks, now_utc)


def _finalize(source_host, path, fmt, errors, warnings, checks, now_utc):
    return {
        "source_host": str(source_host),
        "db_path": str(path),
        "format": fmt,
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "checks": checks,
        "provenance": {"source_host": str(source_host), "validated_utc": now_utc.isoformat()},
    }


# ---------------------------------------------------------------------------
# SYNTHETIC fixtures (test the validator itself ONLY — never ledger evidence)
# ---------------------------------------------------------------------------
NOW = datetime(2026, 9, 9, 12, 0, 0, tzinfo=UTC)
BASE_TS = "2026-09-0"


def _default_row(i: int) -> dict[str, Any]:
    return {
        "idempotency_key": f"SYN-A10-{i:04d}",
        "execution_id": f"90000{i}",
        "outcome_timestamp": f"{BASE_TS}{i + 1}T10:0{i}:00+00:00",
        "is_executed": 1,
        "is_closed": 1,
        "exit_reason": "BREAK_EVEN_SL_HIT",
        "realized_pnl_usd": 1.5 * (-1) ** i,
        "realized_r_multiple": 0.14 * (-1) ** i,
        "mae_r": -0.4,
        "mfe_r": 0.5,
        "payload": json.dumps({"synthetic": True, "fixture": "a10"}),
    }


def _build_db(
    tmp_path: Path,
    rows: list[dict[str, Any]],
    *,
    drop_columns: tuple[str, ...] = (),
    extra_columns: tuple[tuple[str, str], ...] = (),
    create_table: bool = True,
    enforce_key_uniqueness: bool = True,
    filename: str = "synthetic_audit.db",
) -> Path:
    """Build a SYNTHETIC audit.db fixture in tmp_path (validator test only)."""
    db = tmp_path / filename
    conn = sqlite3.connect(db)
    if create_table:
        all_cols = [c for c in REPO_SCHEMA_COLUMNS if c not in drop_columns]
        ddl_cols = []
        for name in all_cols:
            ddl = _ddl_for(name)
            # Tests that must INSERT a duplicate idempotency_key need the
            # UNIQUE constraint relaxed at fixture level; the validator still
            # detects the duplicates that the real export could carry.
            if name == "idempotency_key" and not enforce_key_uniqueness:
                ddl = "idempotency_key TEXT NOT NULL"
            ddl_cols.append(ddl)
        for name, decl in extra_columns:
            ddl_cols.append(f"{name} {decl}")
        conn.execute(f"CREATE TABLE {TABLE} (\n    " + ",\n    ".join(ddl_cols) + "\n)")
    for i, overrides in enumerate(rows):
        row = {**_default_row(i), **overrides}
        row = {
            k: v
            for k, v in row.items()
            if k
            in {c for c in REPO_SCHEMA_COLUMNS if c not in drop_columns}
            | {name for name, _ in extra_columns}
        }
        names = ", ".join(row)
        qs = ", ".join("?" for _ in row)
        conn.execute(f"INSERT INTO {TABLE} ({names}) VALUES ({qs})", tuple(row.values()))
    conn.commit()
    conn.close()
    return db


def _ddl_for(name: str) -> str:
    if name == "id":
        return "id INTEGER PRIMARY KEY AUTOINCREMENT"
    if name == "idempotency_key":
        return "idempotency_key TEXT UNIQUE NOT NULL"
    if name in ("outcome_timestamp", "payload"):
        return f"{name} TEXT NOT NULL"
    if name in ("is_executed", "is_closed"):
        return f"{name} INTEGER DEFAULT 0"
    if name in ("exit_reason", "behavioral_flags", "execution_id"):
        return f"{name} TEXT DEFAULT ''"
    return f"{name} REAL DEFAULT 0.0"


# ---------------------------------------------------------------------------
# Tests — SYNTHETIC fixtures exercising the validator (not ledger evidence)
# ---------------------------------------------------------------------------
class TestWellFormedExport:
    def test_repo_schema_fixture_passes(self, tmp_path):
        db = _build_db(tmp_path, [{} for _ in range(3)])
        rep = validate_audit_export(
            db, source_host="SYNTHETIC-TEST-HOST", now_utc=NOW, expected_row_count=3
        )
        assert rep["ok"], rep["errors"]
        assert rep["errors"] == []
        assert rep["provenance"]["source_host"] == "SYNTHETIC-TEST-HOST"
        assert rep["checks"]["row_count"] == 3
        assert rep["checks"]["replayable_rows"] == 3
        assert rep["checks"]["rows_closed"] == 3
        assert rep["format"] == "sqlite"
        # repo schema resolves every hard requirement + exit_reason/execution_id
        assert rep["checks"]["resolved_replay_columns"]["realized_r"] == "realized_r_multiple"
        assert rep["checks"]["resolved_replay_columns"]["mfe_r"] == "mfe_r"
        assert rep["checks"]["resolved_replay_columns"]["mae_r"] == "mae_r"
        # exit_mechanism/planned_risk genuinely absent from the repo schema ->
        # warning, never error (harness falls back to ''/NULL)
        codes = {w["code"] for w in rep["warnings"]}
        assert "optional_column_absent" in codes
        assert not rep["checks"]["unexpected_extra_columns"]

    def test_enriched_fixture_resolves_optional_columns(self, tmp_path):
        db = _build_db(
            tmp_path,
            [{} for _ in range(2)],
            extra_columns=(
                ("exit_mechanism", "TEXT DEFAULT ''"),
                ("planned_risk_usd", "REAL DEFAULT 0.0"),
            ),
        )
        rep = validate_audit_export(db, source_host="SYNTHETIC-TEST-HOST", now_utc=NOW)
        assert rep["ok"], rep["errors"]
        assert rep["checks"]["resolved_replay_columns"]["exit_mechanism"] == "exit_mechanism"
        assert rep["checks"]["resolved_replay_columns"]["planned_risk"] == "planned_risk_usd"
        unresolved = rep["checks"]["unresolved_replay_fields"]
        assert "exit_mechanism" not in unresolved
        assert "planned_risk" not in unresolved


class TestFailures:
    def test_empty_table_fails(self, tmp_path):
        db = _build_db(tmp_path, [])
        rep = validate_audit_export(db, source_host="SYN", now_utc=NOW)
        assert not rep["ok"]
        assert any(e["code"] == "empty_table" for e in rep["errors"])
        assert rep["checks"]["row_count"] == 0

    def test_missing_table_fails(self, tmp_path):
        db = tmp_path / "synthetic_audit.db"
        conn = sqlite3.connect(db)
        conn.execute("CREATE TABLE other_table (x INTEGER)")
        conn.commit()
        conn.close()
        rep = validate_audit_export(db, source_host="SYN", now_utc=NOW)
        assert not rep["ok"]
        assert any(e["code"] == "table_missing" for e in rep["errors"])

    def test_missing_column_fails(self, tmp_path):
        db = _build_db(tmp_path, [{} for _ in range(2)], drop_columns=("mfe_r",))
        rep = validate_audit_export(db, source_host="SYN", now_utc=NOW)
        assert not rep["ok"]
        missing = [e for e in rep["errors"] if e["code"] == "missing_columns"]
        assert missing and "mfe_r" in missing[0]["detail"]

    def test_duplicate_idempotency_keys_fail(self, tmp_path):
        db = _build_db(
            tmp_path, [{}, {}, {"idempotency_key": "SYN-A10-0000"}], enforce_key_uniqueness=False
        )
        rep = validate_audit_export(db, source_host="SYN", now_utc=NOW)
        assert not rep["ok"]
        dup = [e for e in rep["errors"] if e["code"] == "duplicate_ids"]
        assert dup and "SYN-A10-0000" in dup[0]["detail"]
        assert rep["checks"]["duplicate_idempotency_keys"][0]["n"] == 2

    def test_empty_idempotency_key_fails(self, tmp_path):
        db = _build_db(tmp_path, [{}, {"idempotency_key": ""}])
        rep = validate_audit_export(db, source_host="SYN", now_utc=NOW)
        assert not rep["ok"]
        assert any(e["code"] == "empty_idempotency_keys" for e in rep["errors"])

    def test_future_timestamp_fails(self, tmp_path):
        db = _build_db(tmp_path, [{}, {"outcome_timestamp": "2099-01-01T00:00:00+00:00"}])
        rep = validate_audit_export(db, source_host="SYN", now_utc=NOW)
        assert not rep["ok"]
        fut = [e for e in rep["errors"] if e["code"] == "future_timestamps"]
        assert fut and fut[0]["detail"].startswith("1 ")

    def test_unparseable_timestamp_fails(self, tmp_path):
        db = _build_db(tmp_path, [{}, {"outcome_timestamp": "not-a-date"}])
        rep = validate_audit_export(db, source_host="SYN", now_utc=NOW)
        assert not rep["ok"]
        assert any(e["code"] == "unparseable_timestamps" for e in rep["errors"])

    def test_no_replayable_rows_fails(self, tmp_path):
        # every closed row lacks the (realized_r, mfe_r, mae_r) triple ->
        # the harness would skip them all; validator must fail closed
        null_path = {"realized_r_multiple": None, "mfe_r": None, "mae_r": None}
        db = _build_db(tmp_path, [dict(null_path), dict(null_path)])
        rep = validate_audit_export(db, source_host="SYN", now_utc=NOW)
        assert not rep["ok"]
        assert any(e["code"] == "no_replayable_rows" for e in rep["errors"])

    def test_missing_file_fails(self, tmp_path):
        rep = validate_audit_export(tmp_path / "nope.db", source_host="SYN", now_utc=NOW)
        assert not rep["ok"]
        assert any(e["code"] == "file_unreadable" for e in rep["errors"])


class TestWarningsAndCensus:
    def test_out_of_order_timestamps_warn_not_fail(self, tmp_path):
        # row1 predates row0 (append-only ledgers grow forward in time) ->
        # non-blocking warning, not an error
        db = _build_db(tmp_path, [{}, {"outcome_timestamp": "2026-08-30T10:00:00+00:00"}])
        rep = validate_audit_export(db, source_host="SYN", now_utc=NOW)
        assert rep["ok"], rep["errors"]
        assert any(w["code"] == "timestamps_out_of_order" for w in rep["warnings"])

    def test_null_path_row_counted_in_census_not_fatal(self, tmp_path):
        db = _build_db(tmp_path, [{}, {}, {"mae_r": None, "exit_reason": None}])
        rep = validate_audit_export(db, source_host="SYN", now_utc=NOW)
        assert rep["ok"], rep["errors"]
        assert rep["checks"]["missing_value_census"]["mae_r"] == 1
        assert rep["checks"]["missing_value_census"]["exit_reason"] == 1
        assert rep["checks"]["replayable_rows"] == 2

    def test_expected_row_count_mismatch_warns(self, tmp_path):
        db = _build_db(tmp_path, [{} for _ in range(3)])
        rep = validate_audit_export(db, source_host="SYN", now_utc=NOW, expected_row_count=158)
        assert rep["ok"], rep["errors"]
        assert any(w["code"] == "row_count_mismatch" for w in rep["warnings"])

    def test_provenance_label_required(self, tmp_path):
        db = _build_db(tmp_path, [{}])
        rep = validate_audit_export(db, source_host="  ", now_utc=NOW)
        assert not rep["ok"]
        assert any(e["code"] == "missing_provenance" for e in rep["errors"])

    def test_duplicate_execution_id_warns(self, tmp_path):
        db = _build_db(tmp_path, [{}, {"execution_id": "900000"}])
        rep = validate_audit_export(db, source_host="SYN", now_utc=NOW)
        assert rep["ok"], rep["errors"]
        assert any(w["code"] == "duplicate_execution_ids" for w in rep["warnings"])


class TestSqlDumpFormat:
    def test_insert_dump_passes(self, tmp_path):
        db = _build_db(tmp_path, [{} for _ in range(2)])
        dump = tmp_path / "outcomes_export.sql"
        src = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        lines = ["BEGIN TRANSACTION;"]
        for row in src.execute(f"SELECT * FROM {TABLE}"):
            vals = ",".join(
                "NULL"
                if v is None
                else (
                    repr(v)
                    if isinstance(v, (int, float))
                    else "'" + str(v).replace("'", "''") + "'"
                )
                for v in row
            )
            lines.append(f"INSERT INTO {TABLE} VALUES({vals});")
        lines.append("COMMIT;")
        src.close()
        dump.write_text("\n".join(lines), encoding="utf-8")
        rep = validate_audit_export(dump, source_host="SYN", now_utc=NOW, expected_row_count=2)
        assert rep["ok"], rep["errors"]
        assert rep["format"] == "sql_dump"
        assert rep["checks"]["row_count"] == 2

    def test_corrupt_dump_rejected(self, tmp_path):
        # A text file with INSERT-like words but invalid SQL must fail closed
        # with dump_unloadable — never silently validate as an empty sqlite db.
        dump = tmp_path / "bad_export.sql"
        dump.write_text(
            "BEGIN TRANSACTION;\n"
            "INSERT INTO audit_experience_outcomes VALUES('oops', 'unclosed;\n"
            "COMMIT;\n",
            encoding="utf-8",
        )
        rep = validate_audit_export(dump, source_host="SYN", now_utc=NOW)
        assert not rep["ok"]
        assert any(e["code"] == "dump_unloadable" for e in rep["errors"])

    def test_binary_garbage_rejected_not_read_as_sqlite(self, tmp_path):
        garbage = tmp_path / "garbage.bin"
        garbage.write_bytes(b"\x00\x01\x02not sqlite at all" * 8)
        rep = validate_audit_export(garbage, source_host="SYN", now_utc=NOW)
        assert not rep["ok"]
        codes = {e["code"] for e in rep["errors"]}
        assert codes & {"dump_unloadable", "table_missing"}


if __name__ == "__main__":
    # Direct validator CLI: python tests/unit/test_audit_ledger_export_w3.py \
    #     <export.db|.sql> --source-host PROD-WINDOWS-HOST --expected-rows 158
    import argparse as _ap

    _p = _ap.ArgumentParser(description="Validate an audit_experience_outcomes export")
    _p.add_argument("db_path")
    _p.add_argument("--source-host", required=True)
    _p.add_argument("--expected-rows", type=int, default=None)
    _p.add_argument("--future-horizon-days", type=int, default=7)
    _a = _p.parse_args()
    _rep = validate_audit_export(
        _a.db_path,
        source_host=_a.source_host,
        expected_row_count=_a.expected_rows,
        future_horizon_days=_a.future_horizon_days,
    )
    print(json.dumps(_rep, indent=2))
    raise SystemExit(0 if _rep["ok"] else 1)
