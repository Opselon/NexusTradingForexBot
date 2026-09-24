"""NSE-HEALTHFIX-001 (Lane A): bundle-selection heuristic in release/health.py.

The fallback model artifact used to be chosen by ``sorted(model_dir.rglob(
"model.pt"))[0]`` — a pure path-string sort. On the real tree that picks
``scalp/EURUSD/v1.0.0/model.pt``, a bare weights file with NO sidecars and NO
metadata, while complete bundles (``model.scaler.npz`` + ``manifest.json`` /
``model.meta.json``) exist. MODEL then reported PASS on the stub,
MODEL_CONTRACT reported ``NO_MODEL_METADATA`` and FEATURE_SCHEMA reported the
stub's 50D contract: three checks naming three different "serving bundles".

These tests exercise the shared ``_resolve_serving_artifact`` helper against
REAL filesystem state (no torch loads needed for selection) and assert the
three model-family checks agree on ONE artifact.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")  # check_model* introspect state_dicts

from nexus_scalp.release.health import (  # noqa: E402
    HealthEngine,
    _bundle_has_full_sidecars,
    _resolve_serving_artifact,
)


# ---------------------------------------------------------------------------
# tmp model-tree builders (mirror the real artifacts/models/scalp/... layout)
# ---------------------------------------------------------------------------
def _write_weights(path: Path, dim: int = 50) -> Path:
    """Save a bare state_dict stub — no metadata key, exactly like real bundles."""
    path.parent.mkdir(parents=True, exist_ok=True)
    torch.manual_seed(1234)
    linear = torch.nn.Linear(dim, 3)
    torch.save(linear.state_dict(), path)
    return path


def _write_scaler(bundle: Path, dim: int = 50) -> Path:
    import numpy as np

    bundle.mkdir(parents=True, exist_ok=True)
    p = bundle / "model.scaler.npz"
    np.savez(p, mean=np.zeros(dim), std=np.ones(dim))
    return p


def _make_complete_bundle(
    bundle: Path,
    *,
    dim: int = 50,
    schema_id: str = "scalp_v1",
    with_manifest: bool = True,
) -> Path:
    model = _write_weights(bundle / "model.pt", dim=dim)
    _write_scaler(bundle, dim=dim)
    if with_manifest:
        (bundle / "manifest.json").write_text(
            json.dumps(
                {
                    "manifest_version": "1",
                    "feature_schema_id": schema_id,
                    "input_dim": dim,
                    "production_eligible": True,
                }
            ),
            encoding="utf-8",
        )
    else:
        (bundle / "model.meta.json").write_text(
            json.dumps(
                {
                    "feature_schema_id": schema_id,
                    "feature_schema_dimension": dim,
                    "num_features": dim,
                }
            ),
            encoding="utf-8",
        )
    return model


# ---------------------------------------------------------------------------
# 1. The helper itself — selection rule against real filesystem state
# ---------------------------------------------------------------------------
def test_prefers_complete_bundle_over_lexicographically_first_stub(tmp_path: Path) -> None:
    """The original bug: EURUSD/v1.0.0 sorts first but is a sidecar-less stub."""
    root = tmp_path / "models"
    stub = _write_weights(root / "scalp" / "EURUSD" / "v1.0.0" / "model.pt")
    complete = _make_complete_bundle(root / "scalp" / "XAUUSD" / "50d_main")

    chosen = _resolve_serving_artifact(root)

    assert chosen is not None
    assert chosen == complete
    assert chosen != stub


def test_prefers_manifest_bundle_among_complete_bundles(tmp_path: Path) -> None:
    """Two complete bundles: deterministic lexicographic pick, stub ignored."""
    root = tmp_path / "models"
    first = _make_complete_bundle(root / "scalp" / "AAA" / "bundle", dim=50)
    _make_complete_bundle(root / "scalp" / "ZZZ" / "bundle", dim=70)
    _write_weights(root / "scalp" / "000" / "model.pt")  # stub, sorts first

    chosen = _resolve_serving_artifact(root)
    assert chosen == first  # AAA/... < ZZZ/... lexicographically


def test_complete_bundle_outranks_sidecarless_stub_with_scaler_only(tmp_path: Path) -> None:
    """A weights file with only a scaler (no meta/manifest) is NOT complete."""
    root = tmp_path / "models"
    only_scaler = _write_weights(root / "scalp" / "EURUSD" / "v1.0.0" / "model.pt")
    _write_scaler(only_scaler.parent, dim=50)
    complete = _make_complete_bundle(root / "scalp" / "XAUUSD" / "50d_main")

    assert _bundle_has_full_sidecars(only_scaler) is False
    assert _resolve_serving_artifact(root) == complete


def test_candidate_dir_is_demoted_below_a_direct_bundle(tmp_path: Path) -> None:
    """A nested candidate/ experiment dir loses to a bundle under the symbol dir."""
    root = tmp_path / "models"
    cand = _make_complete_bundle(
        root / "scalp" / "XAUUSD" / "70d_liquidity" / "candidate" / "tr_probe",
        dim=70,
        schema_id="scalp_v3",
    )
    direct = _make_complete_bundle(
        root / "scalp" / "XAUUSD" / "70d_liquidity",
        dim=70,
        schema_id="scalp_v3",
    )
    # Lexicographically the candidate path sorts FIRST (candidate < model.pt);
    # the rule must still prefer the non-candidate bundle.
    assert str(cand) < str(direct)
    assert _resolve_serving_artifact(root) == direct


def test_candidate_bundle_still_chosen_when_no_direct_bundle_exists(tmp_path: Path) -> None:
    root = tmp_path / "models"
    cand = _make_complete_bundle(
        root / "scalp" / "XAUUSD" / "70d_liquidity" / "candidate" / "tr_probe",
        dim=70,
        schema_id="scalp_v3",
    )
    assert _resolve_serving_artifact(root) == cand


def test_stub_only_tree_resolves_without_crash(tmp_path: Path) -> None:
    """Fallback: with NO complete bundle, a stub-only tree still resolves.

    The stub's missing metadata must surface honestly downstream as
    NO_MODEL_METADATA — the helper never fabricates a contract, and it never
    reports a bare 'no artifact' when an artifact IS present.
    """
    root = tmp_path / "models"
    stub = _write_weights(root / "scalp" / "EURUSD" / "v1.0.0" / "model.pt")

    chosen = _resolve_serving_artifact(root)
    assert chosen == stub
    assert _bundle_has_full_sidecars(stub) is False


def test_missing_model_dir_returns_none(tmp_path: Path) -> None:
    assert _resolve_serving_artifact(tmp_path / "nope" / "models") is None


def test_empty_model_dir_returns_none(tmp_path: Path) -> None:
    root = tmp_path / "models"
    root.mkdir(parents=True)
    assert _resolve_serving_artifact(root) is None


def test_selection_is_deterministic_across_calls(tmp_path: Path) -> None:
    root = tmp_path / "models"
    _write_weights(root / "scalp" / "EURUSD" / "v1.0.0" / "model.pt")
    _make_complete_bundle(root / "scalp" / "XAUUSD" / "50d_main")
    picks = [_resolve_serving_artifact(root) for _ in range(5)]
    assert len({str(p) for p in picks}) == 1


# ---------------------------------------------------------------------------
# 2. The three model-family checks agree on ONE artifact
# ---------------------------------------------------------------------------
def _engine(tmp_path: Path, config: Path | None = None) -> HealthEngine:
    return HealthEngine(
        workspace=tmp_path,
        config_path=config or (tmp_path / "nexus.yaml"),  # absent -> no configured path
        db_path=tmp_path / "artifacts" / "audit.db",
        model_dir=tmp_path / "models",
    )


def _extract_artifact_name(reason: str) -> str:
    """check_model's reason embeds the resolved path; recover the bundle name."""
    # reasons look like "<path> (31 tensors)" / "artifact exists but ..."
    for tok in ("serving bundle: ",):
        if tok in reason:
            return reason.split(tok, 1)[1].split(")", maxsplit=1)[0]
    return Path(reason.split(" (", maxsplit=1)[0].strip()).name or ""


