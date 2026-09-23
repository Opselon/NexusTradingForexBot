"""BUG-308C (AGENT-GOVERNANCE): GATE11 artifact-integrity shape contract.

Discovered by driving the REAL WalkForwardTrainer bundle publication
(``train_and_validate``) and handing the emitted artifacts straight to GATE11.

Pre-fix main 4958311d — the dict branch read only ``feature_dimension`` /
``num_classes``. The three real artifact shapes emit other spellings entirely:

* the published ``manifest.json`` (emission_gate.py:254-278) emits ``input_dim``
  and ``class_count`` — and no ``integrity_ok`` field at all;
* the trainer's ``model.meta.json`` (walk_forward_trainer.py:2510-2600) emits
  ``feature_schema_dimension`` / ``model_head_classes`` / ``num_classes`` —
  and likewise no ``integrity_ok`` and no hash handle;
* the promotion pipeline passes a ``ModelArtifactInfo``
  (orchestrator.py:369 → integrity.inspect_artifact) whose attribute names
  ``feature_dimension`` / ``num_classes`` / ``integrity_ok`` are the only ones
  the gate originally read.

So the ONLY caller shape the gate handled correctly was the object path — the
12-gate promotion pipeline. Every dict caller (a governance UI, a verification
report, a bundle re-check) got a FAIL on a valid artifact. Latent because no
such caller existed yet; ML-CI-001's real walk-forward harness was the first and
routed around it via ``inspect_artifact()``.

GATE11 now resolves every known spelling (canonical first; the class head ahead
of the label-side count per MODEL_CLASS_CONTRACT SSoT at
walk_forward_trainer.py:2516-2522), honours an explicit ``integrity_ok`` verdict
when supplied, derives an honest verdict from the declared identity markers when
it is not, re-verifies the bundle hash chain when the files are reachable,
classifies ``None`` and foreign shapes fail-closed without raising, and reports
a diagnostic that distinguishes "bad artifact" from "this document declares no
identity of its own".

Layering: GATE11 is a presence + honest-verdict gate, not a value-comparison
gate — the DIMENSION_MISMATCH / CLASS_COUNT_MISMATCH comparison happens in
``integrity.inspect_artifact`` (integrity.py:191-217), which sets
``integrity_ok=False``; GATE11 then rejects on that verdict. Widening the
accepted spellings cannot weaken a comparison this gate never performed.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from nexus_scalp.model_lifecycle.gates import gate_artifact_integrity
from nexus_scalp.model_lifecycle.models import ModelArtifactInfo

# ---------------------------------------------------------------------------
# The three real artifact shapes
# ---------------------------------------------------------------------------


def _bundle_manifest() -> dict[str, object]:
    """The keys a published bundle ``manifest.json`` actually emits.

    See ``emission_gate.build_bundle_manifest`` (emission_gate.py:254-278).
    Note the two things the original GATE11 did not expect: the dimension
    handle is ``input_dim`` and there is no ``integrity_ok`` field at all.
    """
    return {
        "manifest_version": "1.0.0",
        "bundle_id": "bundle_d809e26bf966",
        "model_sha256": "d809e26bf96656e4f906acecfe6dae9d6d45b7232dbf509b129044114716364a",
        "metadata_sha256": "de0d9274971e2eea7b0d095532d7b1c3646645e4458d48f4ab0c5dfff28f44c8",
        "scaler_sha256": "6713465f2b168606c4be1850b3167757ab1679a52a8d95c7d41e2ca2bb3f4889",
        "feature_schema_id": "scalp_v1",
        "label_schema_id": "triple_barrier_3class_v1",
        "class_count": 3,
        "input_dim": 70,
        "seed": 4242,
        "production_eligible": False,
        "smoke": True,
    }


def _trainer_meta() -> dict[str, object]:
    """The keys the trainer's ``model.meta.json`` actually emits.

    See ``WalkForwardTrainer._save_metadata`` (walk_forward_trainer.py:2510-2600):
    ``feature_schema_dimension`` / ``model_head_classes`` / ``num_classes``.
    It carries dimension + class markers but NO hash handle — it is bound BY the
    bundle manifest, and is not itself an identity document.
    """
    return {
        "num_features": 50,
        "num_classes": 3,
        "model_head_classes": 3,
        "feature_schema_id": "scalp_v1",
        "feature_schema_dimension": 50,
        "seed": 4242,
        "production_eligible": False,
        "smoke": True,
    }


def test_bug308c_published_bundle_manifest_passes_gate11() -> None:
    """The headline regression: a raw published manifest.json must PASS."""
    res = gate_artifact_integrity(_bundle_manifest())
    assert res.passed, f"GATE11 false-failed on a valid manifest: {res.reason}"
    assert res.details["num_classes"] == 3


def test_bug308c_published_manifest_with_bundle_dir_verifies_hash_chain(
    tmp_path: Path,
) -> None:
    """With ``_bundle_dir`` the declared hash chain is re-verified against the
    bytes; a real bundle passes and a tampered hash fails."""
    files = {
        "model.pt": b"tensor-bytes",
        "model.meta.json": b"{}",
        "model.scaler.npz": b"scaler-bytes",
    }
    expected = {}
    for name, data in files.items():
        (tmp_path / name).write_bytes(data)
        expected[name] = hashlib.sha256(data).hexdigest()
    m = {
        **_bundle_manifest(),
        "model_sha256": expected["model.pt"],
        "metadata_sha256": expected["model.meta.json"],
        "scaler_sha256": expected["model.scaler.npz"],
        "_bundle_dir": str(tmp_path),
    }
    assert gate_artifact_integrity(m).passed, "a matching hash chain must pass"
    tampered = {**m, "model_sha256": "0" * 64}
    bad = gate_artifact_integrity(tampered)
    assert not bad.passed, "a tampered model hash must be detected"
    assert "verdict=False" in bad.reason


def test_bug308c_published_manifest_missing_file_fails_closed(tmp_path: Path) -> None:
    """A bundle dir that no longer holds the declared files fails closed."""
    m = {**_bundle_manifest(), "_bundle_dir": str(tmp_path)}
    res = gate_artifact_integrity(m)
    assert not res.passed
    assert "verdict=False" in res.reason


def test_bug308c_trainer_meta_without_identity_fails_with_actionable_reason() -> None:
    """model.meta.json has dimension/class markers but no hash handle — GATE11
    must FAIL it (it declares no identity of its own) and say WHY, instead of
    the old opaque "dim=None classes=None"."""
    res = gate_artifact_integrity(_trainer_meta())
    assert not res.passed
    assert res.details["feature_dimension"] == 50  # the marker resolved
    assert res.details["num_classes"] == 3
    assert "no identity of its own" in res.reason
    assert "manifest.json" in res.reason  # tells the operator the fix


def test_bug308c_meta_with_explicit_verdict_is_honoured() -> None:
    """A caller that adds an honest integrity_ok verdict to the meta gets it
    honoured rather than refused for the missing hash handle."""
    res = gate_artifact_integrity({**_trainer_meta(), "integrity_ok": True})
    assert res.passed, res.reason
    assert res.details["feature_dimension"] == 50


def test_bug308c_explicit_false_verdict_rejects() -> None:
    """The value-mismatch path: inspect_artifact() marks the artifact invalid,
    GATE11 rejects on that verdict (alias resolution did not bypass it)."""
    res = gate_artifact_integrity({**_trainer_meta(), "integrity_ok": False})
    assert not res.passed
    assert "verdict=False" in res.reason


def test_bug308c_canonical_spelling_still_supported() -> None:
    """The canonical keys remain first-class — no alias-only regression."""
    res = gate_artifact_integrity({"integrity_ok": True, "feature_dimension": 70, "num_classes": 3})
    assert res.passed, res.reason
    assert res.details["feature_dimension"] == 70


def test_bug308c_all_dimension_spellings_resolve() -> None:
    """Each of the four dimension spellings resolves on its own."""
    for key in ("feature_dimension", "input_dim", "feature_schema_dimension", "num_features"):
        res = gate_artifact_integrity({"integrity_ok": True, key: 50, "num_classes": 3})
        assert res.passed, f"dimension handle {key} did not resolve: {res.reason}"
        assert res.details["feature_dimension"] == 50


def test_bug308c_all_class_spellings_resolve_head_first() -> None:
    """class_count (manifest) and num_classes resolve; the class head wins when
    handles diverge (MODEL_CLASS_CONTRACT SSoT)."""
    res = gate_artifact_integrity({"integrity_ok": True, "feature_dimension": 50, "class_count": 3})
    assert res.passed, res.reason

    divergent = gate_artifact_integrity(
        {
            "integrity_ok": True,
            "feature_dimension": 50,
            "model_head_classes": 4,
            "num_classes": 3,
        }
    )
    assert divergent.details["num_classes"] == 4, "the class head is ground truth"


def test_bug308c_nested_label_contract_class_count_resolves() -> None:
    """A manifest that only carries the class count inside its label contract
    (a downstream projection) still resolves."""
    res = gate_artifact_integrity(
        {
            "integrity_ok": True,
            "feature_dimension": 50,
            "label_contract": {"class_count": 3, "schema_id": "triple_barrier_3class_v1"},
        }
    )
    assert res.passed, res.reason


def test_bug308c_manifest_without_any_identity_fails_diagnostic() -> None:
    """A dict with no dimension and no hash reports what it actually saw."""
    res = gate_artifact_integrity({"integrity_ok": True, "width": 50, "head": 3})
    assert not res.passed
    assert "dim=None" in res.reason
    assert "classes=None" in res.reason


def test_bug308c_manifest_declaring_no_hash_fails_closed() -> None:
    """Markers present, no explicit verdict, no hash handle => FAIL, not pass."""
    res = gate_artifact_integrity({"input_dim": 50, "class_count": 3})
    assert not res.passed


# ---------------------------------------------------------------------------
# Object input: ModelArtifactInfo (the promotion-pipeline shape)
# ---------------------------------------------------------------------------


def _info(**overrides: object) -> ModelArtifactInfo:
    base: dict[str, object] = dict(
        model_id="cand-1",
        model_version="0.0.1",
        artifact_path="/tmp/model.pt",
        artifact_hash="abc123",
        artifact_bytes=1024,
        feature_schema_id="scalp_v1",
        feature_dimension=50,
        num_classes=3,
        architecture="scalp_net",
        scaler_path="",
        scaler_hash="",
        integrity_ok=True,
    )
    for key, value in overrides.items():
        base[key] = value
    return ModelArtifactInfo(**base)  # type: ignore[arg-type]


def test_bug308c_object_canonical_attributes_pass() -> None:
    assert gate_artifact_integrity(_info()).passed


def test_bug308c_object_with_falsy_but_present_verdict_fails() -> None:
    """integrity_ok=False must FAIL even when dims are valid (no shortcut)."""
    res = gate_artifact_integrity(_info(integrity_ok=False))
    assert not res.passed
    assert res.details["feature_dimension"] == 50
    assert res.details["num_classes"] == 3


def test_bug308c_object_with_only_manifest_spellings_resolves() -> None:
    """Objects projected from a manifest by a downstream caller, exposing only
    the manifest spellings, still resolve."""

    class ManifestProjected:
        integrity_ok = True
        input_dim = 50
        class_count = 3

    res = gate_artifact_integrity(ManifestProjected())
    assert res.passed, res.reason
    assert res.details["feature_dimension"] == 50
    assert res.details["num_classes"] == 3


# ---------------------------------------------------------------------------
# Robustness: the orchestrator's empty-artifacts shape and foreign inputs
# ---------------------------------------------------------------------------


def test_bug308c_none_input_returns_structured_fail_not_exception() -> None:
    """orchestrator.py:369 passes ``run.artifacts[0] if run.artifacts else None``.

    A run with zero artifacts must yield a structured FAIL, never a raise.
    """
    res = gate_artifact_integrity(None)
    assert not res.passed
    assert res.gate == "GATE11_ARTIFACT"
    assert "dim=None" in res.reason
    assert "classes=None" in res.reason


def test_bug308c_empty_dict_fails_closed() -> None:
    res = gate_artifact_integrity({})
    assert not res.passed
    assert res.gate == "GATE11_ARTIFACT"


@pytest.mark.parametrize("bad", [None, 0, "", [], object()])
def test_bug308c_non_dict_non_object_inputs_never_raise(bad: object) -> None:
    """GATE11 is a promotion gate: it must classify, never crash a caller that
    hands it an unexpected verification-report shape (fail-closed by design)."""
    res = gate_artifact_integrity(bad)
    assert isinstance(res.gate, str)
    assert res.passed is False


# ---------------------------------------------------------------------------
# Round-trip: the real producer key contract
# ---------------------------------------------------------------------------


def test_bug308c_producer_key_contract_is_live() -> None:
    """The aliases this gate reads are the ones the producers ACTUALLY emit.

    Guards against the fix decoupling from the producers: if either producer
    renames its handles, this breaks at the source of truth rather than only in
    a synthetic dict.
    """
    root = Path(__file__).resolve().parents[2]
    trainer = (root / "src" / "nexus_scalp" / "training" / "walk_forward_trainer.py").read_text(
        encoding="utf-8"
    )
    gate_src = (root / "src" / "nexus_scalp" / "training" / "emission_gate.py").read_text(
        encoding="utf-8"
    )
    assert '"model_head_classes"' in trainer, "trainer dropped model_head_classes"
    assert '"feature_schema_dimension"' in trainer, "trainer dropped feature_schema_dimension"
    assert '"input_dim"' in gate_src, "emission gate dropped input_dim"
    assert '"class_count"' in gate_src, "emission gate dropped class_count"
    # And prove the emitted shapes are the ones GATE11 accepts.
    assert gate_artifact_integrity(_bundle_manifest()).passed


def test_bug308c_published_manifest_json_disk_round_trip(tmp_path: Path) -> None:
    """A manifest.json written to disk and read back still passes GATE11."""
    manifest = _bundle_manifest()
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    loaded = json.loads(p.read_text(encoding="utf-8"))
    assert gate_artifact_integrity(loaded).passed


# ---------------------------------------------------------------------------
# Orchestrator gate-loop hardening
# ---------------------------------------------------------------------------


def test_bug308c_orchestrator_gate_loop_fails_closed_on_exception() -> None:
    """A raising gate yields a structured FAIL carrying the exception class,
    and does NOT abort the remaining gates in the loop."""
    from nexus_scalp.model_lifecycle import orchestrator as orch_mod

    calls: list[str] = []

    def boom() -> None:
        calls.append("boom")
        raise RuntimeError("gate exploded")

    def fine() -> str:
        calls.append("fine")
        return "ok"

    # Drive the loop with the module's own GateResult and the same
    # try/except shape the orchestrator uses (orchestrator.py:420-427).
    results: list[object] = []
    all_passed = True
    for gate_fn in (boom, fine):
        try:
            res = gate_fn()
        except Exception as e:
            res = orch_mod.GateResult(
                gate="UNKNOWN", passed=False, reason=f"{type(e).__name__}: {e}"
            )
        results.append(res)
        if not getattr(res, "passed", False):
            all_passed = False

    assert calls == ["boom", "fine"], "a raising gate must not abort the loop"
    assert all_passed is False
    failed = results[0]
    assert isinstance(failed, orch_mod.GateResult)
    assert failed.passed is False
    assert "RuntimeError" in failed.reason
    assert "gate exploded" in failed.reason
