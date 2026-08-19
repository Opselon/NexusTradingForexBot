"""
Candidate Verification
======================
TASK-08 / 70D governance: FRESH, read-only re-verification of a candidate
before promotion (spec 7 / 34).

NEVER trust cached governance state. Before a promotion transaction may begin,
the candidate + its evidence are re-verified against the LIVE facts:

    * artifact exists and its hash matches the manifest
    * schema is registered and matches runtime expectations
    * input dimension matches the manifest
    * scaler is present, dimension-aligned, positive std
    * feature schema hash matches (when provided)
    * Liquidity algorithm version matches the dataset (when provided)
    * training commit / OOS artifact / shadow evidence recorded (when provided)
    * News / Liquidity dependency contracts are valid (spec 23)

Hard failures are reported per-gate (PROMOTION_BLOCKED_*) — a soft score never
overrides a hard failure (spec 19). This module performs NO writes and NO
runtime mutation.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.features.schema import FEATURE_SCHEMAS
from nexus_scalp.governance.load_gate import sha256_hex
from nexus_scalp.governance.models import (
    GovernanceErrorCode,
    GovernanceEvent,
    GovernanceStage,
)
from nexus_scalp.governance.store import GovernanceStore
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.governance.verify")

#: Gates of a fresh promotion precondition verification (spec 7 / 34).
VERIFY_GATES: tuple[str, ...] = (
    "artifact_exists",
    "artifact_hash_matches",
    "manifest_valid",
    "schema_registered",
    "schema_matches_runtime",
    "input_dimension_matches",
    "scaler_valid",
    "feature_schema_hash_matches",
    "liquidity_version_matches",
    "training_commit_recorded",
    "oos_artifact_recorded",
    "shadow_evidence_recorded",
    "news_contract_valid",
    "liquidity_contract_valid",
)


def verify_candidate(
    *,
    model_id: str,
    model_version: str,
    artifact_path: Path | str,
    scaler_path: Path | str = "",
    manifest: dict[str, Any] | None = None,
    runtime_schema_id: str = "",
    runtime_dimension: int = 0,
    feature_schema_hash: str = "",
    liquidity_algorithm_version: str = "",
    training_commit: str = "",
    oos_artifact: str = "",
    shadow_evidence: dict[str, Any] | None = None,
    news_contract: dict[str, Any] | None = None,
    liquidity_contract: dict[str, Any] | None = None,
    store: GovernanceStore | None = None,
    correlation_id: str = "",
) -> dict[str, Any]:
    """Fresh, read-only verification. Returns a per-gate verdict dict.

    Every gate gets an explicit PASS / FAIL / SKIP / INCONCLUSIVE status — a
    missing gate is NEVER silently GREEN (spec 18 / 19).

    When a fail occurs, a PROMOTION_BLOCKED governance event is recorded
    (best-effort; never raises on store failure).
    """
    art = Path(artifact_path)
    sca = Path(scaler_path) if scaler_path else Path(str(art) + ".scaler.npz")
    mf = manifest or {}
    results: dict[str, dict[str, Any]] = {}
    failures: list[str] = []
    skipped: list[str] = []
    unknown: list[str] = []

    def _gate(name: str, ok: bool, detail: str, status: str | None = None) -> None:
        st = status or ("PASS" if ok else "FAIL")
        results[name] = {"status": st, "detail": detail, "ok": ok}
        if st == "FAIL":
            failures.append(name)
        elif st == "SKIP":
            skipped.append(name)
        elif st == "INCONCLUSIVE":
            unknown.append(name)

    # 1. artifact exists
    _gate(
        "artifact_exists",
        art.exists() and art.stat().st_size > 0,
        f"path={art} size={art.stat().st_size if art.exists() else 0}",
    )

    # 2. artifact hash matches manifest
    actual_hash = sha256_hex(art) if art.exists() else ""
    declared_hash = str(mf.get("artifact_hash", "") or "")
    if not declared_hash:
        _gate("artifact_hash_matches", False, "manifest has no artifact_hash")
    else:
        _gate(
            "artifact_hash_matches",
            bool(actual_hash) and actual_hash == declared_hash,
            f"declared={declared_hash[:16]}… actual={actual_hash[:16]}…",
        )

    # 3. manifest valid
    required = ("model_id", "feature_schema_id", "feature_dimension", "class_count")
    missing = [k for k in required if k not in mf]
    _gate("manifest_valid", not missing, f"missing={missing or 'none'}")

    # 4. schema registered
    sid = str(mf.get("feature_schema_id", "") or "")
    registered = sid in {s.schema_id for s in FEATURE_SCHEMAS.list_schemas()}
    _gate("schema_registered", registered, f"schema_id={sid!r}")

    # 5. schema matches runtime
    if runtime_schema_id:
        _gate(
            "schema_matches_runtime",
            sid == runtime_schema_id,
            f"candidate={sid} runtime={runtime_schema_id}",
        )
    else:
        _gate("schema_matches_runtime", False, "runtime_schema_id not provided", "SKIP")

    # 6. input dimension matches
    declared_dim = int(mf.get("feature_dimension", 0) or 0)
    meta = mf.get("build_metadata", {}) or {}
    int(meta.get("input_dimension", declared_dim) or declared_dim)
    if runtime_dimension:
        _gate(
            "input_dimension_matches",
            declared_dim == runtime_dimension,
            f"declared={declared_dim} runtime={runtime_dimension}",
        )
    else:
        _gate("input_dimension_matches", False, "runtime_dimension not provided", "SKIP")

    # 7. scaler valid
    scaler_ok = False
    scaler_detail = "scaler file missing"
    if sca.exists():
        try:
            import numpy as np

            data = np.load(sca)
            mean = np.asarray(data["mean"], dtype=np.float32).reshape(-1)
            std = np.asarray(data["std"], dtype=np.float32).reshape(-1)
            scaler_ok = bool(
                mean.shape[0] == declared_dim and std.shape[0] == declared_dim and (std > 0).all()
            )
            scaler_detail = (
                f"dim={mean.shape[0]} expected={declared_dim} zero_std={bool((std <= 0).any())}"
            )
        except Exception as e:
            scaler_detail = f"scaler unreadable: {e}"
    _gate("scaler_valid", scaler_ok, scaler_detail)

    # 8. feature schema hash matches (when provided)
    if feature_schema_hash:
        manifest_schema_hash = str(mf.get("feature_schema_hash", "") or "")
        _gate(
            "feature_schema_hash_matches",
            bool(manifest_schema_hash) and manifest_schema_hash == feature_schema_hash,
            f"manifest={manifest_schema_hash[:16] or 'missing'} required={feature_schema_hash[:16]}",
        )
    else:
        _gate("feature_schema_hash_matches", False, "not provided", "SKIP")

    # 9. Liquidity algorithm version matches dataset (when provided)
    if liquidity_algorithm_version:
        declared_alg = str(mf.get("liquidity_algorithm_version", "") or "")
        _gate(
            "liquidity_version_matches",
            bool(declared_alg) and declared_alg == liquidity_algorithm_version,
            f"manifest={declared_alg or 'missing'} required={liquidity_algorithm_version}",
        )
    else:
        _gate("liquidity_version_matches", False, "not provided", "SKIP")

    # 10. training commit recorded
    if training_commit:
        commit = str(mf.get("training_commit", "") or mf.get("source_commit", "") or "")
        _gate(
            "training_commit_recorded",
            bool(commit),
            f"commit={commit[:16] or 'missing'}; required={training_commit[:16]}",
        )
    else:
        _gate("training_commit_recorded", False, "not provided", "SKIP")

    # 11. OOS artifact recorded
    if oos_artifact:
        oos_manifest = str(mf.get("oos_artifact", "") or mf.get("oos_result", "") or "")
        _gate("oos_artifact_recorded", bool(oos_manifest), f"oos={oos_manifest[:24] or 'missing'}")
    else:
        _gate("oos_artifact_recorded", False, "not provided", "SKIP")

    # 12. shadow evidence recorded (sample floor + health)
    if shadow_evidence:
        floor_ok = bool(shadow_evidence.get("sample_floor_met", False))
        _gate(
            "shadow_evidence_recorded",
            floor_ok,
            f"floor={shadow_evidence.get('samples_observed', 'n/a')} "
            f"required={shadow_evidence.get('samples_required', 'n/a')}",
        )
    else:
        _gate("shadow_evidence_recorded", False, "not provided", "SKIP")

    # 13. News contract valid (spec 23: the 70D model consumes Base+News+Liquidity)
    if news_contract is not None:
        news_ok = bool(news_contract.get("valid", False))
        _gate(
            "news_contract_valid",
            news_ok,
            f"detail={news_contract.get('detail', 'no detail')}",
        )
    else:
        _gate("news_contract_valid", False, "not provided", "SKIP")

    # 14. Liquidity contract valid
    if liquidity_contract is not None:
        liq_ok = bool(liquidity_contract.get("valid", False))
        _gate(
            "liquidity_contract_valid",
            liq_ok,
            f"detail={liquidity_contract.get('detail', 'no detail')}",
        )
    else:
        _gate("liquidity_contract_valid", False, "not provided", "SKIP")

    # ---------------------------------------------------------------
    # Eligibility (spec 18 / 19): EVERY mandatory gate must be explicit.
    # A SKIPPED gate means the mandatory evidence was NOT provided — that is
    # INSUFFICIENT_EVIDENCE, never GREEN. A high score can never override a
    # hard failure or missing hard evidence.
    # ---------------------------------------------------------------
    eligible = not failures and not skipped
    reason = ""
    if failures:
        reason = f"promotion blocked by: {', '.join(failures)}"
    elif skipped:
        reason = f"INSUFFICIENT_EVIDENCE: mandatory gates not evidenced: {', '.join(skipped)}"
    elif unknown:
        reason = f"inconclusive gates: {', '.join(unknown)}"

    if not eligible and store is not None:
        try:
            store.record_event(
                GovernanceEvent(
                    event_id=f"ev_verify_{model_id}_{model_version}",
                    event=GovernanceErrorCode.PROMOTION_BLOCKED.value,
                    stage=GovernanceStage.PROMOTION,
                    model_id=model_id,
                    model_version=model_version,
                    correlation_id=correlation_id,
                    error_code="PROMOTION_BLOCKED_VERIFICATION",
                    actor="system",
                    previous_state="",
                    new_state="",
                    reason=reason or "candidate verification failed",
                    payload={
                        "gate": "VERIFY_CANDIDATE",
                        "failures": failures,
                        "skipped": skipped,
                        "unknown": unknown,
                    },
                )
            )
        except Exception:
            pass

    logger.info(
        "[GOVERNANCE] event=VERIFY_CANDIDATE",
        model_id=model_id,
        eligible=eligible,
        failures=failures,
        skipped=skipped,
    )
    return {
        "model_id": model_id,
        "model_version": model_version,
        "eligible": eligible,
        "failures": failures,
        "skipped": skipped,
        "unknown": unknown,
        "gates": results,
        "reason": reason,
        "checked_at": datetime.now(UTC).isoformat(),
    }