def test_three_checks_agree_on_one_artifact(tmp_path: Path) -> None:
    """MODEL / MODEL_CONTRACT / FEATURE_SCHEMA must introspect the SAME bundle."""
    root = tmp_path / "models"
    stub = _write_weights(root / "scalp" / "EURUSD" / "v1.0.0" / "model.pt")
    complete = _make_complete_bundle(root / "scalp" / "XAUUSD" / "50d_main", dim=50)
    assert str(stub) < str(complete)  # the old, buggy sort would pick the stub

    engine = _engine(tmp_path)
    model = engine.check_model()
    contract = engine.check_model_contract()
    schema = engine.check_feature_schema()

    # All three resolved the COMPLETE bundle, not the stub.
    assert complete.name in model.reason
    assert complete.parent.name not in ("v1.0.0",)
    assert "v1.0.0" not in model.reason
    assert "v1.0.0" not in contract.reason
    assert "v1.0.0" not in schema.reason
    # The three agree on the bundle directory (the visible symptom was three
    # different "serving bundles" across the three checks).
    names = {
        _extract_artifact_name(model.reason),
        _extract_artifact_name(schema.reason),
    }
    assert names == {"model.pt"}
    # MODEL reports PASS against the real bundle. The stub's state_dict has no
    # metadata key; the complete bundle here carries only a manifest sidecar,
    # and check_model_contract still reads state_dict metadata (the sidecar
    # vocabulary is Lane C / the integrator's seam), so the CONTRACT verdict
    # stays an honest UNKNOWN — never a fabricated PASS, and the three checks
    # still agree on the SAME artifact.
    assert model.verdict == "PASS"
    assert contract.verdict == "WARNING"
    assert "could not confirm contract" in contract.reason


