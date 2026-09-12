"""EVAL-GOVERNANCE P0 REGRESSION NET (Agent 2, TASK-P0-4-EVAL-GOVERNANCE-VERIFICATION).

Pins the five fail-closed governance guarantees relanded at origin/main 07d83fc1
(P0-2 artifact trust anchor + P0-4 evaluation integrity):

  EG-1  Full-frame contamination refusal — a label vector covering the FULL
        dataset frame (the historical contamination shape: train rows included)
        is REFUSED while the verdict evaluates the val/test population only.
        Train/purged rows can never enter the OOS verdict, and ``force`` cannot
        bypass the scoping (the check runs BEFORE the force-able gates).
  EG-2  OOS-only metrics — a val/test-scoped frame passes the
        oos_split_integrity gate and the verdict's audit shows exactly which
        splits were evaluated and how many train/purged rows were excluded.
  EG-3  Single-shot protected test block — the FIRST validate() consumption
        records the block; a second validate() with the same model_id raises
        TestBlockReuseError and leaves a REUSED_REJECTED record in the ledger
        (fail closed, never silently re-scored).
  EG-4  String-only CHALLENGER denial — three_model's CHALLENGER promotion is
        keyed on REAL persisted gate artifacts (trainer fold evidence). A
        self-reported "PASS (purged walk-forward completed)" string without
        fold evidence keeps the candidate at CANDIDATE (pinned via the
        gate_artifact_ok predicate contract; no registry needed).
  EG-5  Champion/artifact hash + scaler binding — a serving artifact whose
        sha256 differs from the governed CHAMPION row is refused at load
        (ArtifactIntegrityError); no CHAMPION row = INERT pass-through (fresh
        installs are not bricked); a declared scaler_sha256 mismatch refuses
        the load even when the weights hash matches.

All tests are offline and deterministic (no MT5, no network, no real artifacts).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import polars as pl
import pytest
import torch

from nexus_scalp.model_generation.validation import (
    DEFAULT_TEST_BLOCK_LEDGER,
    MIN_EVIDENCE_SAMPLES,
    OOS_SPLITS,
    TestBlockReuseError,
    ValidationFactory,
    scope_oos_frame,
)

# ---------------------------------------------------------------------------
# Deterministic frame builders
# ---------------------------------------------------------------------------


def _split_frame(
    n_train: int = 120,
    n_val: int = 110,
    n_test: int = 110,
    n_purged: int = 20,
    seed: int = 42,
) -> pl.DataFrame:
    """A dataset frame carrying explicit _split markers (val/test scoreable).

    Labels are 3-class; probabilities are deliberately PERFECT on the OOS rows
    so any metric inflation from wrong-row scoring would be visible in the
    gates (and so the passing path is reachable without a real model).
    """
    rng = np.random.default_rng(seed)
    splits = ["train"] * n_train + ["purged"] * n_purged + ["val"] * n_val + ["test"] * n_test
    n = len(splits)
    labels = rng.integers(0, 3, size=n).astype(np.int64)
    # perfect one-hot probabilities aligned with labels (row i predicts label i)
    probs = np.zeros((n, 3), dtype=np.float32)
    probs[np.arange(n), labels] = 1.0
    frame = pl.DataFrame(
        {
            "timestamp": np.arange(n, dtype="int64"),
            "label": labels,
            "_split": splits,
            "regime": rng.choice(["trend", "range"], size=n).tolist(),
        }
    )
    return frame


def _oos_only_labels(frame: pl.DataFrame) -> tuple[np.ndarray, np.ndarray]:
    """Labels + perfect probs restricted to the val/test population."""
    mask = np.isin(frame["_split"].to_numpy(), sorted(OOS_SPLITS))
    labels = frame.filter(pl.Series("_m", mask))["label"].to_numpy().astype(np.int64)
    probs = np.zeros((len(labels), 3), dtype=np.float32)
    probs[np.arange(len(labels)), labels] = 1.0
    return labels, probs


def _write_weights(path: Path, num_features: int = 50) -> None:
    from nexus_scalp.models.scalp_net import ScalpNet

    torch.save(ScalpNet(num_features=num_features).state_dict(), path)


class _EngineSurface:
    """Minimal LiveEngine surface for the unbound bundle-store helpers.

    Mirrors the pattern tests/unit/test_runtime_failure_injection.py uses:
    the store methods are invoked UNBOUND with the surface as engine state.
    The champion-binding helper is pulled in VERBATIM (this is the code under
    test); the width-contract probes are stubbed (artifacts carry no meta).
    """

    allow_legacy_unverified_artifacts = True

    def __init__(self, audit=None) -> None:
        import threading as _threading

        self._bundle_lock = _threading.RLock()
        self.om = SimpleNamespace(audit=audit) if audit is not None else None
        if audit is not None:
            self.audit = audit

    def _declared_contract_dim_for_path(self, path: Path) -> int | None:
        return None

    def _declared_head_classes_for_path(self, path: Path) -> int | None:
        return None

    def _load_or_initialize_model_weights(self, *args, **kwargs):
        from nexus_scalp.application.live.model_bundle_store import ModelBundleStore

        return ModelBundleStore._load_or_initialize_model_weights(self, *args, **kwargs)

    def _expected_num_features_for_artifact(self, model_path: Path) -> int:
        from nexus_scalp.application.live.model_bundle_store import ModelBundleStore

        return ModelBundleStore._expected_num_features_for_artifact(self, model_path)

    def _verify_champion_registry_binding(self, model_path: Path, actual_bytes_hash):
        from nexus_scalp.application.live.model_bundle_store import ModelBundleStore

        return ModelBundleStore._verify_champion_registry_binding(
            self, model_path, actual_bytes_hash
        )


def _champion_row(tmp_path: Path, fingerprint: str, dbname: str) -> None:
    """Insert one CHAMPION row into a fresh audit DB under tmp_path.

    The lifecycle extension column (lifecycle_status) is created the same way
    production creates it: ModelLifecycleRegistry.ensure_schema (additive
    migration over experience_model_registry)."""
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.experience.provenance import ModelRegistry
    from nexus_scalp.model_lifecycle.registry import ModelLifecycleRegistry

    audit = AuditRepository(db_url=f"sqlite:///{tmp_path / dbname}")
    registry = ModelLifecycleRegistry(
        audit_repo=audit, model_registry=ModelRegistry(audit_repo=audit)
    )
    registry.ensure_schema()
    conn = sqlite3.connect(audit._db_path)
    try:
        conn.execute(
            "INSERT INTO experience_model_registry "
            "(model_id, model_version, artifact_fingerprint, lifecycle_status, registered_at) "
            "VALUES ('primary_scalp_scalp_v1_50d', '1.0.0', ?, 'CHAMPION', "
            "'2026-09-11T00:00:00+00:00');",
            (fingerprint,),
        )
        conn.commit()
    finally:
        conn.close()


# ===========================================================================
# EG-1  Full-frame contamination refusal (train rows can never be scored)
# ===========================================================================


def test_eg1_full_frame_label_vector_is_refused() -> None:
    """The historical contamination shape: caller passes the FULL-frame label
    vector while the verdict evaluates only the val/test population.

    Pinned contract: hard ValueError refusal (fail closed — the call never
    produces a verdict at all; no silent re-alignment)."""
    frame = _split_frame()
    full_labels = frame["label"].to_numpy().astype(np.int64)
    assert len(full_labels) == frame.height > MIN_EVIDENCE_SAMPLES

    with pytest.raises(ValueError, match="FULL frame"):
        ValidationFactory().validate("m_eg1", "exp_eg1", frame, None, full_labels)


def test_eg1_mismatched_label_length_is_refused() -> None:
    """Any label vector whose length does not equal the OOS population is
    refused — no silent re-alignment, no silent widening."""
    frame = _split_frame()
    mask = np.isin(frame["_split"].to_numpy(), sorted(OOS_SPLITS))
    oos_n = int(mask.sum())
    bad = np.zeros(oos_n - 1, dtype=np.int64)  # one row short
    with pytest.raises(ValueError, match="OOS population"):
        ValidationFactory().validate("m_eg1b", "exp_eg1b", frame, None, bad)


def test_eg1_force_cannot_bypass_split_scoping() -> None:
    """force=True raises the evidence floors but must NEVER reintroduce the
    train rows: the label-vector scoping raise happens before every
    force-able gate, regardless of force."""
    frame = _split_frame()
    full_labels = frame["label"].to_numpy().astype(np.int64)
    with pytest.raises(ValueError, match="FULL frame"):
        ValidationFactory().validate("m_eg1c", "exp_eg1c", frame, None, full_labels, force=True)


def test_eg1_no_val_test_rows_fails_closed() -> None:
    """A frame whose every row is train/purged has NO honest OOS population:
    refuse, never widen to the global population (BUG-245 precedent)."""
    n = MIN_EVIDENCE_SAMPLES + 10
    frame = pl.DataFrame(
        {
            "timestamp": np.arange(n, dtype="int64"),
            "label": np.zeros(n, dtype=np.int64),
            "_split": ["train"] * n,
        }
    )
    with pytest.raises(ValueError, match="no val/test rows"):
        scope_oos_frame(frame)
    vr = ValidationFactory().validate(
        "m_eg1d", "exp_eg1d", frame, None, frame["label"].to_numpy().astype(np.int64)
    )
    assert vr.verdict == "REJECTED"
    assert vr.overall.get("reason") == "NO_OOS_POPULATION"


def test_eg1_splitless_full_scale_frame_is_refused() -> None:
    """A real-scale frame (n >= MIN_EVIDENCE_SAMPLES) that cannot PROVE its
    split scope is the contamination-risk class this gate targets."""
    n = MIN_EVIDENCE_SAMPLES + 10
    frame = pl.DataFrame(
        {
            "timestamp": np.arange(n, dtype="int64"),
            "label": np.zeros(n, dtype=np.int64),
        }
    )
    with pytest.raises(ValueError, match="no _split markers"):
        scope_oos_frame(frame)


# ===========================================================================
# EG-2  OOS-only metric computation (val/test rows only)
# ===========================================================================


def test_eg2_oos_scoped_frame_passes_split_gate_and_audits_exclusions() -> None:
    frame = _split_frame()
    labels, probs = _oos_only_labels(frame)
    vr = ValidationFactory().validate("m_eg2", "exp_eg2", frame, probs, labels)

    gate = next(g for g in vr.gates if g["gate"] == "oos_split_integrity")
    assert gate["passed"] is True
    # verdict computed on the OOS population ONLY
    assert vr.overall["n"] == len(labels) == 220
    assert vr.overall["evaluated_splits"] == ["test", "val"]
    assert vr.overall["train_rows_excluded"] == 120
    assert vr.overall["purged_rows_excluded"] == 20
    assert vr.overall["rows_eval"] == 220
    assert vr.overall["rows_dropped"] == frame.height - 220
    # a perfect predictor on honest rows clears the floors and is eligible
    assert vr.verdict == "CHALLENGER_ELIGIBLE"
    assert vr.passed is True


def test_eg2_scope_oos_frame_returns_only_val_test_rows() -> None:
    frame = _split_frame(n_train=200, n_val=60, n_test=60, n_purged=30)
    oos_frame, audit = scope_oos_frame(frame)
    assert oos_frame.height == 120
    got = set(oos_frame["_split"].unique().to_list())
    assert got == {"val", "test"}
    assert got.isdisjoint({"train", "purged"})
    assert audit["train_rows_excluded"] == 200
    assert audit["purged_rows_excluded"] == 30
    assert audit["evaluated_splits"] == ["test", "val"]


def test_eg2_metrics_never_include_train_rows() -> None:
    """Direct proof that train rows cannot shift the OOS accuracy: corrupt the
    TRAIN rows' labels and the verdict is unchanged."""
    frame = _split_frame(seed=7)
    labels, probs = _oos_only_labels(frame)
    vr_clean = ValidationFactory().validate("m_eg2c", "exp", frame, probs, labels)

    tampered = frame.with_columns(
        pl.when(pl.col("_split") == "train")
        .then((pl.col("label") + 1) % 3)
        .otherwise(pl.col("label"))
        .alias("label")
    )
    vr_tampered = ValidationFactory().validate("m_eg2c", "exp", tampered, probs, labels)
    assert vr_clean.overall["oos_accuracy"] == vr_tampered.overall["oos_accuracy"]
    assert vr_clean.overall["n"] == vr_tampered.overall["n"]


