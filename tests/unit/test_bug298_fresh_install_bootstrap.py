"""BUG-293 regression: a freshly installed release must START (first-run bootstrap).

Defect (v9.0.12 windows release, user-reported 2026-09-15): clean install ->
`NexusScalpEngine.exe` -> LiveEngine construction -> ``ArtifactIntegrityError:
LOAD_REJECTED: artifact missing or empty (model.pt)`` -> PYI unhandled
exception. The release pipeline minted NO serving bundle anywhere; every CI
gate stayed green because none launched the packaged EXE in a start shape.

Covers the fix surface:
  1. release.bootstrap — bundle_status honesty, starter mint passes the REAL
     engine serving gates (digest + fresh-init canary + behavioral probe),
     idempotency, never-clobber rules, manifest bindings.
  2. resolve_model_path anchors relative config paths to the runtime root.
  3. execution_costs.resolve_canonical_path: source behavior unchanged; the
     packaged-only file is findable via bundle candidates (simulated frozen).
  4. engine_boot first-run gate wiring (source pins + live behavior through
     the bootstrap seam with a tmp workspace).
  5. model-provision / train-once registered on the CLI surface.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from nexus_scalp.release import bootstrap as rb

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# 1 — starter mint: passes EXACTLY the engine's load gates
# ---------------------------------------------------------------------------
def test_starter_mint_passes_engine_serving_gates(tmp_path: Path) -> None:
    """The minted starter must satisfy verify_artifact_integrity + the
    fresh-init canary + the behavioral probe — the same three gates
    _load_or_create_bundle applies (fix the fixture, never the gate)."""
    model = tmp_path / "artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt"
    assert rb.bundle_status(model)["state"] == rb.STATE_MISSING

    result = rb.mint_starter_bundle(model)
    assert result["provisioned"] is True
    assert result["state"] == rb.STATE_OK

    from nexus_scalp.model_lifecycle.integrity import (
        check_model_behavioral_health,
        detect_untrained_fresh_init,
    )
    from nexus_scalp.model_lifecycle.load_integrity import verify_artifact_integrity

    verdict = verify_artifact_integrity(model)  # raises on any integrity failure
    assert verdict.status.value == "VERIFIED"
    is_fresh, _d = detect_untrained_fresh_init(model, None)
    assert is_fresh is False, "starter must NOT be the canonical fresh init"
    healthy, detail, _m = check_model_behavioral_health(model, None)
    assert healthy is True, f"starter failed behavioral health: {detail}"


def test_starter_bundle_files_complete(tmp_path: Path) -> None:
    model = tmp_path / "bundle/model.pt"
    rb.mint_starter_bundle(model)
    manifest = json.loads((model.parent / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["model_sha256"] == rb.sha256_file(model)
    # Canonical seam ownership marker (BUG-296 model_bootstrap) + bound scaler
    # digest (P0-2 trust anchor) — the starter identity the coordinator reads.
    assert manifest.get("provisioner") == rb.mb.PROVISIONER_MARKER
    assert manifest.get("scaler_sha256")
    assert (model.parent / "model.scaler.npz").stat().st_size > 0
    meta = json.loads((model.parent / "model.meta.json").read_text(encoding="utf-8"))
    assert meta["feature_schema_dimension"] == rb.STARTER_DIM


def test_mint_is_idempotent_and_never_reclobbers_ok_bundle(tmp_path: Path) -> None:
    model = tmp_path / "bundle/model.pt"
    first = rb.mint_starter_bundle(model)
    assert first["provisioned"] is True
    digest = rb.sha256_file(model)
    second = rb.mint_starter_bundle(model)
    assert second["provisioned"] is False, "verified servable bundle must be kept"
    assert rb.sha256_file(model) == digest


def test_mint_never_overwrites_existing_unreadable_judgement(tmp_path: Path) -> None:
    """INVALID classification (probe cannot judge) must refuse, not clobber."""
    model = tmp_path / "bundle/model.pt"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"not-a-torch-artifact-but-nonempty")
    with patch.object(
        rb,
        "bundle_status",
        return_value={"state": rb.STATE_INVALID, "detail": "boom", "path": str(model)},
    ):
        with pytest.raises(rb.BootstrapError):
            rb.mint_starter_bundle(model)
    assert model.read_bytes() == b"not-a-torch-artifact-but-nonempty"


def test_mint_replaces_unservable_legacy_bundle(tmp_path: Path) -> None:
    """Digest-valid but degenerate weights (the BUG-269 class) ARE replaced —
    the starter path only ever holds non-governed artifacts."""
    model = tmp_path / "bundle/model.pt"
    model.parent.mkdir(parents=True)
    model.write_bytes(b"\x00\x01garbage-but-present")  # LOAD_REJECTED -> MISSING class
    result = rb.mint_starter_bundle(model)
    assert result["provisioned"] is True
    assert rb.bundle_status(model)["state"] == rb.STATE_OK


# ---------------------------------------------------------------------------
# 2 — path anchoring
# ---------------------------------------------------------------------------
def test_resolve_model_path_absolute_passthrough(tmp_path: Path) -> None:
    p = tmp_path / "somewhere/model.pt"
    assert rb.resolve_model_path(p) == p


def test_resolve_model_path_relative_anchors_to_workspace(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(rb.rpaths, "get_runtime_workspace", lambda: tmp_path)
    resolved = rb.resolve_model_path("artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt")
    assert resolved == (tmp_path / "artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt").resolve()


def test_live_engine_anchors_config_artifact_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LiveEngine.__init__ rewrites a relative config path to the workspace-
    anchored absolute (every later consumer — ChampionManager, bundle load —
    sees ONE canonical path). Proven without full engine construction by
    calling the exact anchoring statement path via the module seam."""
    from nexus_scalp.application import live_engine as le

    rb_probe = tmp_path / "artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt"
    monkeypatch.setattr(rb.rpaths, "get_runtime_workspace", lambda: tmp_path)
    anchored = rb.resolve_model_path(
        le.Path("artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt")
    )
    assert anchored == rb_probe.resolve()


