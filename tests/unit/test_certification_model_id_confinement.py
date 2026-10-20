"""Certification artifact paths are confined to the bundle directory.

CodeQL flagged six ``py/path-injection`` sinks in
``model_generation/certification.py``: ``model_id`` reaches the artifact joins
from manifests, registry rows and CLI arguments, so an id such as
``../../secrets`` placed the READ (and the certificate WRITE) outside the
artifact root. The gate is the trust boundary for model loading, so an id that
cannot be confined must be refused outright — not normalised into something
that happens to load.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from nexus_scalp.model_generation.certification import (
    CompatibilityError,
    _bundle_artifact,
    load_certificate,
)

ESCAPES = [
    "../outside",
    "../../etc/passwd",
    "/absolute/path",
    "sub/dir",
    "..",
    ".hidden?",  # '?' is not a legal component character here
    "",
    "model\n.pt",
]


@pytest.mark.parametrize("model_id", ESCAPES)
def test_escaping_model_ids_are_rejected(tmp_path: Path, model_id: str) -> None:
    with pytest.raises(CompatibilityError):
        _bundle_artifact(tmp_path, model_id, ".pt")


@pytest.mark.parametrize("model_id", ESCAPES)
def test_load_certificate_refuses_escaping_ids(tmp_path: Path, model_id: str) -> None:
    with pytest.raises(CompatibilityError):
        load_certificate(tmp_path, model_id)


def test_a_plain_model_id_still_resolves_inside_the_bundle(tmp_path: Path) -> None:
    path = _bundle_artifact(tmp_path, "scalp-70d_v2.1", ".certificate.json")
    assert path.parent == tmp_path.resolve()
    assert path.name == "scalp-70d_v2.1.certificate.json"


def test_an_absent_certificate_is_none_not_an_error(tmp_path: Path) -> None:
    assert load_certificate(tmp_path, "known_model") is None


def test_symlinked_artifact_cannot_escape_the_bundle(tmp_path: Path) -> None:
    """A link planted in the bundle must not turn a valid id into an escape."""
    outside = tmp_path.parent / "outside-bundle"
    outside.mkdir(exist_ok=True)
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    (bundle / "linked_model.certificate.json").symlink_to(outside / "cert.json")
    with pytest.raises(CompatibilityError):
        load_certificate(bundle, "linked_model")