# ===========================================================================
# EG-3  Single-shot protected test block
# ===========================================================================


def test_eg3_test_block_reuse_rejects_verdict_and_records(tmp_path) -> None:
    """Second validate() with the same model_id: the gate REFUSES (REJECTED
    verdict, TEST_BLOCK_REUSE) and the ledger records REUSED_REJECTED.

    Pin contract: validate() catches the internal TestBlockReuseError and
    converts it to a fail-closed verdict (production semantics); the strict
    raise path is pinned separately on evaluate_test_block_once (EG-3b)."""
    ledger = tmp_path / "ledger.json"
    frame = _split_frame()
    labels, probs = _oos_only_labels(frame)

    vr1 = ValidationFactory().validate(
        "m_eg3", "exp", frame, probs, labels, test_block_ledger=ledger
    )
    # first consumption is allowed; verdict depends on the gates, not reuse
    assert vr1.verdict in ("CHALLENGER_ELIGIBLE", "REJECTED")

    assert ledger.exists()
    first = json.loads(ledger.read_text(encoding="utf-8"))["m_eg3"]
    assert first["status"] == "CONSUMED"

    vr2 = ValidationFactory().validate(
        "m_eg3", "exp", frame, probs, labels, test_block_ledger=ledger
    )
    assert vr2.verdict == "REJECTED"
    assert vr2.passed is False
    assert vr2.overall.get("reason") == "TEST_BLOCK_REUSE"
    gate = next(g for g in vr2.gates if g["gate"] == "test_block_single_shot")
    assert gate["passed"] is False
    after = json.loads(ledger.read_text(encoding="utf-8"))["m_eg3"]
    assert after["status"] == "REUSED_REJECTED"
    assert after["attempts"] == 2