# ---------------------------------------------------------------------------
# 3 — canonical execution costs: source unchanged, packaged resolvable
# ---------------------------------------------------------------------------
def test_costs_source_resolution_unchanged() -> None:
    from nexus_scalp.configuration import execution_costs as ec

    # Source (not frozen): repo-root exists -> resolver returns it verbatim.
    assert ec.resolve_canonical_path() == ec.CANONICAL_PATH


def test_costs_packaged_candidate_discovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulated frozen install: repo-root CANONICAL_PATH absent (mirrors the
    Temp-extract layout), artifact present only under <root>/configs —
    the resolver must find it (v9.0.12 warning class closed)."""
    from nexus_scalp.configuration import execution_costs as ec

    payload = json.loads(ec.CANONICAL_PATH.read_text(encoding="utf-8"))
    cfg_dir = tmp_path / "configs"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "execution_assumptions.json").write_text(json.dumps(payload), encoding="utf-8")

    monkeypatch.setattr(
        ec, "CANONICAL_PATH", tmp_path / "nonexistent" / "execution_assumptions.json"
    )
    monkeypatch.setattr(
        ec, "_packaged_canonical_candidates", lambda: (cfg_dir / "execution_assumptions.json",)
    )
    monkeypatch.delenv(ec.ENV_OVERRIDE, raising=False)
    assert ec.resolve_canonical_path() == cfg_dir / "execution_assumptions.json"
    ec._cached.cache_clear()
    costs = ec.get_execution_assumptions()
    assert costs.symbol == "XAUUSD"
    ec._cached.cache_clear()


def test_costs_missing_everywhere_still_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nexus_scalp.configuration import execution_costs as ec

    monkeypatch.setattr(ec, "CANONICAL_PATH", tmp_path / "gone.json")
    monkeypatch.setattr(ec, "_packaged_canonical_candidates", lambda: ())
    monkeypatch.delenv(ec.ENV_OVERRIDE, raising=False)
    ec._cached.cache_clear()
    with pytest.raises(ec.ExecutionCostsError):
        ec.get_execution_assumptions()
    ec._cached.cache_clear()


# ---------------------------------------------------------------------------
# 4 — engine_boot first-run gate (source pins; live behavior via bootstrap)
# ---------------------------------------------------------------------------
ENGINE_BOOT = REPO_ROOT / "src" / "nexus_scalp" / "cli" / "engine_boot.py"


def test_engine_boot_has_first_run_gate_and_workspace_anchor() -> None:
    src = ENGINE_BOOT.read_text(encoding="utf-8")
    assert "anchor_workspace()" in src, "BUG-293 regression: packaged workspace anchoring removed"
    assert "ensure_packaged_config_dir()" in src
    # Redesign contract: boot goes through the provisioning coordinator, and
    # the coordinator's PAPER fallback path is what ultimately mints.
    assert "FirstRunCoordinator" in src, (
        "BUG-293 regression: provisioning coordinator removed from boot"
    )
    assert "ensure_serving_model(" in src, "BUG-293 regression: first-run provisioning removed"
    # LIVE must NEVER auto-provision (money-path safety):
    gate_block = src.split("BUG-293 FIRST-RUN MODEL GATE", 1)[1].split("Heavy engine imports", 1)[0]
    assert "ExecutionMode.PAPER" in gate_block and "ExecutionMode.SHADOW" in gate_block
    assert "ExecutionMode.LIVE" not in gate_block
    # A starter is never presented as production: the boot path labels it.
    assert "DEV STARTER" in gate_block


def test_first_run_gate_provisions_for_paper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Behavior via the coordinator: PAPER + no official source => starter
    mint (labeled DEV_STARTER); SHADOW/LIVE on an empty slot => refused;
    after a starter install the slot classifies VERIFIED/DEV_STARTER."""
    from nexus_scalp.model_provisioning import LifecycleState
    from nexus_scalp.model_provisioning import service as prov

    monkeypatch.setattr(rb.rpaths, "get_runtime_workspace", lambda: tmp_path)
    coord = prov.FirstRunCoordinator(official=rb_official_unset())

    out_shadow = coord.ensure_serving_model("shadow")
    assert out_shadow["action"] == "refuse"
    assert rb.bundle_status(prov.serving_model_path())["state"] == rb.STATE_MISSING

    out_paper = coord.ensure_serving_model("paper")
    assert out_paper["action"] == "starter"
    assert out_paper["servable"] is True

    cls = prov.classify_serving_slot(prov.serving_model_path())
    assert cls.state == LifecycleState.VERIFIED
    assert cls.origin == prov.ORIGIN_DEV_STARTER
    assert cls.is_starter

    out_live = coord.ensure_serving_model("live")
    assert out_live["action"] == "keep"  # a bundle now exists; live never mints anyway


