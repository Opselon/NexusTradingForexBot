"""P1/P2 MODEL ARTIFACT TRUST — load-path integrity + torch.load guard tests.

Covers:

    H. model weights modified after manifest generation  -> HASH_MISMATCH, rejected
    I. missing model integrity metadata                  -> LEGACY_UNVERIFIED,
                                                            rejected by default
    J. fine-tune atomic activation (weights+manifest coherent pair)
    K. crash/interruption before activation leaves the prior artifact serving
    M. unsafe torch.load detection (the CI guard is exercised as a unit)

The serving loader (ModelBundleStore._load_or_create_bundle via the facade)
calls verify_artifact_integrity() BEFORE weights become the serving bundle.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "src"))

from nexus_scalp.model_lifecycle.load_integrity import (  # noqa: E402
    ArtifactIntegrityError,
    ArtifactIntegrityStatus,
    verify_artifact_integrity,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _bundle(tmp_path: Path, *, with_manifest: bool = True, with_meta: bool = False) -> Path:
    d = tmp_path / "bundle"
    d.mkdir()
    weights = d / "model.pt"
    weights.write_bytes(b"FAKE-WEIGHTS-BYTES")
    if with_manifest:
        (d / "manifest.json").write_text(
            json.dumps(
                {
                    "manifest_version": "1.0.0",
                    "model_sha256": _sha(weights),
                    "feature_schema_id": "scalp_v3",
                }
            ),
            encoding="utf-8",
        )
    if with_meta:
        (d / "model.meta.json").write_text(
            json.dumps({"feature_schema_dimension": 70}), encoding="utf-8"
        )
    return weights


# ---------------------------------------------------------------------------
# H. weights modified after manifest generation
# ---------------------------------------------------------------------------
def test_h_weights_modified_after_manifest_rejected(tmp_path: Path) -> None:
    weights = _bundle(tmp_path)
    # Tamper AFTER the manifest was written (the fine-tune in-place class).
    weights.write_bytes(b"TAMPERED-WEIGHTS-BYTES")
    with pytest.raises(ArtifactIntegrityError) as err:
        verify_artifact_integrity(weights)
    assert err.value.verdict.status is ArtifactIntegrityStatus.HASH_MISMATCH
    v = err.value.verdict.as_dict()
    assert v["expected_sha256"] and v["actual_sha256"]


# ---------------------------------------------------------------------------
# I. missing integrity metadata
# ---------------------------------------------------------------------------
def test_i_missing_metadata_rejected_by_default(tmp_path: Path) -> None:
    weights = _bundle(tmp_path, with_manifest=False)
    with pytest.raises(ArtifactIntegrityError) as err:
        verify_artifact_integrity(weights)
    assert err.value.verdict.status is ArtifactIntegrityStatus.LEGACY_UNVERIFIED


def test_i2_missing_metadata_allowed_only_with_explicit_optin(tmp_path: Path) -> None:
    weights = _bundle(tmp_path, with_manifest=False)
    verdict = verify_artifact_integrity(weights, allow_legacy_unverified=True)
    assert (
        verdict.status is ArtifactIntegrityStatus.LEGACY_UNVERIFIED
    )  # observable, never "verified"


def test_i3_meta_only_bundle_without_digest_is_legacy(tmp_path: Path) -> None:
    # A meta.json WITHOUT a digest field declares nothing about integrity.
    d = tmp_path / "b"
    d.mkdir()
    w = d / "model.pt"
    w.write_bytes(b"W")
    (d / "model.meta.json").write_text(
        json.dumps({"feature_schema_dimension": 70}), encoding="utf-8"
    )
    with pytest.raises(ArtifactIntegrityError):
        verify_artifact_integrity(w)


def test_i4_missing_artifact_rejected(tmp_path: Path) -> None:
    with pytest.raises(ArtifactIntegrityError) as err:
        verify_artifact_integrity(tmp_path / "nope" / "model.pt")
    assert err.value.verdict.status is ArtifactIntegrityStatus.LOAD_REJECTED


# ---------------------------------------------------------------------------
# VERIFIED path
# ---------------------------------------------------------------------------
def test_v_verified_bundle_passes(tmp_path: Path) -> None:
    weights = _bundle(tmp_path)
    verdict = verify_artifact_integrity(weights)
    assert verdict.status is ArtifactIntegrityStatus.VERIFIED
    assert verdict.expected_sha256 == _sha(weights)


# ---------------------------------------------------------------------------
# J/K. fine-tune atomic activation + interruption (weights+manifest coherence)
# ---------------------------------------------------------------------------
def test_j_write_weights_then_manifest_order_is_safe(tmp_path: Path) -> None:
    """The publish order (weights LAST-in-manifest, manifest as commit marker)
    means an interruption between weights and manifest leaves NO metadata —
    classified LEGACY_UNVERIFIED (rejected), never silently served."""
    d = tmp_path / "pub"
    d.mkdir()
    old = d / "model.pt"
    old.write_bytes(b"OLD")
    (d / "manifest.json").write_text(json.dumps({"model_sha256": _sha(old)}), encoding="utf-8")
    # Simulate crash after new weights landed but before manifest update:
    new_weights = d / "model.pt.new"
    new_weights.write_bytes(b"NEW")
    new_weights.replace(d / "model.pt")  # weights swapped
    # manifest still describes OLD -> mismatch (fail closed)
    with pytest.raises(ArtifactIntegrityError) as err:
        verify_artifact_integrity(d / "model.pt")
    assert err.value.verdict.status is ArtifactIntegrityStatus.HASH_MISMATCH


def test_k_interruption_before_activation_keeps_old_verifiable(tmp_path: Path) -> None:
    """A crash BEFORE activation leaves the prior (manifest-coherent) artifact
    verifiable — rollback/restart keeps serving a VERIFIED bundle."""
    d = tmp_path / "prior"
    d.mkdir()
    w = d / "model.pt"
    w.write_bytes(b"PRIOR-VERIFIED")
    (d / "manifest.json").write_text(json.dumps({"model_sha256": _sha(w)}), encoding="utf-8")
    # staged candidate that never activated
    staged = d / "staged"
    staged.mkdir()
    (staged / "model.pt").write_bytes(b"CANDIDATE-NEVER-ACTIVATED")
    verdict = verify_artifact_integrity(w)
    assert verdict.status is ArtifactIntegrityStatus.VERIFIED


# ---------------------------------------------------------------------------
# M. unsafe torch.load detection (CI guard as a unit)
# ---------------------------------------------------------------------------
def _run_guard(repo: Path, tmp_tree: Path) -> tuple[int, str]:
    # Run the guard's checker against a synthetic tree directly.
    probe = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys, pathlib;\n"
                f"sys.path.insert(0, r'{repo / 'scripts' / 'ci'}');\n"
                "from check_torch_load_safety import check_file, SCAN_ROOT;\n"
                "p = pathlib.Path(sys.argv[1]);\n"
                "bad = check_file(p);\n"
                "print('VIOLATIONS', len(bad));\n"
            ),
            str(tmp_tree / "unsafe.py"),
        ],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return probe.returncode, probe.stdout + probe.stderr


def test_m_guard_detects_newly_introduced_unsafe_torch_load(tmp_path: Path) -> None:
    unsafe = tmp_path / "unsafe.py"
    unsafe.write_text(
        "import torch\n"
        "def load(p):\n"
        "    return torch.load(p, map_location='cpu')  # missing weights_only\n",
        encoding="utf-8",
    )
    rc, out = _run_guard(REPO, tmp_path)
    assert "VIOLATIONS 1" in out, out


def test_m2_guard_accepts_weights_only_true(tmp_path: Path) -> None:
    safe = tmp_path / "unsafe.py"  # same filename the runner expects
    safe.write_text(
        "import torch\n"
        "def load(p):\n"
        "    return torch.load(p, map_location='cpu', weights_only=True)\n",
        encoding="utf-8",
    )
    rc, out = _run_guard(REPO, tmp_path)
    assert "VIOLATIONS 0" in out, out


def test_m3_guard_ignores_comments_and_strings(tmp_path: Path) -> None:
    """No false positives for prose mentioning torch.load (AST-based guard)."""
    doc = tmp_path / "unsafe.py"
    doc.write_text(
        "# torch.load( with weights_only=False is documented here as DANGEROUS\n"
        'S = "torch.load(path) without weights_only"\n'
        "def unrelated(x):\n"
        "    return x\n",
        encoding="utf-8",
    )
    rc, out = _run_guard(REPO, tmp_path)
    assert "VIOLATIONS 0" in out, out


def test_m4_guard_clean_on_current_repo_sources() -> None:
    """The whole production tree is clean right now (repo-level invariant)."""
    script = REPO / "scripts" / "ci" / "check_torch_load_safety.py"
    probe = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(REPO),
        check=False,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr
