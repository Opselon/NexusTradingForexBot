# PURPOSE     : Contract tests for GET /api/provisioning/datasets (additive
#               provisioning dataset browser) — legacy raw-JSON shape, the
#               allowlist containment rules, extension/mtime/cap filtering and
#               the two typed error codes from CONTRACT §7.
# OWNER       : lane3-backend (provisioning wave; owns this file + src/
#               nexus_scalp/web/provisioning_routes.py only).
# CONSUMES    : nexus_scalp.web.provisioning_routes.register_provisioning_routes
#               mounted on a bare FastAPI app (TestClient, exactly like
#               tests/integration/test_pr247_semantic_reconciliation.py:51);
#               NEXUS_IMPORT_ROOTS env var as the allowlist seam; pytest tmp_path.
# PROVIDES    : Pins for {success, roots[{root, exists, count, files[{name,
#               path, ext, size_bytes, modified_iso}]}], total}, DATASETS_ROOT_REJECTED
#               on an out-of-allowlist ?root=, PROVISIONING_DATASETS_ERROR on
#               internal failure (no raw exception text), 500/root cap with
#               newest-mtime-first order, and train/start path usability via
#               _allowed_import_path.
# INVariANTS  : never widens _allowed_import_roots(); .csv/.parquet only;
#               entries resolving outside their root are never listed; missing
#               root = exists:false, never an error; file contents never read
#               (tests only stat/write); NO live-server contact — TestClient
#               only, localhost:59273 must never be touched from here.
# EXTEND      : add cases HERE when the listing contract grows; if the frozen
#               frontend/src/features/provisioning/model.ts DTOs disagree with
#               these pins, file a REQUESTS.md entry instead of editing them.
"""Contract pins for GET /api/provisioning/datasets (GAP-6 dataset browser).

Runs against a bare FastAPI app with ONLY the provisioning routes mounted and
a fully hermetic allowlist: NEXUS_IMPORT_ROOTS -> tmp importroot, cwd -> tmp
work dir (so the baked-in relative defaults data/imports + data/raw resolve
inside tmp and can never observe host data).
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nexus_scalp.web import provisioning_routes as routes

_DATASETS = "/api/provisioning/datasets"

# Deterministic mtimes (newest -> oldest): b.parquet, a.csv, sub/c.csv.
_MTIME_B = 1_700_000_300
_MTIME_A = 1_700_000_200
_MTIME_C = 1_700_000_100


@pytest.fixture
def web(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> tuple[TestClient, Path]:
    """Bare-app TestClient + hermetic allowlist over a fixture dataset tree.

    Layout (all under tmp_path, nothing under the repo):
      importroot/a.csv, b.parquet, notes.txt (excluded), sub/c.csv (recursive),
      outside.csv (outside every allowed root -> must never be listed).
    """
    importroot = (tmp_path / "importroot").resolve()
    work = (tmp_path / "work").resolve()
    (importroot / "sub").mkdir(parents=True)
    work.mkdir()
    (importroot / "a.csv").write_text("time,open\n1,2\n", encoding="utf-8")
    (importroot / "b.parquet").write_bytes(b"PAR1fake")  # contents are never read
    (importroot / "notes.txt").write_text("not a dataset\n", encoding="utf-8")
    (importroot / "sub" / "c.csv").write_text("time,open\n3,4\n", encoding="utf-8")
    (tmp_path / "outside.csv").write_text("time,open\n9,9\n", encoding="utf-8")
    for rel, stamp in (
        ("b.parquet", _MTIME_B),
        ("a.csv", _MTIME_A),
        ("sub/c.csv", _MTIME_C),
    ):
        os.utime(importroot / rel, (stamp, stamp))
    monkeypatch.setenv("NEXUS_IMPORT_ROOTS", str(importroot))
    monkeypatch.chdir(work)  # resolves data/imports + data/raw under tmp
    app = FastAPI()
    routes.register_provisioning_routes(
        app, lambda **kw: {"success": False, "error": kw}, lambda *a, **kw: None
    )
    return TestClient(app), importroot


def _entry(body: dict, root: Path) -> dict:
    """The single listing entry for `root` (asserts it is present exactly once)."""
    matches = [e for e in body["roots"] if e["root"] == str(root)]
    assert len(matches) == 1, f"expected exactly one entry for {root}, got {body['roots']}"
    return matches[0]


def test_success_shape_counts_extension_filter_and_mtime_order(
    web: tuple[TestClient, Path],
) -> None:
    """Legacy raw-JSON shape, recursive csv/parquet-only listing, newest first."""
    client, importroot = web
    r = client.get(_DATASETS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    assert set(body) >= {"success", "roots", "total"}  # NO v1 {data, meta} envelope
    assert "data" not in body and "meta" not in body
    assert isinstance(body["roots"], list) and body["roots"]
    assert body["total"] == sum(e["count"] for e in body["roots"])

    entry = _entry(body, importroot)
    assert entry["exists"] is True
    names = [f["name"] for f in entry["files"]]
    assert entry["count"] == len(names) == 3
    # mtime DESC (newest first); sub/c.csv included; txt and outside-root absent
    assert names == ["b.parquet", "a.csv", "c.csv"]
    assert "notes.txt" not in r.text
    assert "outside.csv" not in r.text

    for f in entry["files"]:
        assert f["ext"] in ("csv", "parquet")
        path = Path(f["path"])
        assert path.is_absolute()
        assert path.is_relative_to(importroot)  # containment, directly usable as train file
        assert f["path"].lower().endswith("." + f["ext"])
        assert isinstance(f["size_bytes"], int) and f["size_bytes"] > 0
        assert datetime.fromisoformat(f["modified_iso"])  # ISO-8601 parses
    assert [f["name"] for f in entry["files"]] == names  # matches observed mtimes
    stamps = [os.stat(f["path"]).st_mtime for f in entry["files"]]
    assert stamps == sorted(stamps, reverse=True)


def test_missing_default_roots_tolerated_never_error(web: tuple[TestClient, Path]) -> None:
    """The baked-in relative defaults (missing under the hermetic cwd) come back
    as exists:false entries — success stays true, counts stay 0."""
    client, _ = web
    body = client.get(_DATASETS).json()
    assert body["success"] is True
    for rel in ("data/imports", "data/raw"):
        entry = _entry(body, (Path.cwd() / rel).resolve())
        assert entry["exists"] is False
        assert entry["count"] == 0
        assert entry["files"] == []


def test_root_filter_inside_allowed_root_narrows_listing(web: tuple[TestClient, Path]) -> None:
    """?root= must accept an allowed root AND a subdirectory inside one."""
    client, importroot = web
    whole = client.get(_DATASETS, params={"root": str(importroot)}).json()
    assert whole["success"] is True
    assert [e["root"] for e in whole["roots"]] == [str(importroot)]
    assert whole["total"] == 3

    sub = client.get(_DATASETS, params={"root": str(importroot / "sub")}).json()
    assert sub["success"] is True
    assert sub["total"] == 1
    entry = _entry(sub, (importroot / "sub").resolve())
    assert entry["exists"] is True
    assert [f["name"] for f in entry["files"]] == ["c.csv"]


def test_root_filter_outside_allowed_root_rejected(web: tuple[TestClient, Path]) -> None:
    """Parent dir (prefix-bypass class), traversal and cwd: all DATASETS_ROOT_REJECTED."""
    client, importroot = web
    for bad in (
        str(importroot.parent),  # parent CONTAINS the root but is not inside it
        str(importroot / ".." / ".." / "escape"),  # resolves above every root
        str(Path.cwd()),  # the work dir itself is not an allowed root
        str(importroot / "a.csv\x00"),  # null byte -> unresolvable -> outside
    ):
        r = client.get(_DATASETS, params={"root": bad})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["success"] is not True
        assert body["error"]["code"] == "DATASETS_ROOT_REJECTED"
        assert "NEXUS_IMPORT_ROOTS" in r.text  # the operator is told where to extend


def test_listed_paths_pass_the_train_start_containment_guard(
    web: tuple[TestClient, Path],
) -> None:
    """`path` is directly usable as train/start `file`: it survives
    _allowed_import_path unchanged (same allowlist, no widening)."""
    client, importroot = web
    body = client.get(_DATASETS, params={"root": str(importroot)}).json()
    files = body["roots"][0]["files"]
    assert files
    for f in files:
        assert routes._allowed_import_path(f["path"]) == Path(f["path"])


def test_datasets_needs_no_engine_and_is_not_blocked_by_one(
    web: tuple[TestClient, Path],
) -> None:
    """Engineless path unaffected (and an active engine does not guard this
    read-only route the way it guards POST /official)."""
    client, _ = web
    assert getattr(client.app.state, "engine", None) is None
    assert client.get(_DATASETS).json()["success"] is True
    client.app.state.engine = object()  # engine now "running"
    assert client.get(_DATASETS).json()["success"] is True


def test_internal_failure_maps_to_typed_error_without_leaking(
    web: tuple[TestClient, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Any scan/allowlist failure answers PROVISIONING_DATASETS_ERROR with no
    raw exception text (py/stack-trace-exposure discipline)."""
    client, _ = web

    def _boom() -> list[Path]:
        raise OSError("permission denied exposing C:\\secret\\path")

    monkeypatch.setattr(routes, "_allowed_import_roots", _boom)
    r = client.get(_DATASETS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is not True
    assert body["error"]["code"] == "PROVISIONING_DATASETS_ERROR"
    assert "permission denied" not in r.text and "secret" not in r.text


def test_cap_500_per_root_newest_first(web: tuple[TestClient, Path]) -> None:
    """Hard cap: 505 candidates -> exactly the 500 newest survive, oldest dropped."""
    client, importroot = web
    for i in range(502):
        path = importroot / f"bulk_{i:03d}.csv"
        path.write_text("x\n", encoding="utf-8")
        stamp = 1_700_100_000 + i  # all newer than the fixture files
        os.utime(path, (stamp, stamp))
    body = client.get(_DATASETS, params={"root": str(importroot)}).json()
    assert body["success"] is True
    entry = body["roots"][0]
    names = [f["name"] for f in entry["files"]]
    assert entry["count"] == body["total"] == 500
    assert len(names) == 500
    assert names[0] == "bulk_501.csv"  # newest kept first
    assert names[-1] == "bulk_002.csv"  # bulk_000/001 are the oldest of the bulk set
    # the 3 fixture files are the 3 oldest overall -> dropped by the cap
    for dropped in ("a.csv", "b.parquet", "c.csv", "bulk_000.csv", "bulk_001.csv"):
        assert dropped not in names
