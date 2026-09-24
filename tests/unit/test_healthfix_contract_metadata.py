"""NSE-HEALTHFIX-001 lane C — sidecar contract metadata vocabulary (MODEL_CONTRACT).

RED-BEFORE (origin/main, probe `GET /health`): HealthEngine.check_model_contract
read the bundle contract from the TORCH STATE_DICT
(release/health.py:478-483 -> `sd["metadata"]` / `sd["model_metadata"]`).
The state_dict of EVERY real bundle in this repo carries no metadata key
(probed: EURUSD/v1.0.0, XAUUSD/70d_liquidity, XAUUSD/50d_main -> top keys are
bare tensor names, metadata=None), so the gate emitted
`bundle metadata incomplete (NO_MODEL_METADATA)` against bundles that DO
declare their contract — in SIBLING files.

GREEN: release/model_bootstrap.resolve_bundle_contract resolves the declared
contract from those sidecars (signed manifest.json first, model.meta.json as
fallback), returns (schema_id, dimension) or (None, None) without ever
inventing a value, and reports manifest/meta disagreement honestly so a caller
can render the verdict. The wiring of check_model_contract to this helper is
the INTEGRATOR's seam (health.py is lane A's file); these tests exercise the
helper itself, which is the part lane C owns.

No torch load happens anywhere here — the vocabulary is pure JSON sidecar
reading, so the suite is fast and runs in the slim onefile CLI too.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexus_scalp.release import model_bootstrap
from nexus_scalp.release.model_bootstrap import resolve_bundle_contract

# The exact shapes the real producers write (see training/emission_gate.py
# build_bundle_manifest and the trainer's model.meta.json), not invented ones.
MANIFEST_70D = {
    "manifest_version": "1.0.0",
    "bundle_id": "bundle_ab12cd34ef56",
    "model_sha256": "a" * 64,
    "metadata_sha256": "b" * 64,
    "scaler_sha256": "c" * 64,
    "dataset_id": "ds_xauusd_70d",
    "dataset_sha256": "d" * 64,
    "feature_schema_id": "scalp_v3",
    "feature_schema_hash": "e" * 16,
    "label_schema_id": "triple_barrier_v1",
    "class_count": 3,
    "input_dim": 70,
    "seq_len": 32,
    "architecture": "scalp_v3",
    "lineage": "CLEAN_HISTORICAL",
    "production_eligible": True,
}

META_70D = {
    "feature_schema_id": "scalp_v3",
    "feature_schema_dimension": 70,
    "num_features": 70,
    "model_head_classes": 3,
    "canonical_feature_names": [f"feat_{i}" for i in range(70)],
    "model_sha256": "a" * 64,
    "label_contract": {"class_count": 3, "label_schema_id": "triple_barrier_v1"},
}

META_50D = {
    "feature_schema_id": "scalp_v1",
    "feature_schema_dimension": 50,
    "num_features": 50,
    "model_head_classes": 3,
}


def _write_bundle(bundle: Path, *, manifest: dict | None, meta: dict | None) -> Path:
    bundle.mkdir(parents=True, exist_ok=True)
    if manifest is not None:
        (bundle / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if meta is not None:
        (bundle / "model.meta.json").write_text(json.dumps(meta), encoding="utf-8")
    return bundle


# ---------------------------------------------------------------------------
# 1. the real bundle shapes: manifest + meta agree -> manifest's (scalp_v3, 70)
# ---------------------------------------------------------------------------


def test_manifest_plus_meta_returns_manifest_contract(tmp_path: Path) -> None:
    """The production 70D bundle: manifest.json (signed, hash-bound) +
    model.meta.json both present and agreeing -> the MANIFEST pair wins and the
    resolved contract is exactly what the gate needs to PASS instead of
    reporting NO_MODEL_METADATA."""
    bundle = _write_bundle(
        tmp_path / "scalp" / "XAUUSD" / "70d_liquidity", manifest=MANIFEST_70D, meta=META_70D
    )
    schema_id, dimension, report = resolve_bundle_contract(bundle)
    assert schema_id == "scalp_v3"
    assert dimension == 70
    assert report["source"] == "manifest.json"
    assert report["disagreement"] is False
    assert report["manifest_pair"] == ["scalp_v3", 70]
    assert report["meta_pair"] == ["scalp_v3", 70]


def test_manifest_pair_is_returned_verbatim(tmp_path: Path) -> None:
    """The manifest's own spellings (feature_schema_id + input_dim) are what the
    function returns — no re-derivation through the registry, so an unregistered
    research schema id still surfaces truthfully."""
    bundle = _write_bundle(
        tmp_path / "research",
        manifest={**MANIFEST_70D, "feature_schema_id": "scalp_v9", "input_dim": 90},
        meta=META_70D,
    )
    schema_id, dimension, _report = resolve_bundle_contract(bundle)
    assert (schema_id, dimension) == ("scalp_v9", 90)


# ---------------------------------------------------------------------------
# 2. meta-only bundle (no signed manifest) -> the fallback vocabulary
# ---------------------------------------------------------------------------


def test_meta_only_bundle_returns_meta_contract(tmp_path: Path) -> None:
    """XAUUSD/50d_main ships model.meta.json with feature_schema_id +
    feature_schema_dimension and no signed manifest: the fallback still yields a
    real contract, so a bundle is never reported as metadata-less when it
    declares its contract in the meta file alone."""
    bundle = _write_bundle(tmp_path / "scalp" / "XAUUSD" / "50d_main", manifest=None, meta=META_50D)
    schema_id, dimension, report = resolve_bundle_contract(bundle)
    assert (schema_id, dimension) == ("scalp_v1", 50)
    assert report["source"] == "model.meta.json"
    assert report["disagreement"] is False
    assert report["manifest_pair"] is None


def test_meta_num_features_spelling_is_accepted(tmp_path: Path) -> None:
    """Some producers emit ``num_features`` without the schema-dimension
    spelling; the helper accepts either, mirroring
    model_bundle_store._artifact_meta_coherence."""
    bundle = _write_bundle(
        tmp_path / "b",
        manifest=None,
        meta={"feature_schema_id": "scalp_v1", "num_features": 50},
    )
    schema_id, dimension, _report = resolve_bundle_contract(bundle)
    assert (schema_id, dimension) == ("scalp_v1", 50)


# ---------------------------------------------------------------------------
# 3. sidecar-less bundle (the EURUSD/v1.0.0 stub) -> (None, None), no invention
# ---------------------------------------------------------------------------


def test_sidecarless_bundle_returns_none_pair(tmp_path: Path) -> None:
    """The probe's actual failure artifact: a bare weights file with zero
    sidecars. The helper MUST return (None, None) — the honest UNKNOWN the gate
    reports as NO_MODEL_METADATA. Fabricating a schema id here would turn a
    truthful WARNING into a made-up PASS."""
    bundle = tmp_path / "scalp" / "EURUSD" / "v1.0.0"
    bundle.mkdir(parents=True, exist_ok=True)
    (bundle / "model.pt").write_bytes(b"<stub-state-dict>")
    schema_id, dimension, report = resolve_bundle_contract(bundle)
    assert schema_id is None
    assert dimension is None
    assert report["source"] is None
    assert report["disagreement"] is False
    assert report["manifest_pair"] is None
    assert report["meta_pair"] is None


def test_empty_and_missing_directory_is_not_an_error(tmp_path: Path) -> None:
    """A nonexistent bundle directory (cold clone, no artifact yet) yields
    (None, None) without raising — the caller renders the 'no artifact' path."""
    missing = tmp_path / "does" / "not" / "exist"
    assert resolve_bundle_contract(missing) == (
        None,
        None,
        {"source": None, "disagreement": False, "manifest_pair": None, "meta_pair": None},
    )

    empty = tmp_path / "empty"
    empty.mkdir()
    schema_id, dimension, _report = resolve_bundle_contract(empty)
    assert (schema_id, dimension) == (None, None)


def test_corrupt_sidecars_are_absent_not_invented(tmp_path: Path) -> None:
    """Garbage JSON / a non-object document is treated as ABSENT, never decoded
    into a plausible-looking contract."""
    bundle = tmp_path / "corrupt"
    bundle.mkdir()
    (bundle / "manifest.json").write_text("{not json", encoding="utf-8")
    (bundle / "model.meta.json").write_text("[1, 2, 3]", encoding="utf-8")
    schema_id, dimension, report = resolve_bundle_contract(bundle)
    assert (schema_id, dimension) == (None, None)
    assert report["source"] is None
    assert report["disagreement"] is False


def test_invalid_dimension_values_stay_none(tmp_path: Path) -> None:
    """A dimension that is not a positive integer (zero, negative, a string that
    is not a number, null) is NOT coerced into a contract."""
    bundle = tmp_path / "weird"
    bundle.mkdir()
    (bundle / "model.meta.json").write_text(
        json.dumps(
            {
                "feature_schema_id": "scalp_v3",
                "feature_schema_dimension": 0,
                "num_features": "seventy",
            }
        ),
        encoding="utf-8",
    )
    schema_id, dimension, _report = resolve_bundle_contract(bundle)
    assert schema_id == "scalp_v3"
    assert dimension is None


# ---------------------------------------------------------------------------
# 4. manifest / meta DISAGREEMENT -> manifest wins + honest flag
# ---------------------------------------------------------------------------


def test_manifest_meta_disagreement_manifest_wins(tmp_path: Path) -> None:
    """Contract §5.2: when the two sidecars disagree, the SIGNED manifest is
    authoritative (it is hash-bound to the weights and is the publication
    record), and the disagreement is REPORTED, not hidden — the caller decides
    the verdict, the helper just refuses to silently pick one."""
    bundle = _write_bundle(
        tmp_path / "drift",
        manifest=MANIFEST_70D,  # declares scalp_v3 / 70
        meta=META_50D,  # declares scalp_v1 / 50 (stale sidecar after a retrain)
    )
    schema_id, dimension, report = resolve_bundle_contract(bundle)
    assert (schema_id, dimension) == ("scalp_v3", 70)
    assert report["source"] == "manifest.json"
    assert report["disagreement"] is True
    assert report["manifest_pair"] == ["scalp_v3", 70]
    assert report["meta_pair"] == ["scalp_v1", 50]


def test_dimension_only_disagreement_is_flagged(tmp_path: Path) -> None:
    """Same schema id, different dimension (a 70D head bolted onto a 50D meta):
    still a disagreement, still manifest's dimension."""
    bundle = _write_bundle(
        tmp_path / "dim_drift",
        manifest=MANIFEST_70D,
        meta={**META_70D, "feature_schema_dimension": 50, "num_features": 50},
    )
    schema_id, dimension, report = resolve_bundle_contract(bundle)
    assert (schema_id, dimension) == ("scalp_v3", 70)
    assert report["disagreement"] is True


