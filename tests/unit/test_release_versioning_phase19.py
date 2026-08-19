"""TASK-9 (TASK-09-70D-PRODUCTION-RELEASE) — runtime version-consistency tests
(TEST-REL-16/27/30 mapping).

Covers:
    TEST-REL-16  Web bundle matches backend (stamp vs live-hash detection)
    TEST-REL-27  release manifest validated (version block reporting)
    TEST-REL-30  UI displays actual release/migration status (version block
                 exposes real backend data; no hardcoded status)
    brief §52    VERSION_INCONSISTENCY on drift, never silently ignored

Run: .venv/Scripts/python.exe -m pytest tests/unit/test_release_versioning_phase19.py -q
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexus_scalp.release import versioning as ver


@pytest.fixture()
def web_dir(tmp_path: Path) -> Path:
    """A fake served web bundle (4 canonical assets)."""
    d = tmp_path / "Web"
    d.mkdir()
    for name in ("app.js", "api_client.js", "index.html", "styles.css"):
        (d / name).write_text(f"// {name} fixture", encoding="utf-8")
    return d


@pytest.fixture()
def build_info_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A fake build-info.json stamped at a known path."""
    d = tmp_path
    info = {
        "product": "NexusScalpEngine",
        "version": "9.2.0",
        "git_commit": "abc123",
        "channel": "stable",
        "build_timestamp": "2026-08-19T00:00:00Z",
    }
    (d / "build-info.json").write_text(json.dumps(info), encoding="utf-8")
    monkeypatch.chdir(d)
    return d


def _no_db() -> dict:
    return {}


# ---------------------------------------------------------------------------
# Web bundle version detection (TEST-REL-16)
# ---------------------------------------------------------------------------


def test_web_bundle_live_hash_constant(web_dir: Path) -> None:
    h1 = ver._hash_web_dir(web_dir)
    h2 = ver._hash_web_dir(web_dir)
    assert h1 == h2 and h1[0] and h1[1] == 4


def test_web_bundle_changes_detected(web_dir: Path) -> None:
    before = ver._hash_web_dir(web_dir)
    (web_dir / "app.js").write_text("// changed", encoding="utf-8")
    after = ver._hash_web_dir(web_dir)
    assert before[0] != after[0]


def test_missing_web_dir_yields_no_hash() -> None:
    assert ver._hash_web_dir(Path("C:/definitely/not/here")) == ("", 0)


def test_stamp_mismatch_reported_inconsistent(
    web_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A build-info stamp that disagrees with the served assets must be
    VERSION_INCONSISTENCY — never silently ignored (brief §52)."""
    info = {
        "product": "NexusScalpEngine",
        "version": "9.2.0",
        "git_commit": "abc123",
        "channel": "stable",
        "web_bundle_version": "0000-deadbeef",  # wrong on purpose
    }
    (tmp_path / "build-info.json").write_text(json.dumps(info), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    block = ver.RuntimeVersionBlock(db_provider=_no_db, web_dir=web_dir).build()
    assert block["version_status"] == ver.STATUS_INCONSISTENT
    assert any("web bundle stamp" in p for p in block["problems"])


def test_matching_stamp_consistent(
    web_dir: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    live, _ = ver._hash_web_dir(web_dir)
    info = {
        "version": "9.2.0",
        "channel": "stable",
        "web_bundle_version": live,
    }
    (tmp_path / "build-info.json").write_text(json.dumps(info), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    block = ver.RuntimeVersionBlock(db_provider=_no_db, web_dir=web_dir).build()
    assert block["version_status"] == ver.STATUS_CONSISTENT
    assert block["web_bundle_version"] == live


# ---------------------------------------------------------------------------
# DB migration status flows into the version block (TEST-REL-30)
# ---------------------------------------------------------------------------


def test_db_drift_reported_inconsistent(web_dir: Path) -> None:
    def drift_provider() -> dict:
        return {
            "audit": {"current": 3, "expected": 4, "state": "pending", "pending": 1},
            "news": {"current": 2, "expected": 2, "state": "current", "pending": 0},
            "candle_intel": {"current": 2, "expected": 2, "state": "current", "pending": 0},
        }

    block = ver.RuntimeVersionBlock(db_provider=drift_provider, web_dir=web_dir).build()
    assert block["version_status"] == ver.STATUS_INCONSISTENT
    assert any("db audit" in p for p in block["problems"])
    assert block["database_schema"]["audit"]["current"] == 3


def test_no_db_drift_consistent(web_dir: Path) -> None:
    def ok_provider() -> dict:
        return {
            "audit": {"current": 4, "expected": 4, "state": "current", "pending": 0},
        }

    block = ver.RuntimeVersionBlock(db_provider=ok_provider, web_dir=web_dir).build()
    assert block["version_status"] == ver.STATUS_CONSISTENT


def test_0_0_0_version_reported_inconsistent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unstamped build must never claim consistency (brief §15)."""
    info = {"version": "0.0.0", "channel": "dev"}
    (tmp_path / "build-info.json").write_text(json.dumps(info), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    block = ver.RuntimeVersionBlock(db_provider=_no_db, web_dir=None).build()
    assert block["version_status"] == ver.STATUS_INCONSISTENT
    assert any("0.0.0" in p for p in block["problems"])


# ---------------------------------------------------------------------------
# Feature schema truth (registry-derived)
# ---------------------------------------------------------------------------


def test_feature_schema_block_real_registry(web_dir: Path) -> None:
    block = ver.RuntimeVersionBlock(db_provider=_no_db, web_dir=web_dir).build()
    fs = block["feature_schema"]
    assert fs["registered"] is True
    assert fs["id"] == "scalp_v1"
    assert fs["dimension"] == 50


def test_version_block_shape(web_dir: Path) -> None:
    block = ver.RuntimeVersionBlock(db_provider=_no_db, web_dir=web_dir).build()
    for key in (
        "application_version",
        "commit",
        "web_bundle_version",
        "web_bundle_stamp_source",
        "feature_schema",
        "database_schema",
        "version_status",
        "problems",
        "checked_at",
    ):
        assert key in block, key
