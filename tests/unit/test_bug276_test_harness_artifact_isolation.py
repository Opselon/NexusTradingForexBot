"""BUG-276: test-harness paths must never write production model artifacts or
the production model registry.

Incident (2026-09-14): a repo-root ``pytest tests/unit -n 4`` run rebuilt the
PRODUCTION champion bundle at
``artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt`` — bytes flipped from
the governed ``c9982ddde1755591`` to the fresh-init drift signature
``bb1f0afe30f746da`` — and the live registry collected CHAMPION rows bound to
pytest ``tmp_path`` artifacts (row 4073) and to the clobbered bytes (row 4080).

Mechanics (VERIFIED):
  * conftest's BUG-223 isolation sets NEXUS_AUDIT_DB, but LiveEngine's implicit
    audit construction goes through ``load_database_config('audit')`` ->
    ``default_sqlite_path`` -> the PRODUCTION artifacts/audit.db, because the
    env seam is honored only by AuditRepository's legacy implicit default — NOT
    by the DatabaseConfig resolver.
  * ``LiveEngine(force_fresh=True)`` mints fresh weights and WRITES them to
    ``config.model.model_artifact_path`` — the production bundle — via
    ``_save_model_weights_atomic``.

Fixes pinned here:
  1. ``load_database_config`` honors NEXUS_AUDIT_DB for the implicit sqlite
     default (same precedence contract as BUG-223: explicit callers win).
  2. ``force_fresh_model=True`` is refused (fail closed) when the target model
     path IS the configured production artifact.
  3. The quarantine migration is row-scoped, evidence-preserving and re-runs
     idempotently.
  4. Source pin: no production module ever passes force_fresh=True.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# 1. NEXUS_AUDIT_DB must reach load_database_config (the LiveEngine path)
# ---------------------------------------------------------------------------


def test_load_database_config_honors_nexus_audit_db(monkeypatch: pytest.MonkeyPatch) -> None:
    from nexus_scalp.database.config import load_database_config

    monkeypatch.setenv("NEXUS_AUDIT_DB", "C:/isolated/tmp_audit.db")
    monkeypatch.delenv("NSE_DATABASE__PROVIDER", raising=False)
    monkeypatch.delenv("NSE_DATABASE__SQLITE_PATH", raising=False)

    cfg = load_database_config("audit")
    assert cfg.is_sqlite
    assert cfg.sqlite_connect_path.replace("\\", "/").lower() == "c:/isolated/tmp_audit.db"


def test_load_database_config_explicit_sqlite_path_env_wins(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit NSE_DATABASE__SQLITE_PATH (container contract) takes
    precedence over the test-isolation seam."""
    from nexus_scalp.database.config import load_database_config

    monkeypatch.setenv("NSE_DATABASE__PROVIDER", "sqlite")
    monkeypatch.setenv("NSE_DATABASE__SQLITE_PATH", "C:/explicit/a.db")
    monkeypatch.setenv("NEXUS_AUDIT_DB", "C:/isolated/tmp_audit.db")

    cfg = load_database_config("audit")
    assert cfg.sqlite_connect_path.replace("\\", "/").lower() == "c:/explicit/a.db"


