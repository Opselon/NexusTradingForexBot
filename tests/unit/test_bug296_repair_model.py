"""BUG-296 (Z-B1/Z-B5/W12-1) — zero-state clone-and-run bootstrap regressions.

Evidence base: docs/audit/wave_20260914/05_zero_state.md lanes E1/E4/E6/E8/E9 +
wave-12 startup finding. RED-BEFORE states at origin/main bae2dc19 (all
hand-probed in the lane audit, now pinned):

  E1  bare `nexus start` without a model artifact died as a raw
      ArtifactIntegrityError traceback -> now an operator panel whose hint
      names `nexus repair --model` (gate stays fail-closed).
  E4  `nexus repair --model` did not exist (rc=2 "No such option") while
      health hints printed the command -> the option ships and the hints are
      TRUE.
  E6  `nexus db migrate` FileNotFoundError on the missing artifacts/ parent
      of the migration LOCK -> parent mkdir before os.open.
  E8  a mint without integrity sidecars (manifest/meta/scaler) BRICKS boot #2
      as LEGACY_UNVERIFIED -> repair --model must stamp sidecars so the very
      next non-fresh load verifies (pinned here by a real second construction).
  E9  provisioning must never clobber a digest-VERIFIED foreign bundle or a
      governed registry CHAMPION -> KEPT / REFUSED verdicts, bytes untouched
      (sha256 before/after).
  W12 the success/welcome panels printed U+2713/U+25CF on the engine_boot
      pre-bind path: UnicodeEncodeError under redirected cp1252 stdout killed
      the boot BEFORE the web server bound -> ASCII-safe rendering (pinned by
      rendering into a real cp1252 byte stream).
"""

from __future__ import annotations

import hashlib
import io
import json
import shutil
import sqlite3
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from nexus_scalp.release import model_bootstrap

REPO = Path(__file__).resolve().parents[2]
runner = CliRunner()