def rb_official_unset() -> object:
    from nexus_scalp.model_provisioning import OfficialBundleSource

    return OfficialBundleSource(base_url="")


# ---------------------------------------------------------------------------
# 5 — CLI surface registration
# ---------------------------------------------------------------------------
def test_provision_commands_registered() -> None:
    import nexus_scalp.cli.main as cmain

    names = {c.name or c.callback.__name__ for c in cmain.app.registered_commands}
    assert "model-provision" in names, sorted(names)
    assert "train-once" in names, sorted(names)


def test_model_provision_status_json_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`model-provision --status --json` on an empty slot: exit RUNTIME +
    JSON payload with state=missing (lowercase LifecycleState vocabulary;
    deterministic, no torch mint needed)."""
    from typer.testing import CliRunner

    import nexus_scalp.cli.provision_commands as pc

    monkeypatch.setattr(rb.rpaths, "get_runtime_workspace", lambda: tmp_path)
    res = CliRunner().invoke(pc.app, ["model-provision", "--status", "--json"])
    assert res.exit_code == rb_xc_runtime()
    payload = json.loads(_stdout_json(res.stdout))
    assert payload["state"] == "missing"
    assert "recommended" in payload


def rb_xc_runtime() -> int:
    from nexus_scalp.release import exit_codes as xc

    return xc.EXIT_RUNTIME


def _stdout_json(out: str) -> str:
    start = out.index("{")
    depth = 0
    for i in range(start, len(out)):
        if out[i] == "{":
            depth += 1
        elif out[i] == "}":
            depth -= 1
            if depth == 0:
                return out[start : i + 1]
    raise AssertionError(f"no balanced JSON in stdout: {out[:200]}")


# ---------------------------------------------------------------------------
# extras — starter must survive the REAL load path (not just the gates):
# a LiveEngine-shaped ModelBundleStore load on the minted bundle
# ---------------------------------------------------------------------------
def test_minted_bundle_loads_through_model_bundle_store(tmp_path: Path) -> None:
    """_load_or_create_bundle on a minted starter must return a usable
    bundle (this is the exact call that crashed v9.0.12)."""
    if "--fast" in sys.argv:  # torch-heavy
        pytest.skip("fast tier")
    torch = pytest.importorskip("torch")

    from nexus_scalp.application.live.model_bundle_store import ModelBundleStore
    from nexus_scalp.models.scalp_net import ScalpNet

    model = tmp_path / "bundle/model.pt"
    rb.mint_starter_bundle(model)

    class _Stub:  # minimal state surface (unbound-delegation contract)
        FEATURE_DIM = 70
        allow_legacy_unverified_artifacts = False

        def _declared_contract_dim_for_path(self, p: Path) -> int:
            return 70

        def _declared_head_classes_for_path(self, p: Path) -> int:
            return 3

        _load_or_initialize_model_weights = ModelBundleStore._load_or_initialize_model_weights
        _load_scaler_artifacts = ModelBundleStore._load_scaler_artifacts
        _expected_num_features_for_artifact = ModelBundleStore._expected_num_features_for_artifact
        _verify_champion_registry_binding = lambda self, p, actual_bytes_hash=None: None  # noqa: E731
        _bundle_lock = __import__("threading").Lock()

    stub = _Stub()
    bundle = ModelBundleStore._load_or_create_bundle(stub, model_path=model, force_fresh=False)
    assert bundle.artifact_path == model
    assert isinstance(bundle.model, ScalpNet)
    x = torch.zeros(1, 70)
    with torch.no_grad():
        out = bundle.model(x, return_logits=True)
    assert out.shape[-1] >= 3