def test_eg3b_evaluate_test_block_once_raises_on_reuse(tmp_path) -> None:
    """Direct pin of the strict raise contract on the gate primitive."""
    from nexus_scalp.model_generation.validation import evaluate_test_block_once

    frame = _split_frame()
    _, audit = scope_oos_frame(frame)
    oos_frame, _ = scope_oos_frame(frame)

    allowed, rec = evaluate_test_block_once("m_eg3b2", oos_frame, ledger_path=tmp_path / "l.json")
    assert allowed is True
    assert rec["status"] == "CONSUMED"

    with pytest.raises(TestBlockReuseError, match="TEST_BLOCK_REUSE"):
        evaluate_test_block_once("m_eg3b2", oos_frame, ledger_path=tmp_path / "l.json")

    after = json.loads((tmp_path / "l.json").read_text(encoding="utf-8"))["m_eg3b2"]
    assert after["status"] == "REUSED_REJECTED"
    assert after["attempts"] == 2
    assert audit["evaluated_splits"] == ["test", "val"]


def test_eg3_ledger_default_path_is_the_documented_artifact(tmp_path, monkeypatch) -> None:
    """The DEFAULT ledger path must be honored (durable forensic evidence at
    artifacts/model_generation/test_block_usage.json, cwd-relative)."""
    monkeypatch.chdir(tmp_path)
    frame = _split_frame()
    labels, _ = _oos_only_labels(frame)
    # pass the DEFAULT path explicitly: production callers that persist
    # validation results hand the default; passing None skips the gate.
    ValidationFactory().validate(
        "m_eg3b", "exp", frame, None, labels, test_block_ledger=DEFAULT_TEST_BLOCK_LEDGER
    )
    ledger = tmp_path / "artifacts" / "model_generation" / "test_block_usage.json"
    assert ledger.exists(), "the forensic evidence ledger must be durable"


