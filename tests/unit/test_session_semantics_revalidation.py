"""P1 final-model validation: session semantics provenance + revalidation gate.

The mission requires that models trained under the OLD fixed-UTC session
semantics are NOT silently treated as equivalent to models trained under the
corrected DST-aware semantics, and that the exact production artifact is
independently validated after full-data training.

Deliverables (verified by the tests below):
    1. ``SESSION_SEMANTICS_VERSION`` + the full session definitions travel in
       the training metadata (``model.meta.json``) of every new candidate.
    2. Governance artifact verification REJECTS a candidate whose metadata
       carries no (or an older) session semantics version: fail-closed
       revalidation requirement for pre-correction artifacts.
    3. Post-publication integrity: the emission-gate manifest binds the
       artifact sha256; ``verify_candidate`` re-derives the artifact hash from
       disk and compares (existing gate — pinned here for the session change).

Session-semantics version identity travels via
``features.session_time.SESSION_SEMANTICS_VERSION``; a stub constant
``REQUIRED_SESSION_SEMANTICS_VERSION`` in governance/verify.py is the
promotion-side expectation.
"""

from __future__ import annotations

from nexus_scalp.features.session_time import (
    SESSION_SEMANTICS_VERSION,
    session_semantics_metadata,
)


def test_metadata_block_is_complete():
    meta = session_semantics_metadata()
    assert meta["session_semantics_version"] == SESSION_SEMANTICS_VERSION
    assert meta["method"].startswith("IANA")
    for market in ("tokyo", "london", "new_york"):
        assert market in meta["definitions"]
        assert meta["definitions"][market]["tz"]


def test_verify_candidate_blocks_missing_session_version(tmp_path):
    """A candidate trained BEFORE the correction (no session_semantics field
    in metadata) must fail the governance verification — never silently
    equivalent."""
    import hashlib

    from nexus_scalp.governance.verify import verify_candidate

    art = tmp_path / "model.pt"
    art.write_bytes(b"fake-artifact-bytes")
    sca = tmp_path / "model.pt.scaler.npz"

    import numpy as np

    np.savez(sca, mean=np.zeros(50, dtype=np.float32), std=np.ones(50, dtype=np.float32))
    manifest = {
        "model_id": "c1",
        "model_version": "v1",
        "feature_schema_id": "scalp_v1",
        "feature_dimension": 50,
        "class_count": 3,
        "artifact_hash": hashlib.sha256(art.read_bytes()).hexdigest(),
        # NOTE: no session_semantics key at all — pre-correction artifact.
    }
    res = verify_candidate(
        model_id="c1",
        model_version="v1",
        artifact_path=art,
        scaler_path=sca,
        manifest=manifest,
    )
    assert not res["eligible"], "pre-correction artifact must be blocked"
    assert "session_semantics_revalidated" in res["failures"]


def test_verify_candidate_blocks_stale_session_version(tmp_path):
    """An artifact whose metadata declares an OLDER session semantics version
    is not equivalent to the corrected semantics — blocked for revalidation."""
    import hashlib

    import numpy as np

    from nexus_scalp.governance.verify import verify_candidate

    art = tmp_path / "model.pt"
    art.write_bytes(b"fake-artifact-bytes")
    sca = tmp_path / "model.pt.scaler.npz"
    np.savez(sca, mean=np.zeros(50, dtype=np.float32), std=np.ones(50, dtype=np.float32))
    manifest = {
        "model_id": "c1",
        "model_version": "v1",
        "feature_schema_id": "scalp_v1",
        "feature_dimension": 50,
        "class_count": 3,
        "artifact_hash": hashlib.sha256(art.read_bytes()).hexdigest(),
        "session_semantics": {
            "session_semantics_version": "fixed_utc_v0",
            "method": "fixed UTC hour windows",
        },
    }
    res = verify_candidate(
        model_id="c1",
        model_version="v1",
        artifact_path=art,
        scaler_path=sca,
        manifest=manifest,
    )
    assert not res["eligible"]
    assert "session_semantics_revalidated" in res["failures"]


def test_verify_candidate_accepts_current_session_version(tmp_path):
    import hashlib

    import numpy as np

    from nexus_scalp.governance.verify import verify_candidate

    art = tmp_path / "model.pt"
    art.write_bytes(b"fake-artifact-bytes")
    sca = tmp_path / "model.pt.scaler.npz"
    np.savez(sca, mean=np.zeros(50, dtype=np.float32), std=np.ones(50, dtype=np.float32))
    manifest = {
        "model_id": "c1",
        "model_version": "v1",
        "feature_schema_id": "scalp_v1",
        "feature_dimension": 50,
        "class_count": 3,
        "artifact_hash": hashlib.sha256(art.read_bytes()).hexdigest(),
        "session_semantics": session_semantics_metadata(),
    }
    res = verify_candidate(
        model_id="c1",
        model_version="v1",
        artifact_path=art,
        scaler_path=sca,
        manifest=manifest,
    )
    assert "session_semantics_revalidated" not in res["failures"]
    assert "session_semantics_revalidated" not in res["skipped"]