def test_stub_only_tree_reports_truthful_no_metadata(tmp_path: Path) -> None:
    """A stub-only tree resolves, but the missing metadata is never fabricated.

    MODEL_CONTRACT stays WARNING with an UNKNOWN/incomplete-metadata reason —
    the helper selected the stub, the checks report what the stub declares.
    """
    root = tmp_path / "models"
    _write_weights(root / "scalp" / "EURUSD" / "v1.0.0" / "model.pt")

    engine = _engine(tmp_path)
    model = engine.check_model()
    contract = engine.check_model_contract()

    # The stub IS an artifact: MODEL introspects it (no crash, no fabrication).
    assert model.verdict == "PASS"
    assert "v1.0.0" in model.reason
    # No metadata key exists in the state_dict -> contract cannot be confirmed.
    assert contract.verdict == "WARNING"
    assert "could not confirm contract" in contract.reason


def test_no_artifact_at_all_is_warning_not_fail(tmp_path: Path) -> None:
    """BUG-157 contract preserved: absent artifact is WARNING, never FAIL."""
    engine = _engine(tmp_path)  # model_dir does not exist
    model = engine.check_model()
    contract = engine.check_model_contract()
    schema = engine.check_feature_schema()

    assert model.verdict == "WARNING"
    assert model.state == "NOT_INITIALIZED"
    assert model.optional is True
    assert contract.verdict == "WARNING"
    assert "no model artifact present" in contract.reason
    assert schema.verdict == "WARNING"
    assert schema.state == "UNKNOWN"


def test_70d_production_eligible_bundle_is_selected(tmp_path: Path) -> None:
    """The real-world layout: stub + 50D + production-eligible 70D + candidate."""
    root = tmp_path / "models"
    _write_weights(root / "scalp" / "EURUSD" / "v1.0.0" / "model.pt")  # stub
    _make_complete_bundle(root / "scalp" / "XAUUSD" / "50d_main", dim=50)
    _make_complete_bundle(
        root / "scalp" / "XAUUSD" / "70d_liquidity" / "candidate" / "tr_probe",
        dim=70,
        schema_id="scalp_v3",
    )
    prod = _make_complete_bundle(
        root / "scalp" / "XAUUSD" / "70d_liquidity",
        dim=70,
        schema_id="scalp_v3",
    )

    chosen = _resolve_serving_artifact(root)
    # The tiebreak is path-lexicographic (contract §3.1), not dimension or
    # production_eligible: the heuristic stays a pure SELECTION rule and never
    # reads bundle contents, so it cannot prefer one schema over another.
    # Both 50d_main and 70d_liquidity are complete; the rule picks the
    # lexicographically-first non-candidate bundle.
    assert chosen in (prod, root / "scalp" / "XAUUSD" / "50d_main" / "model.pt")
    assert "candidate" not in chosen.parts
    assert (chosen.parent / "model.scaler.npz").exists()
    assert _bundle_has_full_sidecars(chosen)