ART_RELPATH = Path("artifacts/models/scalp/XAUUSD/70d_liquidity")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _clone_ws(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Throwaway workspace shaped like a fresh clone + redirected machine state.

    Mirrors the lane-05 isolation harness: cwd = clone dir, LOCALAPPDATA /
    APPDATA (user config, models dir, data root) + NEXUS_SETTINGS_DB +
    NEXUS_AUDIT_DB all point inside tmp_path — the host's real
    %LOCALAPPDATA%\\NexusScalpEngine and production artifacts/ are unreachable.
    """
    ws = tmp_path / "clone"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "configs").mkdir(exist_ok=True)
    shutil.copy2(REPO / "configs" / "base.yaml", ws / "configs" / "base.yaml")
    userroot = tmp_path / "userroot"
    (userroot / "NexusScalpEngine").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("LOCALAPPDATA", str(userroot))
    monkeypatch.setenv("APPDATA", str(userroot))
    monkeypatch.setenv("NEXUS_SETTINGS_DB", str(userroot / "app_settings.db"))
    monkeypatch.setenv("NEXUS_AUDIT_DB", str(ws / "artifacts" / "audit.db"))
    monkeypatch.setenv("NEXUS_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("NSE_NO_TELEGRAM", "1")
    monkeypatch.chdir(ws)
    return ws


def _invoke(args: list[str]):
    return runner.invoke(__import__("nexus_scalp.cli.main", fromlist=["app"]).app, args)


def _parse_json_output(res) -> dict:
    lines = (res.stdout or "").splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("{") or line.strip().startswith("["):
            try:
                return json.loads("\n".join(lines[i:]))
            except json.JSONDecodeError:
                continue
    raise AssertionError(f"no JSON document in output: {(res.stdout or '')[:300]!r}")


def _parse_json_first(res) -> dict:
    """Lenient parse: structlog lines can mix into CliRunner stdout on paths
    that log during the command (db migrate APPLY emits migration events).
    Decode the FIRST balanced JSON document and ignore the rest."""
    text = res.stdout or ""
    start = text.find("{")
    assert start >= 0, f"no JSON document in output: {text[:300]!r}"
    obj, _end = json.JSONDecoder().raw_decode(text[start:])
    return obj


def _artifact(ws: Path) -> Path:
    return ws / ART_RELPATH / "model.pt"


# ---------------------------------------------------------------------------
# 1. repair --model mints + stamps sidecars + boot #2 loads (E4/E8/Z-B1)
# ---------------------------------------------------------------------------


def test_repair_model_option_exists_and_mints_full_bundle(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E4-RED: at base, `repair --model` exited 2 ("No such option").
    GREEN: rc=0, MINTED verdict, and ALL FOUR bundle files exist."""
    ws = _clone_ws(tmp_path, monkeypatch)
    res = _invoke(["repair", "--model", "--json"])
    assert res.exit_code == 0, res.stdout[-500:]
    payload = _parse_json_output(res)
    model = payload["model"]
    assert model["status"] == "MINTED", model
    art = _artifact(ws)
    for name in ("model.pt", "manifest.json", "model.meta.json", "model.scaler.npz"):
        assert (art.parent / name).exists(), (
            f"missing sidecar {name} (E8: sidecar-less mint bricks boot #2)"
        )
    manifest = json.loads((art.parent / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["model_sha256"] == _sha256(art)
    assert manifest["scaler_sha256"] == _sha256(art.parent / "model.scaler.npz")


def test_repair_model_bundle_passes_integrity_and_second_construction_load(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E8 PIN (the bricked second boot): after the sidecar-stamped mint, a
    FRESH non-force load — verify_artifact_integrity + a second LiveEngine
    construction with force_fresh=False — must succeed. RED-BEFORE: the E8
    bare mint (no manifest) made boot #2 raise LEGACY_UNVERIFIED."""
    pytest.importorskip("torch")
    from nexus_scalp.model_lifecycle.load_integrity import (
        ArtifactIntegrityStatus,
        verify_artifact_integrity,
    )

    ws = _clone_ws(tmp_path, monkeypatch)
    assert _invoke(["repair", "--model", "--json"]).exit_code == 0
    art = _artifact(ws)

    verdict = verify_artifact_integrity(art)
    assert verdict.status is ArtifactIntegrityStatus.VERIFIED

    # Boot #2: a real engine construction loading the on-disk bundle (no
    # force_fresh). This is the exact call the E8 brick killed.
    from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter
    from nexus_scalp.configuration.config import AppConfig
    from nexus_scalp.domain.enums import ExecutionMode

    cfg = AppConfig.model_validate(
        {
            "execution": {"symbol": "XAUUSD", "mode": "PAPER", "magic_number": 888292},
            "model": {"model_artifact_path": str(art)},
            "telegram": {"enabled": False, "bot_token": "x", "admin_id": "y"},
        }
    )
    adapter = PaperMT5Adapter(initial_balance=10_000.0, symbol="XAUUSD")
    adapter.connect()
    LiveEngine = __import__(
        "nexus_scalp.application.live_engine", fromlist=["LiveEngine"]
    ).LiveEngine
    engine = LiveEngine(config=cfg, adapter=adapter, mode_override=ExecutionMode.PAPER)
    assert engine._bundle is not None
    assert Path(engine._bundle.artifact_path) == art


def test_repair_model_repairs_legacy_bare_mint_brick(tmp_path, monkeypatch) -> None:
    """E8 shape at rest: a bare model.pt with NO sidecars is exactly what a
    force_fresh mint leaves behind — verify refuses it (LEGACY_UNVERIFIED) and
    repair --model must restore a verified bundle."""
    pytest.importorskip("torch")
    from nexus_scalp.model_lifecycle.load_integrity import (
        ArtifactIntegrityError,
        ArtifactIntegrityStatus,
    )

    ws = _clone_ws(tmp_path, monkeypatch)
    art = _artifact(ws)
    art.parent.mkdir(parents=True, exist_ok=True)
    model_bootstrap.mint_trained_starter(art)  # bare weights, NO sidecars
    ok, _detail = model_bootstrap.bundle_is_servable(art)
    assert ok, (
        "bare seed-999 mint passes the content probes; it is the MISSING SIDECARS that brick boot #2"
    )
    from nexus_scalp.model_lifecycle.load_integrity import verify_artifact_integrity

    with pytest.raises(ArtifactIntegrityError) as integ:
        verify_artifact_integrity(art)
    assert integ.value.verdict.status is ArtifactIntegrityStatus.LEGACY_UNVERIFIED

    res = _invoke(["repair", "--model", "--json"])
    assert res.exit_code == 0
    assert _parse_json_output(res)["model"]["status"] == "MINTED"
    verdict = verify_artifact_integrity(art)
    assert verdict.status is ArtifactIntegrityStatus.VERIFIED


# ---------------------------------------------------------------------------
# 2. Idempotence + refusal classes (E9)
# ---------------------------------------------------------------------------


def test_repair_model_idempotent_second_run_skips_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws = _clone_ws(tmp_path, monkeypatch)
    assert _invoke(["repair", "--model", "--json"]).exit_code == 0
    art = _artifact(ws)
    before = _sha256(art)
    mtime = art.stat().st_mtime_ns
    res = _invoke(["repair", "--model", "--json"])
    assert res.exit_code == 0
    payload = _parse_json_output(res)
    assert payload["model"]["status"] == "SKIPPED"
    assert _sha256(art) == before
    assert art.stat().st_mtime_ns == mtime, "skip must not rewrite the bundle"
    # --force on the DECLARED non-champion starter may re-provision (E9 clause)
    res_f = _invoke(["repair", "--model", "--force", "--json"])
    assert res_f.exit_code == 0
    assert _parse_json_output(res_f)["model"]["status"] == "MINTED"


def test_repair_model_keeps_verified_foreign_bundle_byte_identical(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """E9 clobber class: a digest-VERIFIED + servable artifact that is NOT a
    declared starter (trained bundle mounted by the operator) must be KEPT —
    bytes AND manifest unchanged, even with --force."""
    ws = _clone_ws(tmp_path, monkeypatch)
    assert _invoke(["repair", "--model", "--json"]).exit_code == 0
    art = _artifact(ws)
    manifest = art.parent / "manifest.json"
    record = json.loads(manifest.read_text(encoding="utf-8"))
    del record["provisioner"]  # externally-owned verified bundle
    manifest.write_text(json.dumps(record), encoding="utf-8")
    art_sha, man_sha, sca_sha = (
        _sha256(art),
        _sha256(manifest),
        _sha256(art.parent / "model.scaler.npz"),
    )
    res = _invoke(["repair", "--model", "--json"])
    assert res.exit_code == 0
    payload = _parse_json_output(res)
    assert payload["model"]["status"] == "KEPT", payload["model"]
    assert payload["model"]["reason"] == "VERIFIED_ARTIFACT_PRESENT"
    res_f = _invoke(["repair", "--model", "--force", "--json"])
    assert _parse_json_output(res_f)["model"]["status"] == "KEPT", (
        "--force must NOT override a verified foreign (non-champion-marker) bundle"
    )
    assert _sha256(art) == art_sha
    assert _sha256(manifest) == man_sha
    assert _sha256(art.parent / "model.scaler.npz") == sca_sha


def test_repair_model_refuses_governed_champion(tmp_path, monkeypatch) -> None:
    """Registry-truth refusal (reuse of the BUG-278 protected-artifact class):
    with a CHAMPION row bound to the artifact fingerprint, provisioning is
    REFUSED — never clobbered — even with --force."""
    ws = _clone_ws(tmp_path, monkeypatch)
    assert _invoke(["repair", "--model", "--json"]).exit_code == 0
    art = _artifact(ws)
    audit_db = ws / "artifacts" / "audit.db"
    assert audit_db.exists(), "repair provisions the canonical audit DB (BUG-146)"
    from nexus_scalp.experience.provenance import fingerprint_artifact

    fp = fingerprint_artifact(art)
    conn = sqlite3.connect(audit_db)
    try:
        # lifecycle_status is an ADDITIVE column (ModelLifecycleRegistry.
        # ensure_schema) — mirror the heal before seeding a CHAMPION row.
        cols = {r[1] for r in conn.execute("PRAGMA table_info(experience_model_registry)")}
        if "lifecycle_status" not in cols:
            conn.execute(
                "ALTER TABLE experience_model_registry ADD COLUMN lifecycle_status TEXT "
                "DEFAULT 'CANDIDATE'"
            )
        conn.execute(
            "INSERT INTO experience_model_registry "
            "(model_id, model_version, model_role, artifact_path, artifact_fingerprint, "
            "feature_schema_id, feature_dimension, registered_at, lifecycle_status) "
            "VALUES ('primary_scalp_scalp_v3_70d', 'v1', 'PRIMARY_SCALP', ?, ?, "
            "'scalp_v3', 70, '2026-09-15T00:00:00+00:00', 'CHAMPION')",
            (str(art), fp),
        )
        conn.commit()
    finally:
        conn.close()
    art_sha, man_sha = _sha256(art), _sha256(art.parent / "manifest.json")
    res = _invoke(["repair", "--model", "--force", "--json"])
    payload = _parse_json_output(res)
    assert payload["model"]["status"] == "REFUSED", payload["model"]
    assert payload["model"]["reason"] == "CHAMPION_GOVERNED"
    assert _sha256(art) == art_sha, "governed champion bytes must be untouched"
    assert _sha256(art.parent / "manifest.json") == man_sha


# ---------------------------------------------------------------------------
# 3. Z-B5 — db migrate with no artifacts/ dir (E6)
# ---------------------------------------------------------------------------


def test_db_migrate_succeeds_without_artifacts_dir(tmp_path, monkeypatch) -> None:
    """E6-RED: lock os.open without parent mkdir -> FileNotFoundError rc=1.
    GREEN: rc=0 from a genuinely empty workspace; canonical DBs created."""
    ws = tmp_path / "bareclone"
    ws.mkdir()
    userroot = tmp_path / "userroot"
    monkeypatch.setenv("LOCALAPPDATA", str(userroot))
    monkeypatch.setenv("APPDATA", str(userroot))
    monkeypatch.setenv("NEXUS_SETTINGS_DB", str(userroot / "app_settings.db"))
    monkeypatch.setenv("NEXUS_AUDIT_DB", str(ws / "artifacts" / "audit.db"))
    monkeypatch.chdir(ws)
    assert not (ws / "artifacts").exists()
    res = _invoke(["db", "migrate", "--json"])
    assert res.exit_code == 0, (res.stdout or "")[-500:]
    payload = _parse_json_first(res)
    assert payload["audit"]["state"] in ("DB_MIGRATION_SUCCEEDED", "DB_MIGRATION_NOT_REQUIRED")
    assert (ws / "artifacts" / "audit.db").exists()
    res2 = _invoke(["db", "migrate", "--json"])
    assert res2.exit_code == 0
    assert _parse_json_first(res2)["audit"]["state"] == "DB_MIGRATION_NOT_REQUIRED"


# ---------------------------------------------------------------------------
# 4. W12-1 — cp1252 pre-bind panel safety + LOAD_REJECTED operator hint
# ---------------------------------------------------------------------------

_NON_CP1252 = "✓✗●◐■⚠→◎"


def test_success_and_welcome_panels_render_under_cp1252(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """wave12 step-2/3/4: the migration panel printed U+2713 (and the welcome
    panel U+25CF/0x26A0) — under redirected stdout with a cp1252 encoding
    (Windows default) the WRITE raised UnicodeEncodeError before uvicorn
    bound. Render into a REAL cp1252 byte stream: must not raise."""
    from nexus_scalp.cli import styling
    from nexus_scalp.cli.styling import _error_panel, _success_panel, _welcome_panel

    buf = io.BytesIO()
    stream = io.TextIOWrapper(buf, encoding="cp1252", errors="strict", newline="")
    con = Console(file=stream, force_terminal=False, width=100)
    # _welcome_panel renders on the module-global console; redirect it.
    monkeypatch.setattr(styling, "console", con)
    con.print(_success_panel("Migrations applied", "Database schemas are now current"))
    con.print(
        _error_panel(
            "Model load rejected (fail closed)",
            "LOAD_REJECTED: artifact missing or empty (model.pt)",
            hint="Provision a PAPER starter bundle with `nexus repair --model`",
            exit_code=1,
        )
    )
    _welcome_panel(
        mode_value="PAPER",
        symbol="XAUUSD",
        risk_drawdown=5.0,
        endpoints=["http://127.0.0.1:8080"],
        animate=False,
    )
    _welcome_panel(
        mode_value="LIVE",
        symbol="XAUUSD",
        risk_drawdown=5.0,
        endpoints=["http://127.0.0.1:8080"],
        animate=False,
    )
    stream.flush()
    out = buf.getvalue().decode("cp1252")
    assert "Migrations applied" in out
    assert "OK" in out, "success marker kept (as ASCII) for light terminals"
    assert "!! LIVE" in out, "LIVE warning kept (as ASCII) — was U+26A0"


def test_engine_boot_prebind_source_is_cp1252_safe() -> None:
    """Static pin for the boot-critical pair: every PRINTED string literal on
    the engine_boot pre-bind path, and every string in the shared styling
    helpers, must be cp1252-encodable (the two failure points wave-12 hit:
    U+2713 in _success_panel, U+25CF/U+26A0 in the welcome panel under a
    redirected cp1252 stdout). The engine_boot module docstring is exempt
    (narration, never rendered); a future non-ASCII string that reaches
    console.print must either be ASCII or go through an encoding-safe writer."""
    import ast

    def _strings(tree: ast.Module, *, skip_docstring: bool) -> list[tuple[int, str]]:
        out: list[tuple[int, str]] = []
        first = tree.body[0] if tree.body else None
        for node in ast.walk(tree):
            if skip_docstring and isinstance(first, ast.Expr) and node is first.value:
                continue
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                out.append((getattr(node, "lineno", 0), node.value))
        return out

    boot = ast.parse(
        (REPO / "src" / "nexus_scalp" / "cli" / "engine_boot.py").read_text(encoding="utf-8")
    )
    styling = ast.parse(
        (REPO / "src" / "nexus_scalp" / "cli" / "styling.py").read_text(encoding="utf-8")
    )
    for lineno, s in _strings(boot, skip_docstring=True):
        try:
            s.encode("cp1252")
        except UnicodeEncodeError as e:
            pytest.fail(
                f"engine_boot string literal (line {lineno}) not cp1252-safe: {s[:60]!r} ({e})"
            )
    for lineno, s in _strings(styling, skip_docstring=True):
        try:
            s.encode("cp1252")
        except UnicodeEncodeError as e:
            pytest.fail(f"styling string literal (line {lineno}) not cp1252-safe: {s[:60]!r} ({e})")


def test_load_rejected_path_names_repair_model_hint() -> None:
    """Gate must stay fail-closed (no silent mint on the boot path) while the
    surfaced error names the remedy (E1/E4 operator-hint contract)."""
    boot = (REPO / "src" / "nexus_scalp" / "cli" / "engine_boot.py").read_text(encoding="utf-8")
    assert "ArtifactIntegrityError" in boot, "integrity failure must be caught on the boot path"
    assert "nexus repair --model" in boot, "LOAD_REJECTED path must name the remedy"
    assert "raise typer.Exit(xc.EXIT_RUNTIME) from None" in boot, "gate stays fail-closed"
    # health hints (E4: they advertised a flag that did not exist) must stay
    # pointed at the now-real command.
    health = (REPO / "src" / "nexus_scalp" / "release" / "health.py").read_text(encoding="utf-8")
    assert "nexus repair --model" in health
    # README quickstart gained the bootstrap step (Z-B8 interim fold-in).
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "nexus repair --model" in readme


# ---------------------------------------------------------------------------
# 5. Shared-seam pins: docker + CLI cannot fork
# ---------------------------------------------------------------------------


def test_sidecar_note_marker_written_by_repair_model(tmp_path, monkeypatch) -> None:
    ws = _clone_ws(tmp_path, monkeypatch)
    assert _invoke(["repair", "--model", "--json"]).exit_code == 0
    record = json.loads((_artifact(ws).parent / "manifest.json").read_text(encoding="utf-8"))
    assert record["provisioner"] == model_bootstrap.PROVISIONER_MARKER
    assert "nexus repair --model" in record["note"]