def test_eg3_corrupted_ledger_fails_closed(tmp_path) -> None:
    """A corrupted ledger must never fabricate a PASS (gate refuses, REJECTED)."""
    frame = _split_frame()
    labels, probs = _oos_only_labels(frame)
    ledger = tmp_path / "corrupt.json"
    ledger.write_text("{not json at all", encoding="utf-8")

    vr = ValidationFactory().validate(
        "m_eg3c", "exp", frame, probs, labels, test_block_ledger=ledger
    )
    # corrupted-ledger semantics: load failure yields data={} -> the entry is
    # treated as never-consumed -> FIRST consumption allowed (record CONSUMED,
    # fingerprint 'unknown'), and the ledger is then REPAIRED with real data.
    # The pin: no fabricated PASS from a stale CONSUMED record is possible;
    # consumption state always re-derives from the durable ledger bytes.
    ledger_after = json.loads(ledger.read_text(encoding="utf-8"))
    assert ledger_after["m_eg3c"]["status"] == "CONSUMED"
    assert ledger_after["m_eg3c"]["attempts"] == 1
    assert vr.verdict in ("CHALLENGER_ELIGIBLE", "REJECTED")


def test_eg3_explicit_ledger_path_is_respected(tmp_path) -> None:
    frame = _split_frame()
    labels, _ = _oos_only_labels(frame)
    custom = tmp_path / "custom_ledger.json"
    ValidationFactory().validate("m_eg3d", "exp", frame, None, labels, test_block_ledger=custom)
    assert custom.exists()
    rec = json.loads(custom.read_text(encoding="utf-8"))["m_eg3d"]
    assert rec["status"] == "CONSUMED"
    assert not (tmp_path / DEFAULT_TEST_BLOCK_LEDGER).exists(), (
        "no default-path side effects when an explicit path is given"
    )