def test_partial_declaration_is_returned_as_is(tmp_path: Path) -> None:
    """A sidecar that declares a schema id but no dimension is NOT completed by
    the helper — a half-declared contract stays half-declared and the caller
    keeps reporting NO_MODEL_METADATA for the missing half."""
    bundle = _write_bundle(
        tmp_path / "partial",
        manifest=None,
        meta={"feature_schema_id": "scalp_v3", "note": "interrupted write"},
    )
    schema_id, dimension, report = resolve_bundle_contract(bundle)
    assert schema_id == "scalp_v3"
    assert dimension is None
    assert report["source"] == "model.meta.json"
    assert report["meta_pair"] == ["scalp_v3", None]


# ---------------------------------------------------------------------------
# 5. strictly read-only on artifacts — nothing is written, nothing is minted
# ---------------------------------------------------------------------------


def test_resolve_is_read_only_and_does_not_mint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Contract §5.3: resolving the contract never writes a file, never mints a
    starter, never touches a champion. A sidecar-less bundle stays sidecar-less
    after the call (the provisioning seam is a different entry point)."""
    bundle = tmp_path / "scalp" / "EURUSD" / "v1.0.0"
    bundle.mkdir(parents=True, exist_ok=True)
    model = bundle / "model.pt"
    model.write_bytes(b"<stub-state-dict>")

    calls: list[str] = []
    monkeypatch.setattr(
        model_bootstrap,
        "mint_starter_bundle",
        lambda *a, **k: calls.append("mint"),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(
        model_bootstrap,
        "provision",
        lambda *a, **k: calls.append("provision"),  # type: ignore[arg-type]
    )
    monkeypatch.setattr(
        model_bootstrap,
        "write_sidecars",
        lambda *a, **k: calls.append("sidecars") or {},  # type: ignore[arg-type]
    )

    schema_id, dimension, _report = resolve_bundle_contract(bundle)
    assert (schema_id, dimension) == (None, None)
    assert calls == [], "resolve_bundle_contract must never trigger provisioning"
    assert set(p.name for p in bundle.iterdir()) == {"model.pt"}


def test_no_file_under_the_bundle_tree_is_modified(tmp_path: Path) -> None:
    """Every file that existed before the call exists with IDENTICAL bytes after
    it — the read-only contract, checked against the on-disk state."""
    bundle = _write_bundle(tmp_path / "ro", manifest=MANIFEST_70D, meta=META_70D)
    (bundle / "model.pt").write_bytes(b"<weights>")
    (bundle / "model.scaler.npz").write_bytes(b"<scaler>")

    before = {p.name: p.read_bytes() for p in bundle.iterdir()}
    resolve_bundle_contract(bundle)
    after = {p.name: p.read_bytes() for p in bundle.iterdir()}
    assert before == after


# ---------------------------------------------------------------------------
# 6. the resolved pair is exactly what unblocks the compatibility gate
# ---------------------------------------------------------------------------


def test_resolved_pair_passes_resolve_model_compatibility(tmp_path: Path) -> None:
    """End-to-end intent: feeding the sidecar-resolved pair into the SAME
    compatibility engine check_model_contract calls yields PASS, while the
    pre-fix (None, None) yields UNKNOWN/NO_MODEL_METADATA. This is the
    regression pin for the probe's MODEL_CONTRACT WARNING without touching
    health.py (the integrator's seam)."""
    pytest.importorskip("numpy")  # resolve_model_compatibility pulls the numeric stack
    from nexus_scalp.features.liquidity_runtime import resolve_model_compatibility
    from nexus_scalp.features.schema_contract import DIMENSION, SCHEMA_ID

    bundle = _write_bundle(
        tmp_path / "scalp" / "XAUUSD" / "70d_liquidity", manifest=MANIFEST_70D, meta=META_70D
    )

    before = resolve_model_compatibility(None, 50, SCHEMA_ID, DIMENSION)
    assert (before["result"], before["reason"]) == ("UNKNOWN", "NO_MODEL_METADATA")

    schema_id, dimension, _report = resolve_bundle_contract(bundle)
    after = resolve_model_compatibility(schema_id, dimension, SCHEMA_ID, DIMENSION)
    assert after["result"] == "PASS", after
    assert after["reason"] == "SCHEMA_DIMENSION_MATCH"