def test_load_database_config_other_domains_untouched(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """NEXUS_AUDIT_DB is the AUDIT domain seam only; news/strategies keep the
    canonical default paths."""
    from nexus_scalp.database.config import load_database_config

    monkeypatch.setenv("NEXUS_AUDIT_DB", "C:/isolated/tmp_audit.db")
    monkeypatch.delenv("NSE_DATABASE__PROVIDER", raising=False)

    cfg = load_database_config("strategies")
    assert "tmp_audit" not in cfg.sqlite_connect_path.lower().replace("\\", "/")


# ---------------------------------------------------------------------------
# 2. force_fresh must refuse the production artifact (fail closed)
# ---------------------------------------------------------------------------

_GUARD_EVENT = "BUG276_FORCE_FRESH_PROD_ARTIFACT_REFUSED"


class _NoSurface:
    """Direct construction surface: guard runs against self without engine attrs."""


def test_force_fresh_guard_refuses_existing_governed_tree_artifact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An EXISTING artifact inside the governed model tree must refuse minting."""
    from nexus_scalp.application.live import model_bundle_store as mbs

    governed_root = tmp_path / "artifacts/models"
    prod = governed_root / "scalp/XAUUSD/70d_liquidity/model.pt"
    prod.parent.mkdir(parents=True)
    prod.write_bytes(b"governed bytes")
    monkeypatch.setattr(
        "nexus_scalp.release.paths.get_models_dir", lambda: governed_root, raising=True
    )

    with pytest.raises(RuntimeError) as exc:
        mbs.ModelBundleStore._refuse_force_fresh_on_production_artifact(_NoSurface(), prod)
    assert _GUARD_EVENT in str(exc.value)


def test_force_fresh_guard_allows_isolated_tmp_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nexus_scalp.application.live import model_bundle_store as mbs

    governed_root = tmp_path / "prod_root/artifacts/models"
    governed_root.mkdir(parents=True)
    monkeypatch.setattr(
        "nexus_scalp.release.paths.get_models_dir", lambda: governed_root, raising=True
    )
    other = tmp_path / "pytest-tmp/model.pt"  # does not exist -> always allowed
    mbs.ModelBundleStore._refuse_force_fresh_on_production_artifact(_NoSurface(), other)
    other.parent.mkdir(parents=True)
    other.write_bytes(b"fresh")  # exists, but OUTSIDE the governed tree -> allowed
    mbs.ModelBundleStore._refuse_force_fresh_on_production_artifact(_NoSurface(), other)


# ---------------------------------------------------------------------------
# 3. quarantine migration — row-scoped, evidence-preserving, idempotent
# ---------------------------------------------------------------------------


def _mk_registry_db(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path))
    con.execute(
        """CREATE TABLE experience_model_registry (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             model_id TEXT NOT NULL,
             model_version TEXT NOT NULL,
             model_role TEXT NOT NULL,
             artifact_path TEXT,
             artifact_fingerprint TEXT,
             feature_schema_id TEXT,
             feature_dimension INTEGER,
             config_version TEXT,
             build_identity TEXT,
             was_replacement INTEGER DEFAULT 0,
             registered_at TEXT,
             lifecycle_status TEXT DEFAULT 'CANDIDATE',
             promotion_reason TEXT,
             gate_summary TEXT,
             training_run_id TEXT,
             parent_model_id TEXT,
             parent_model_version TEXT
           )"""
    )
    return con


def _row(con: sqlite3.Connection, mid: str, fp: str, path: str, status: str, when: str) -> int:
    cur = con.execute(
        "INSERT INTO experience_model_registry (model_id, model_version, model_role,"
        " artifact_path, artifact_fingerprint, feature_schema_id, feature_dimension,"
        " registered_at, lifecycle_status) VALUES (?, 'v1.0', 'PRIMARY_SCALP', ?, ?,"
        " 'scalp_v1', 50, ?, ?)",
        (mid, path, fp, when, status),
    )
    con.commit()
    return int(cur.lastrowid or 0)


@pytest.fixture()
def quarantine_script() -> Path:
    p = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "maintenance"
        / "quarantine_test_champion_rows.py"
    )
    assert p.exists(), f"migration script missing: {p}"
    return p


def test_quarantine_demotes_contaminated_rows_and_writes_evidence(
    tmp_path: Path, quarantine_script: Path
) -> None:
    db = tmp_path / "audit.db"
    con = _mk_registry_db(db)
    serving = tmp_path / "artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt"
    serving.parent.mkdir(parents=True)
    serving_bytes = b"governed champion bytes"
    serving.write_bytes(serving_bytes)
    serving_rel = "artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt"
    governed_fp = hashlib.sha256(serving_bytes).hexdigest()[:16]

    row_history = _row(
        con,
        "primary_scalp_scalp_v1_50d",
        "0872ae0b85b3c74b",
        "artifacts/.../v1.0.0/model.pt",
        "CHAMPION",
        "2026-09-04T16:35:39+00:00",
    )  # legacy stale row: path not present in this fake root, but registered BEFORE
    # the incident window — must NOT be touched (no REGISTRY_RECONCILED evidence)
    row_tmp = _row(  # pytest tmp_path artifact stamped CHAMPION
        con,
        "primary_scalp_scalp_v1_50d",
        "03157501848ac5b4",
        str(tmp_path / "pytest-of-x" / "test_live_tick_present0" / "model.pt"),
        "CHAMPION",
        "2026-09-14T03:50:15+00:00",
    )
    row_drift = _row(  # serving path, fingerprint != current bytes, in-window
        con,
        "primary_scalp_scalp_v1_50d",
        "bb1f0afe30f746da",
        serving_rel,
        "CHAMPION",
        "2026-09-14T03:14:02+00:00",
    )
    row_good = _row(  # the governed row — must survive as CHAMPION
        con,
        "primary_scalp_scalp_v1_50d",
        governed_fp,
        serving_rel,
        "CHAMPION",
        "2026-09-09T23:36:13+00:00",
    )
    # incident-window RECONCILED events (only the tmp-path one for the legacy row)
    con.execute(
        "CREATE TABLE model_governance_events (id INTEGER PRIMARY KEY, event TEXT,"
        " timestamp TEXT, payload TEXT)"
    )
    con.executemany(
        "INSERT INTO model_governance_events (event, timestamp, payload) VALUES (?,?,?)",
        [
            (
                "REGISTRY_RECONCILED",
                "2026-09-14T03:50:16+00:00",
                json.dumps({"artifact_path": str(tmp_path / "pytest-of-x")}),
            ),
            (
                "REGISTRY_RECONCILED",
                "2026-09-14T03:14:03+00:00",
                json.dumps({"artifact_path": serving_rel}),
            ),
        ],
    )
    con.commit()
    con.close()

    import subprocess
    import sys

    evidence = tmp_path / "evidence.json"
    r = subprocess.run(  # noqa: PLW1510 - rc asserted below
        [
            sys.executable,
            str(quarantine_script),
            "--db",
            str(db),
            "--workspace",
            str(tmp_path),
            "--serving-path",
            serving_rel,
            "--apply",
            "--evidence-out",
            str(evidence),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, r.stdout + r.stderr

    con = sqlite3.connect(str(db))
    st = dict(con.execute("SELECT id, lifecycle_status FROM experience_model_registry"))
    con.close()
    assert st[row_tmp] == "QUARANTINED"
    assert st[row_drift] == "QUARANTINED"
    assert st[row_good] == "CHAMPION"  # governed row survives
    assert st[row_history] == "CHAMPION"  # legacy row: pre-window, path outside serving
    # lineage preserved: reason recorded, rows NOT deleted
    ev = json.loads(evidence.read_text(encoding="utf-8"))
    ids = {e["id"] for e in ev["quarantined"]}
    assert ids == {row_tmp, row_drift}
    assert all(e["reason"].startswith("BUG-276") for e in ev["quarantined"])

    # Idempotent re-run: zero additional changes, same exit 0
    r2 = subprocess.run(  # noqa: PLW1510 - rc asserted below
        [
            sys.executable,
            str(quarantine_script),
            "--db",
            str(db),
            "--workspace",
            str(tmp_path),
            "--serving-path",
            serving_rel,
            "--apply",
            "--evidence-out",
            str(evidence),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r2.returncode == 0, r2.stdout + r2.stderr
    ev2 = json.loads(evidence.read_text(encoding="utf-8"))
    assert ev2["quarantined"] == []


# ---------------------------------------------------------------------------
# 4. source pin: production code never forces fresh weights
# ---------------------------------------------------------------------------


def test_no_production_module_forces_fresh_model() -> None:
    src = Path(__file__).resolve().parents[2] / "src" / "nexus_scalp"
    hits: list[str] = []
    for py in src.rglob("*.py"):
        text = py.read_text(encoding="utf-8", errors="replace")
        for i, line in enumerate(text.splitlines(), 1):
            code = line.split("#", 1)[0]
            # Call-site pattern only: LiveEngine(...force_fresh_model=True) or
            # _load_or_create_bundle(..., force_fresh=True) — docstring prose
            # and error-message strings must not trip the pin.
            if re.search(
                r"(LiveEngine\(|_load_or_create_bundle\()[^\n]*force_fresh(_model)?\s*=\s*True",
                code,
            ):
                hits.append(f"{py.relative_to(src)}:{i}: {line.strip()}")
    assert not hits, (
        "production code must never mint fresh weights into a configured "
        f"artifact path (BUG-276): {hits}"
    )


def test_quarantine_reason_is_audit_trail_conform(tmp_path: Path) -> None:
    """Migration sets promotion_reason with the BUG id + timestamp so the
    append-only evidence trail explains WHY the row lost authority."""
    db = tmp_path / "audit.db"
    con = _mk_registry_db(db)
    serving = tmp_path / "artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt"
    serving.parent.mkdir(parents=True)
    serving.write_bytes(b"governed bytes")  # precondition: serving artifact exists
    row = _row(
        con,
        "primary_scalp_scalp_v1_50d",
        "03157501848ac5b4",
        str(tmp_path / "pytest-of-x" / "m.pt"),
        "CHAMPION",
        "2026-09-14T03:50:15+00:00",
    )
    con.execute(
        "CREATE TABLE model_governance_events (id INTEGER PRIMARY KEY, event TEXT,"
        " timestamp TEXT, payload TEXT)"
    )
    con.commit()
    con.close()

    import subprocess
    import sys

    script = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "maintenance"
        / "quarantine_test_champion_rows.py"
    )
    r = subprocess.run(  # noqa: PLW1510 - rc asserted below
        [
            sys.executable,
            str(script),
            "--db",
            str(db),
            "--workspace",
            str(tmp_path),
            "--serving-path",
            "artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt",
            "--apply",
            "--evidence-out",
            str(tmp_path / "ev.json"),
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, r.stdout + r.stderr
    con = sqlite3.connect(str(db))
    status, reason = con.execute(
        "SELECT lifecycle_status, promotion_reason FROM experience_model_registry WHERE id=?",
        (row,),
    ).fetchone()
    con.close()
    assert status == "QUARANTINED"
    assert reason and "BUG-276" in reason
    assert datetime