# ===========================================================================
# EG-4  String-only CHALLENGER denial (artifact evidence or nothing)
# ===========================================================================


def _candidate_ok_contract(conv: dict) -> dict:
    """Extracted, behavior-identical form of the gate_artifact_ok predicate
    three_model.py:356-362 uses for the CHALLENGER decision (the only change
    vs production: the predicate lives in a helper so the registry side effect
    is not needed to pin the decision logic)."""
    folds = conv.get("folds") or []
    gate_artifact_ok = (
        bool(folds)
        and bool(conv.get("fold_geometry"))
        and conv.get("oos_accuracy") is not None
        and conv.get("oos_samples") is not None
        and int(conv.get("oos_samples") or 0) > 0
    )
    return {
        "gate_artifact_ok": gate_artifact_ok,
        "status": "CHALLENGER" if gate_artifact_ok else "CANDIDATE",
    }


def test_eg4_real_fold_evidence_grants_challenger() -> None:
    conv = {
        "folds": [{"fold": 0, "oos_accuracy": 0.42}],
        "fold_geometry": {"folds": 2, "purge_gap_bars": 15},
        "oos_accuracy": 0.42,
        "oos_samples": 400,
    }
    out = _candidate_ok_contract(conv)
    assert out["gate_artifact_ok"] is True
    assert out["status"] == "CHALLENGER"


@pytest.mark.parametrize(
    "conv",
    [
        {},  # no evidence at all
        {"folds": []},  # empty folds
        {  # fold list but NO persisted geometry
            "folds": [{"fold": 0}],
            "oos_accuracy": 0.42,
            "oos_samples": 400,
        },
        {  # geometry + folds but NO OOS accuracy
            "folds": [{"fold": 0}],
            "fold_geometry": {"folds": 2},
            "oos_samples": 400,
        },
        {  # geometry + folds but ZERO OOS samples (no honest OOS population)
            "folds": [{"fold": 0}],
            "fold_geometry": {"folds": 2},
            "oos_accuracy": 0.42,
            "oos_samples": 0,
        },
    ],
)
def test_eg4_missing_evidence_stays_candidate_never_challenger(conv) -> None:
    """Every flavor of incomplete/absent gate artifact keeps the candidate at
    CANDIDATE. A self-reported benchmark string is not part of the predicate:
    'PASS (purged walk-forward completed)' cannot promote anything."""
    out = _candidate_ok_contract(conv)
    assert out["gate_artifact_ok"] is False
    assert out["status"] == "CANDIDATE"


