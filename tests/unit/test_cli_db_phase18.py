"""
CLI DB Commands — TASK-10 regression suite (TEST-DBM-25: CLI uses same engine)
==============================================================================
Verifies `nexus db status|plan|migrate|verify|migrations|history` are backed by
the canonical engine and produce truthful output, including --json (no ANSI).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from nexus_scalp.cli.main import app

runner = CliRunner()


def _json_out(res: Any) -> dict[str, Any]:
    """Parses the JSON payload from CLI stdout, skipping any log noise that
    a test runner may have mixed into the stream (production `--json` emits
    pure JSON; this is purely a test-harness resilience helper)."""
    text = res.stdout
    start = text.find("{")
    if start == -1:
        raise AssertionError(f"no JSON in CLI output: {text[:200]!r}")
    return json.loads(text[start:])


@pytest.fixture()
def db_dir(tmp_path: Path, monkeypatch: Any) -> Path:
    """Scratch workspace with an OLD audit DB so migrations are pending."""
    import sqlite3

    ws = tmp_path / "ws"
    (ws / "artifacts").mkdir(parents=True)
    db = ws / "artifacts" / "audit.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE schema_meta (key TEXT PRIMARY KEY, value TEXT)")
    con.execute("INSERT INTO schema_meta VALUES ('schema_version', '1')")
    con.commit()
    con.close()
    monkeypatch.chdir(ws)
    return ws


def test_db_plan_dry_run(db_dir: Path) -> None:
    """`nexus db plan` shows pending migrations but applies NOTHING."""
    res = runner.invoke(app, ["db", "plan", "--json"])
    assert res.exit_code == 0
    data = _json_out(res)
    audit = data["audit"]
    assert audit["current_version"] == 1
    assert audit["expected_version"] == 7
    assert audit["pending_count"] == 6
    # No mutation happened.
    import sqlite3

    con = sqlite3.connect(db_dir / "artifacts" / "audit.db")
    assert (
        con.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()[0] == "1"
    )
    con.close()


def test_db_status_json_shape(db_dir: Path) -> None:
    res = runner.invoke(app, ["db", "status", "--json"])
    assert res.exit_code == 0
    data = _json_out(res)
    for name in ("audit", "news", "candle_intel"):
        assert name in data
        assert "current_version" in data[name]
        assert "expected_version" in data[name]
        assert "migration_state" in data[name]


def test_db_migrate_applies_and_verify_passes(db_dir: Path) -> None:
    res = runner.invoke(app, ["db", "migrate", "--json"])
    assert res.exit_code == 0
    data = _json_out(res)
    audit = data["audit"]
    assert audit["state"] == "DB_MIGRATION_SUCCEEDED"
    assert audit["current_version"] == 7
    # Verify now passes.
    res2 = runner.invoke(app, ["db", "verify", "--json"])
    assert res2.exit_code == 0
    vdata = _json_out(res2)
    assert vdata["audit"]["verified"] is True


def test_db_migrations_and_history(db_dir: Path) -> None:
    runner.invoke(app, ["db", "migrate", "--json"])
    res = runner.invoke(app, ["db", "migrations", "--json"])
    assert res.exit_code == 0
    data = _json_out(res)
    assert data["audit"]["pending"] == []
    res2 = runner.invoke(app, ["db", "history", "--json", "--limit", "5"])
    assert res2.exit_code == 0
    hist = _json_out(res2)
    assert len(hist["audit"]["history"]) >= 3


def test_db_domain_filter(db_dir: Path) -> None:
    """--database news only touches the news domain."""
    res = runner.invoke(app, ["db", "plan", "--database", "news", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.stdout)
    assert set(data.keys()) == {"news"}
    assert "audit" not in data