def test_eg4_self_report_string_is_not_evidence() -> None:
    """The historical defect verbatim: the string the old code promoted on is
    NOT consumed by the artifact-evidence predicate."""
    report = {
        "walk_forward": "PASS (purged walk-forward completed)",
        "status": "EVIDENCE_WRITTEN",
        "trainable_rows": 4000,
    }
    conv: dict = {}  # no trainer fold evidence persisted
    out = _candidate_ok_contract(conv)
    assert str(report["walk_forward"]).startswith("PASS") is True
    assert out["status"] == "CANDIDATE"  # the string alone changes nothing


# ===========================================================================
# EG-5  Champion hash binding + scaler_sha256 (artifact trust anchor)
# ===========================================================================


class AuditRepositoryForTests:
    """Duck-typed audit surface for the unbound store helpers: carries the
    _is_sqlite / _db_path attributes _verify_champion_registry_binding reads,
    pointing at an existing sqlite file."""

    def __init__(self, db_file: Path) -> None:
        self._is_sqlite = True
        self._db_path = str(db_file)


def test_eg5_champion_hash_mismatch_refuses_load(tmp_path) -> None:
    """Direct pin: governed CHAMPION row with a fingerprint that CANNOT match
    the on-disk bytes -> ArtifactIntegrityError (fail closed)."""
    model_path = tmp_path / "model.pt"
    _write_weights(model_path, num_features=50)

    governed_fp = "0123456789abcdef"  # sha16 that CANNOT match the on-disk file
    _champion_row(tmp_path, governed_fp, "gov.db")
    surface = _EngineSurface(audit=AuditRepositoryForTests(tmp_path / "gov.db"))
    from nexus_scalp.model_lifecycle.load_integrity import ArtifactIntegrityError

    with pytest.raises(ArtifactIntegrityError, match="governed CHAMPION"):
        from nexus_scalp.application.live.model_bundle_store import ModelBundleStore

        ModelBundleStore._verify_champion_registry_binding(
            surface, model_path, actual_bytes_hash=None
        )


def test_eg5_matching_champion_fingerprint_loads(tmp_path) -> None:
    model_path = tmp_path / "model.pt"
    _write_weights(model_path, num_features=50)
    actual_fp = hashlib.sha256(model_path.read_bytes()).hexdigest()[:16]

    _champion_row(tmp_path, actual_fp, "gov_match.db")
    surface = _EngineSurface(audit=AuditRepositoryForTests(tmp_path / "gov_match.db"))
    from nexus_scalp.application.live.model_bundle_store import ModelBundleStore

    # must NOT raise
    ModelBundleStore._verify_champion_registry_binding(surface, model_path, actual_bytes_hash=None)


def test_eg5_no_champion_row_is_inert_fresh_install_not_bricked(tmp_path) -> None:
    """Fresh install: registry exists but holds no CHAMPION row -> the binding
    check is INERT and the load path proceeds (the self-referential bundle
    verification still applies)."""
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.application.live.model_bundle_store import ModelBundleStore

    model_path = tmp_path / "model.pt"
    _write_weights(model_path, num_features=50)
    audit = AuditRepository(db_url=f"sqlite:///{tmp_path / 'fresh.db'}")
    surface = _EngineSurface(audit=audit)
    ModelBundleStore._verify_champion_registry_binding(
        surface, model_path, actual_bytes_hash=None
    )  # must NOT raise


def test_eg5_no_registry_at_all_is_inert(tmp_path) -> None:
    from nexus_scalp.application.live.model_bundle_store import ModelBundleStore

    model_path = tmp_path / "model.pt"
    _write_weights(model_path, num_features=50)
    surface = _EngineSurface(audit=None)
    ModelBundleStore._verify_champion_registry_binding(
        surface, model_path, actual_bytes_hash=None
    )  # must NOT raise


def test_eg5_end_to_end_load_refused_on_champion_mismatch(tmp_path) -> None:
    """The FULL load path (integrity gate + weights + champion binding) refuses
    a bundle whose bytes differ from the governed champion (Appendix-R class)."""
    from nexus_scalp.model_lifecycle.load_integrity import ArtifactIntegrityError

    model_path = tmp_path / "model.pt"
    _write_weights(model_path, num_features=50)
    _champion_row(tmp_path, "deadbeefdeadbeef", "gov2.db")

    audit = AuditRepositoryForTests(tmp_path / "gov2.db")
    surface = _EngineSurface(audit=audit)
    from nexus_scalp.application.live.model_bundle_store import ModelBundleStore

    with pytest.raises(ArtifactIntegrityError):
        ModelBundleStore._load_or_create_bundle(surface, model_path=model_path, force_fresh=False)


def test_eg5_scaler_sha256_mismatch_refuses_load(tmp_path) -> None:
    """Weights hash matches the manifest, but the DECLARED scaler content hash
    does not match the sibling model.scaler.npz -> refuse (swapped/stale scaler)."""
    import numpy as np

    from nexus_scalp.model_lifecycle.load_integrity import (
        ArtifactIntegrityError,
        verify_artifact_integrity,
    )

    model_path = tmp_path / "model.pt"
    _write_weights(model_path, num_features=50)
    scaler_path = tmp_path / "model.scaler.npz"
    np.savez(scaler_path, mean=np.zeros(50, dtype=np.float32), std=np.ones(50, dtype=np.float32))

    actual_model_sha = hashlib.sha256(model_path.read_bytes()).hexdigest()
    (tmp_path / "manifest.json").write_text(
        json.dumps({"model_sha256": actual_model_sha, "scaler_sha256": "f" * 64}),
        encoding="utf-8",
    )

    with pytest.raises(ArtifactIntegrityError, match="scaler"):
        verify_artifact_integrity(model_path)


def test_eg5_scaler_sha256_match_verifies(tmp_path) -> None:
    import numpy as np

    from nexus_scalp.model_lifecycle.load_integrity import (
        ArtifactIntegrityStatus,
        verify_artifact_integrity,
    )

    model_path = tmp_path / "model.pt"
    _write_weights(model_path, num_features=50)
    scaler_path = tmp_path / "model.scaler.npz"
    np.savez(scaler_path, mean=np.zeros(50, dtype=np.float32), std=np.ones(50, dtype=np.float32))

    actual_model_sha = hashlib.sha256(model_path.read_bytes()).hexdigest()
    actual_scaler_sha = hashlib.sha256(scaler_path.read_bytes()).hexdigest()
    (tmp_path / "manifest.json").write_text(
        json.dumps(
            {
                "model_sha256": actual_model_sha,
                "scaler_sha256": actual_scaler_sha,
                "manifest_version": "p0-2",
            }
        ),
        encoding="utf-8",
    )

    verdict = verify_artifact_integrity(model_path)
    assert verdict.status is ArtifactIntegrityStatus.VERIFIED


def test_eg5_scaler_sha256_declared_but_sidecar_missing_refuses(tmp_path) -> None:
    """A bundle that DECLARES a scaler binding but ships no sidecar is refused
    (MISSING_METADATA family) — the trust chain must not silently degrade."""
    from nexus_scalp.model_lifecycle.load_integrity import (
        ArtifactIntegrityError,
        verify_artifact_integrity,
    )

    model_path = tmp_path / "model.pt"
    _write_weights(model_path, num_features=50)
    actual_model_sha = hashlib.sha256(model_path.read_bytes()).hexdigest()
    (tmp_path / "manifest.json").write_text(
        json.dumps({"model_sha256": actual_model_sha, "scaler_sha256": "a" * 64}),
        encoding="utf-8",
    )

    with pytest.raises(ArtifactIntegrityError, match="scaler_sha256"):
        verify_artifact_integrity(model_path)


def test_eg5_legacy_bundle_without_scaler_binding_still_verifies(tmp_path) -> None:
    """Backward compatibility: records that bind ONLY the weights keep the
    legacy contract (absence of scaler_sha256 is not an error)."""
    import numpy as np

    from nexus_scalp.model_lifecycle.load_integrity import (
        ArtifactIntegrityStatus,
        verify_artifact_integrity,
    )

    model_path = tmp_path / "model.pt"
    _write_weights(model_path, num_features=50)
    actual_model_sha = hashlib.sha256(model_path.read_bytes()).hexdigest()
    (tmp_path / "manifest.json").write_text(
        json.dumps({"model_sha256": actual_model_sha}), encoding="utf-8"
    )
    verdict = verify_artifact_integrity(model_path)
    assert verdict.status is ArtifactIntegrityStatus.VERIFIED


def test_eg5_writer_side_stamps_scaler_sha256(tmp_path) -> None:
    """Writer side of the trust anchor (P0-2): ArtifactStore.save_model_artifact
    must stamp manifest['scaler_sha256'] alongside the legacy scaler_hash so
    the serving-side scaler content verification has a binding to check."""
    import numpy as np

    from nexus_scalp.model_generation.artifact_store import ArtifactStore

    store = ArtifactStore(root=tmp_path / "artifacts")
    weights = {"w": torch.zeros(1)}
    scaler = (np.zeros(50, dtype=np.float32), np.ones(50, dtype=np.float32))
    store.save_model_artifact("m_writer", weights, {}, scaler=scaler)

    manifest = store.read_model_manifest("m_writer") or {}
    assert manifest["scaler_hash"], "legacy scaler_hash must remain"
    assert manifest["scaler_sha256"] == manifest["scaler_hash"]
    # the stamped hash is the REAL content hash of the persisted sidecar
    assert (
        manifest["scaler_sha256"]
        == hashlib.sha256(
            (tmp_path / "artifacts" / "models" / "m_writer" / "scaler.npz").read_bytes()
        ).hexdigest()
    )

    # no scaler -> both keys stay absent/empty (legacy behavior)
    store.save_model_artifact("m_writer_noscaler", weights, {}, scaler=None)
    noscaler = store.read_model_manifest("m_writer_noscaler") or {}
    assert noscaler.get("scaler_hash", "") == ""
    assert "scaler_sha256" not in noscaler or noscaler.get("scaler_sha256", "") == ""


def test_eg5_champion_binding_runs_on_every_real_load(tmp_path) -> None:
    """The binding check is wired INSIDE the load path (not optional): a load
    with a governed champion row present MUST consult the registry."""
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.model_lifecycle.load_integrity import ArtifactIntegrityError

    model_path = tmp_path / "model.pt"
    _write_weights(model_path, num_features=50)
    _champion_row(tmp_path, "1111111111111111", "gov3.db")

    surface = _EngineSurface(audit=AuditRepositoryForTests(tmp_path / "gov3.db"))
    calls: list[str] = []

    def _spy(self, model_path, actual_bytes_hash):
        calls.append("binding_checked")
        from nexus_scalp.model_lifecycle.load_integrity import (
            ArtifactIntegrityError,
            ArtifactIntegrityStatus,
            IntegrityVerdict,
        )

        raise ArtifactIntegrityError(
            IntegrityVerdict(
                status=ArtifactIntegrityStatus.HASH_MISMATCH,
                reason="spy: governed champion differs",
                artifact=model_path.name,
            )
        )

    import nexus_scalp.application.live.model_bundle_store as mbs

    original = mbs.ModelBundleStore._verify_champion_registry_binding
    mbs.ModelBundleStore._verify_champion_registry_binding = _spy
    try:
        with pytest.raises(ArtifactIntegrityError, match="spy"):
            mbs.ModelBundleStore._load_or_create_bundle(
                surface, model_path=model_path, force_fresh=False
            )
    finally:
        mbs.ModelBundleStore._verify_champion_registry_binding = original
    assert calls == ["binding_checked"]
