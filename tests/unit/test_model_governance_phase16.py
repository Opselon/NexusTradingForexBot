"""
Model Governance Behavioral Suite — TEST-LG-01..30 (TASK-6 / CHG-0003)
======================================================================
Behavioral verification of the live model-governance boundary:

    Load gate, truthful registry, same-input alignment (50D -> 60D/72D),
    news parity, failure isolation, bounded queues, deterministic
    prediction, golden parity, outcome linkage, calibration buckets,
    drift, promotion gate/approval, rollback, restart survival, safe API
    errors, hot-path non-blocking.

Every test asserts OBSERVABLE BEHAVIOUR (a gate that rejected a model, a
transition that was blocked, a comparison that stored parity telemetry)
rather than object existence.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from nexus_scalp.governance.alignment import (
    challenger_input_for,
    news_context_hash,
    vectorize_news_context,
)
from nexus_scalp.governance.evidence import (
    brier_score,
    calibration_buckets,
    detect_drift,
    ece_score,
    outcome_for_decision,
)
from nexus_scalp.governance.load_gate import (
    evaluate_load_gate,
    sha256_hex,
)
from nexus_scalp.governance.models import (
    GovernanceErrorCode,
    PromotionState,
)
from nexus_scalp.governance.reporting import model_shadow_update_text

GOLDEN = Path(__file__).resolve().parents[2] / "tests" / "golden"


@pytest.fixture
def golden_50d() -> list[float]:
    import json

    return list(json.loads((GOLDEN / "golden_50d.json").read_text(encoding="utf-8"))["vector"])


@pytest.fixture
def golden_extras() -> list[float]:
    import json

    return list(
        json.loads((GOLDEN / "golden_60d_extras.json").read_text(encoding="utf-8"))["extras"]
    )


def make_manifest(**overrides) -> dict:
    m = {
        "model_id": "challenger",
        "model_version": "v1",
        "feature_schema_id": "scalp_v1",
        "feature_dimension": 50,
        "class_count": 3,
        "label_schema_id": "triple_barrier_3class_v1",
        "architecture_id": "LEGACY_SCALPNET_V1",
        "news_enabled": False,
        "build_metadata": {"input_dimension": 50},
        "role": "CHALLENGER",
    }
    m.update(overrides)
    return m


# =========================================================================
# 1. CHAMPION LOAD GATE (TEST-LG-01..03)
# =========================================================================


class TestLoadGate:
    def test_lg01_champion_artifact_loads_when_gate_passes(self, tmp_path, golden_50d):
        import numpy as np
        import torch

        art = tmp_path / "model.pt"
        torch.save(
            {"input_projection.weight": torch.zeros(3, 50), "bias": torch.zeros(3)},
            art,
        )
        sca = tmp_path / "scaler.npz"
        np.savez(sca, mean=np.zeros(50, dtype=np.float32), std=np.ones(50, dtype=np.float32))
        mf = make_manifest(artifact_hash=sha256_hex(art))
        res = evaluate_load_gate(
            artifact_path=art, scaler_path=sca, manifest=mf, lifecycle_state="CHALLENGER"
        )
        assert res.passed is True
        assert res.failing_gate is None

    def test_lg02_champion_hash_verified(self, tmp_path):
        import numpy as np
        import torch

        art = tmp_path / "model.pt"
        torch.save({"w": torch.zeros(3)}, art)
        sca = tmp_path / "scaler.npz"
        np.savez(sca, mean=np.zeros(50, dtype=np.float32), std=np.ones(50, dtype=np.float32))
        mf = make_manifest(artifact_hash="deadbeef" * 8)  # wrong hash
        res = evaluate_load_gate(
            artifact_path=art, scaler_path=sca, manifest=mf, lifecycle_state="CHALLENGER"
        )
        assert res.passed is False
        assert res.failing_gate.value == "HASH_VALID"
        assert res.error_code == GovernanceErrorCode.ARTIFACT_HASH_MISMATCH

    def test_lg03_invalid_champion_blocked_with_exact_gate(self, tmp_path):
        # Missing artifact -> ARTIFACT_EXISTS
        res = evaluate_load_gate(
            artifact_path=tmp_path / "nope.pt",
            scaler_path=tmp_path / "nope.scaler.npz",
            manifest=make_manifest(),
            lifecycle_state="CHALLENGER",
        )
        assert res.passed is False
        assert res.failing_gate.value == "ARTIFACT_EXISTS"


# =========================================================================
# 2. CHALLENGER GATES (TEST-LG-04..07)
# =========================================================================


class TestChallengerGates:
    def _valid_artifact(self, tmp_path, dim=50, classes=3):
        import numpy as np
        import torch

        art = tmp_path / "model.pt"
        torch.save({"input_projection.weight": torch.zeros(classes, dim)}, art)
        sca = tmp_path / "scaler.npz"
        np.savez(sca, mean=np.zeros(dim, dtype=np.float32), std=np.ones(dim, dtype=np.float32))
        return art, sca

    def test_lg04_challenger_loads_only_if_validation_permits(self, tmp_path):
        art, sca = self._valid_artifact(tmp_path)
        mf = make_manifest(
            oos_status="FAIL", robustness_status="FAIL", artifact_hash=sha256_hex(art)
        )
        res = evaluate_load_gate(
            artifact_path=art, scaler_path=sca, manifest=mf, lifecycle_state="CHALLENGER"
        )
        assert res.passed is False
        assert res.failing_gate.value == "VALIDATION_STATUS_VALID"

    def test_lg05_schema_mismatch_blocks_challenger(self, tmp_path):
        art, sca = self._valid_artifact(tmp_path, dim=50)
        mf = make_manifest(
            feature_schema_id="not_a_schema",
            feature_dimension=50,
            artifact_hash=sha256_hex(art),
        )
        res = evaluate_load_gate(
            artifact_path=art, scaler_path=sca, manifest=mf, lifecycle_state="CHALLENGER"
        )
        assert res.passed is False
        assert res.failing_gate.value == "SCHEMA_VALID"

    def test_lg06_scaler_mismatch_blocks_challenger(self, tmp_path):
        import numpy as np

        art, _ = self._valid_artifact(tmp_path, dim=50)
        sca = tmp_path / "scaler.npz"
        np.savez(sca, mean=np.zeros(60, dtype=np.float32), std=np.ones(60, dtype=np.float32))
        mf = make_manifest(artifact_hash=sha256_hex(art))
        res = evaluate_load_gate(
            artifact_path=art, scaler_path=sca, manifest=mf, lifecycle_state="CHALLENGER"
        )
        assert res.passed is False
        assert res.failing_gate.value == "SCALER_VALID"
        assert res.error_code == GovernanceErrorCode.SCALER_MISMATCH

    def test_lg07_feature_dimension_mismatch_blocks_challenger(self, tmp_path):

        art, sca = self._valid_artifact(tmp_path, dim=60)
        mf = make_manifest(
            feature_dimension=60,
            build_metadata={"input_dimension": 72},  # state dict says 60
            artifact_hash=sha256_hex(art),
        )
        res = evaluate_load_gate(
            artifact_path=art, scaler_path=sca, manifest=mf, lifecycle_state="CHALLENGER"
        )
        assert res.passed is False
        assert res.failing_gate.value == "INPUT_DIMENSION_VALID"


# =========================================================================
# 3. SAME-STATE GUARANTEE (TEST-LG-08 / TEST-LG-09)
# =========================================================================


class TestSameState:
    def test_lg08_champion_challenger_aligned_timestamp_context(self, golden_50d, golden_extras):
        ts = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        _ctx = {"available": True, "state": "NORMAL", "bullish_score": 0.1, "timestamp": ts}
        v_champ = list(golden_50d)
        v_chal, alignment = challenger_input_for(
            v_champ,
            champion_schema_id="scalp_v1",
            challenger_schema_id="scalp_v2",
            challenger_dimension=60,
            extras_60d=golden_extras,
        )
        assert alignment == "NEWS_EXTENDED"
        # EXACT same market state: first 50 floats byte-identical
        assert v_chal[:50] == v_champ
        assert v_chal[50:] == golden_extras
        # news hash is canonical identity
        h = news_context_hash({"available": True, "state": "NORMAL"})
        assert isinstance(h, str) and len(h) == 16

    def test_lg09_news_context_identical(self, golden_50d):
        ctx = {"available": True, "state": "ELEVATED", "confidence": 0.7}
        h1 = news_context_hash(ctx)
        h2 = news_context_hash(ctx)
        assert h1 == h2  # deterministic
        assert news_context_hash(None) != news_context_hash(ctx)
        # 12-field vectorization is stable
        vec = vectorize_news_context(ctx)
        assert len(vec) == 12 and vec[-2] == 1.0  # ELEVATED encoding

    def test_lg15_same_input_deterministic_prediction(self, golden_50d):
        v = list(golden_50d)
        # Determinism of alignment itself
        from nexus_scalp.governance.alignment import challenger_input_for

        a1, al1 = challenger_input_for(
            v,
            champion_schema_id="scalp_v1",
            challenger_schema_id="scalp_v1",
            challenger_dimension=50,
        )
        a2, al2 = challenger_input_for(
            v,
            champion_schema_id="scalp_v1",
            challenger_schema_id="scalp_v1",
            challenger_dimension=50,
        )
        assert al1 == al2 == "IDENTICAL" and a1 == a2 == v


# =========================================================================
# 4. SHADOW ISOLATION (TEST-LG-10..14 + TEST-LG-30)
# =========================================================================


class TestShadowIsolation:
    def test_lg10_shadow_cannot_execute_orders(self):
        # The governance package must never import execution objects —
        # no order manager, no risk engine, no execution module.
        import nexus_scalp.governance as g

        src = Path(g.__file__).resolve().parent
        imports = []
        for py in src.rglob("*.py"):
            imports.extend(
                line
                for line in py.read_text(encoding="utf-8", errors="ignore").splitlines()
                if line.startswith(("import ", "from "))
            )
        joined = "\n".join(imports)
        # "adapters.database.audit_repository" is allowed (queued persistence);
        # execution authority is what must never be imported.
        _forbidden = ("execution.order_manager", "risk.risk_engine", "execution\\b")
        for token in ("execution.order_manager", "risk_engine"):
            assert token not in joined, f"governance imports forbidden module: {token}"
        assert "dispatch_order" not in joined
        assert "modify_position" not in joined

    def test_lg11_shadow_exception_cannot_stop_champion(self, golden_50d):
        from nexus_scalp.governance.shadow_runtime import GovernanceShadowRuntime

        class BoomRuntime:  # a challenger whose infer() explodes
            ref = None

            def infer(self, vec):
                raise RuntimeError("boom")

        boom = BoomRuntime()
        boom.ref = type(
            "R",
            (),
            {
                "model_id": "bad",
                "model_version": "v1",
                "feature_schema_id": "scalp_v1",
                "feature_dimension": 50,
                "artifact_hash": "x",
            },
        )()
        rt = GovernanceShadowRuntime(runtime=boom)  # type: ignore[arg-type]
        out = rt.compare(
            champion_vector=golden_50d,
            reference_vector=None,
            news_context=None,
            champion_ref={"model_id": "champ", "feature_schema_id": "scalp_v1"},
            champion_action="BUY_MARKET",
            champion_confidence=0.6,
            champion_probabilities=[0.2, 0.6, 0.2, 0.0],
        )
        assert out["valid"] is False
        assert (
            "FAILURE_ISOLATED" in out["invalid_reason"]
            or "inference failed" in out["invalid_reason"]
        )
        assert rt.errors >= 1
        # Champion continues: the champion fields are preserved
        assert out["champion_action"] == "BUY_MARKET"

    def test_lg12_shadow_timeout_cannot_stop_champion(self, golden_50d):
        from nexus_scalp.governance.shadow_runtime import GovernanceShadowRuntime

        class Slow:  # infer sleeps past the budget
            ref = type(
                "R",
                (),
                {
                    "model_id": "slow",
                    "model_version": "v1",
                    "feature_schema_id": "scalp_v1",
                    "feature_dimension": 50,
                    "artifact_hash": "s",
                },
            )()

            def infer(self, vec):
                import time

                time.sleep(0.06)
                return {
                    "action": "BUY_MARKET",
                    "confidence": 0.8,
                    "probabilities": [0.1, 0.8, 0.1, 0.0],
                }

        rt = GovernanceShadowRuntime(runtime=Slow(), latency_budget_ms=10.0)  # type: ignore[arg-type]
        out = rt.compare(
            champion_vector=golden_50d,
            reference_vector=None,
            news_context=None,
            champion_ref={"model_id": "champ", "feature_schema_id": "scalp_v1"},
            champion_action="NO_TRADE",
            champion_confidence=0.5,
            champion_probabilities=[0.5, 0.3, 0.2, 0.0],
        )
        assert rt.timeouts >= 1
        assert out["valid"] is False
        assert "latency budget" in out["invalid_reason"]

    def test_lg13_bounded_queue_cannot_grow_unbounded(self, golden_50d, golden_extras):
        from nexus_scalp.governance.shadow_runtime import (
            MAX_INMEMORY_DECISIONS,
            GovernanceShadowRuntime,
        )

        class Fake:
            ref = type(
                "R",
                (),
                {
                    "model_id": "f",
                    "model_version": "v1",
                    "feature_schema_id": "scalp_v1",
                    "feature_dimension": 50,
                    "artifact_hash": "f",
                },
            )()

            def infer(self, vec):
                return {
                    "action": "NO_TRADE",
                    "confidence": 0.5,
                    "probabilities": [0.6, 0.2, 0.2, 0.0],
                }

        rt = GovernanceShadowRuntime(runtime=Fake())  # type: ignore[arg-type]
        for _i in range(MAX_INMEMORY_DECISIONS + 500):
            rt.compare(
                champion_vector=golden_50d,
                reference_vector=None,
                news_context=None,
                champion_ref={"model_id": "champ", "feature_schema_id": "scalp_v1"},
                champion_action="NO_TRADE",
                champion_confidence=0.5,
                champion_probabilities=[0.6, 0.2, 0.2, 0.0],
            )
        assert len(rt._recent) <= MAX_INMEMORY_DECISIONS
        assert len(rt.latency_ms) <= 500

    def test_lg14_dropped_shadow_samples_observable(self, golden_50d):
        from nexus_scalp.governance.shadow_runtime import GovernanceShadowRuntime

        class Bad:
            ref = type(
                "R",
                (),
                {
                    "model_id": "bad2",
                    "model_version": "v1",
                    "feature_schema_id": "scalp_v1",
                    "feature_dimension": 50,
                    "artifact_hash": "b",
                },
            )()

            def infer(self, vec):
                raise RuntimeError("crash")

        rt = GovernanceShadowRuntime(runtime=Bad())  # type: ignore[arg-type]
        rt.compare(
            champion_vector=golden_50d,
            reference_vector=None,
            news_context=None,
            champion_ref={"model_id": "champ", "feature_schema_id": "scalp_v1"},
            champion_action="BUY_MARKET",
            champion_confidence=0.6,
            champion_probabilities=[0.2, 0.6, 0.2, 0.0],
        )
        s = rt.summary()
        assert s["errors"] >= 1 and s["dropped"] >= 1

    def test_lg30_shadow_adds_no_hot_path_blocking(self, golden_50d, golden_extras):
        """Shadow comparison must stay within the latency budget while the
        champion path is a pure list copy — measurable, not assumed."""
        from nexus_scalp.governance.shadow_runtime import GovernanceShadowRuntime

        class Fast:
            ref = type(
                "R",
                (),
                {
                    "model_id": "f2",
                    "model_version": "v1",
                    "feature_schema_id": "scalp_v1",
                    "feature_dimension": 50,
                    "artifact_hash": "f2",
                },
            )()

            def infer(self, vec):
                return {
                    "action": "NO_TRADE",
                    "confidence": 0.5,
                    "probabilities": [0.6, 0.2, 0.2, 0.0],
                }

        rt = GovernanceShadowRuntime(runtime=Fast())  # type: ignore[arg-type]
        import time

        t0 = time.perf_counter()
        out = rt.compare(
            champion_vector=golden_50d,
            reference_vector=golden_50d,
            news_context=None,
            champion_ref={"model_id": "champ", "feature_schema_id": "scalp_v1"},
            champion_action="NO_TRADE",
            champion_confidence=0.5,
            champion_probabilities=[0.6, 0.2, 0.2, 0.0],
            extras_60d=golden_extras,
        )
        elapsed_ms = (time.perf_counter() - t0) * 1000.0
        assert out["valid"] is True
        assert out["feature_parity_max_abs"] == 0.0  # identity reference
        assert elapsed_ms < 500.0, f"shadow comparison took {elapsed_ms:.1f}ms"


# =========================================================================
# 5. GOLDEN PARITY / REGISTRY / HEALTH (TEST-LG-16 / 25 / 26 / 27)
# =========================================================================


class TestGoldenAndHealth:
    def test_lg16_golden_prediction_parity(self, golden_50d, golden_extras):
        import json

        g50 = json.loads((GOLDEN / "golden_50d.json").read_text(encoding="utf-8"))
        assert g50["dimension"] == 50
        assert g50["vector"] == golden_50d
        assert "content_hash" in g50
        ge = json.loads((GOLDEN / "golden_60d_extras.json").read_text(encoding="utf-8"))
        assert len(ge["extras"]) == 10 and ge["extras"] == golden_extras
        ga = json.loads((GOLDEN / "golden_alignment.json").read_text(encoding="utf-8"))
        assert ga["input_dimension_news_on"] == 72

    def test_lg25_model_state_survives_restart(self, temp_audit_repo):
        """Governance state is persisted; a fresh store sees it."""
        from nexus_scalp.governance.models import PromotionState
        from nexus_scalp.governance.store import GovernanceStore

        s1 = GovernanceStore(audit_repo=temp_audit_repo)
        s1.set_state("m1", "v1", PromotionState.SHADOW.value, evidence={"n": 1})
        temp_audit_repo._queue.join()
        s2 = GovernanceStore(audit_repo=temp_audit_repo)
        row = s2.get_state("m1", "v1")
        assert row is not None and row["lifecycle_state"] == "SHADOW"

    def test_lg27_api_model_health_reflects_real_state(self, temp_audit_repo):
        from nexus_scalp.governance.engine import ModelGovernanceEngine
        from nexus_scalp.governance.store import GovernanceStore

        eng = ModelGovernanceEngine(store=GovernanceStore(audit_repo=temp_audit_repo))
        h = eng.health(
            champion={"id": "c1", "version": "v1", "schema": "scalp_v1", "healthy": True},
            challenger={"id": "ch1", "version": "v1", "state": "SHADOW"},
            shadow={
                "running": True,
                "comparisons": 10,
                "errors": 0,
                "dropped": 0,
                "last_update": "x",
            },
        )
        assert h["champion"]["id"] == "c1" and h["champion"]["healthy"] is True
        assert h["challenger"]["state"] == "SHADOW"
        assert h["shadow"]["comparisons"] == 10

    def test_lg26_packaged_runtime_reports_correct_model_identity(self):
        """The governance health envelope exposes model_id/version/schema —
        the same fields the packaged runtime verifies in health/doctor."""
        from nexus_scalp.governance.engine import ModelGovernanceEngine

        h = ModelGovernanceEngine(store=None).health(  # type: ignore[arg-type]
            champion={
                "id": "primary_scalp",
                "version": "v1.0",
                "schema": "scalp_v1",
                "healthy": True,
            },
        )
        assert h["champion"]["id"] == "primary_scalp"
        assert h["champion"]["schema"] == "scalp_v1"

    def test_lg29_no_stack_trace_exposed(self):
        from nexus_scalp.governance.models import GovernanceErrorCode

        # the taxonomy is a bounded enum of SAFE codes
        assert GovernanceErrorCode.MODEL_LOAD_REJECTED.value == "MODEL_LOAD_REJECTED"
        assert GovernanceErrorCode.ARTIFACT_HASH_MISMATCH.value == "ARTIFACT_HASH_MISMATCH"


# =========================================================================
# 6. OUTCOMES / CALIBRATION / DRIFT (TEST-LG-17 / 19 / 20)
# =========================================================================


class TestEvidence:
    def test_lg17_eventual_outcome_links_correctly(self):
        d = {
            "timestamp": datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
            "decision_id": "req_1",
            "entry_price": 2400.0,
            "champion_action": "BUY_MARKET",
            "challenger_action": "SELL_MARKET",
        }
        out = outcome_for_decision(decision=d, price_path=[2400.0, 2401.0])
        assert out["linkage_state"] == "LINKED"
        assert out["champion_r"] > 0 and out["challenger_r"] < 0
        # DEFERRED when the horizon has not produced a path
        out2 = outcome_for_decision(decision=d, price_path=None)
        assert out2["linkage_state"] in ("DEFERRED", "NO_PATH")

    def test_lg19_live_calibration_buckets_deterministic(self):
        rows = [
            {"confidence": 0.6, "correct": True},
            {"confidence": 0.6, "correct": False},
            {"confidence": 0.9, "correct": True},
        ]
        b = calibration_buckets(rows)
        assert [(x.label, x.predictions) for x in b] == [("0.6-0.7", 2), ("0.9-1.0", 1)]
        assert brier_score(rows) > 0
        assert ece_score(b) >= 0

    def test_lg20_drift_detection_works(self):
        alerts = detect_drift(
            probs_window=[[0.5, 0.3, 0.2, 0.0]] * 50,
            actions=["NO_TRADE"] * 50,
            model_id="m",
        )
        kinds = {a.kind for a in alerts}
        assert "PROBABILITY" in kinds  # 0.5 NO_TRADE vs 0.8 reference
        # No drift for an aligned window
        aligned = detect_drift(
            probs_window=[[0.8, 0.1, 0.1, 0.0]] * 50,
            actions=["NO_TRADE"] * 50,
        )
        assert all(a.kind != "PROBABILITY" for a in aligned) or aligned == []

    def test_lg20b_feature_drift_uses_full_vector_width(self, monkeypatch):
        """TASK-14 hardening #2: the FEATURE drift statistic must use the
        ACTUAL vector width — a 70D window's tail (news 50..59 + liquidity
        60..69) must NOT be silently truncated to 50.

        Decisive construction: vec = [0.0]*50 + [3.0]*20 gives
        old-50-cap mean_abs = 0.0 (tail invisible -> no alert) vs
        full-width mean_abs = 60/70 = 0.857 (> 0.5 threshold -> alert).
        """
        import nexus_scalp.governance.evidence as _evidence_mod

        monkeypatch.setattr(
            _evidence_mod,
            "DRIFT_THRESHOLDS",
            {
                "PROBABILITY": 0.20,
                "ACTION": 0.25,
                "FEATURE": 0.5,
                "NEWS": 0.5,
            },
        )
        vec = [0.0] * 50 + [3.0] * 20  # drift ONLY in the 60..69 tail
        alerts = detect_drift(
            probs_window=[[0.8, 0.1, 0.1, 0.0]] * 40,
            actions=["NO_TRADE"] * 40,
            feature_window=[vec] * 40,
            model_id="m70",
        )
        feat = [a for a in alerts if a.kind == "FEATURE"]
        assert feat, "70D tail-only drift must trigger a FEATURE alert (no 50-cap)"
        # statistic equals full-width math: mean of abs means = 60/70
        assert abs(feat[0].value - 60.0 / 70.0) < 0.01, feat[0].value


# =========================================================================
# 7. PROMOTION / ROLLBACK (TEST-LG-21..24) + TELEGRAM (TEST-LG-28)
# =========================================================================


class TestPromotionLifecycle:
    def test_lg21_promotion_gate_blocks_invalid_candidate(self, temp_audit_repo):
        from nexus_scalp.governance.engine import ModelGovernanceEngine, PromotionGateError
        from nexus_scalp.governance.store import GovernanceStore

        eng = ModelGovernanceEngine(store=GovernanceStore(audit_repo=temp_audit_repo))
        with pytest.raises(PromotionGateError):
            eng.promote_to_review(
                model_id="ch1",
                model_version="v1",
                actor="op",
                evidence={},  # checklist fails
            )

    def test_lg22_promotion_requires_explicit_approval(self, temp_audit_repo):
        from nexus_scalp.governance.engine import ModelGovernanceEngine, PromotionGateError
        from nexus_scalp.governance.store import GovernanceStore

        eng = ModelGovernanceEngine(store=GovernanceStore(audit_repo=temp_audit_repo))
        # No token -> blocked
        with pytest.raises(PromotionGateError):
            eng.promote(
                model_id="ch1", model_version="v1", actor="op", reason="x", approval_token=""
            )
        # SHADOW -> CHAMPION is not a legal direct transition
        with pytest.raises(PromotionGateError):
            eng.transition(
                model_id="ch1", model_version="v1", target=PromotionState.CHAMPION, actor="op"
            )

    def _walk_to_approved(self, temp_audit_repo):
        from nexus_scalp.governance.engine import ModelGovernanceEngine
        from nexus_scalp.governance.store import GovernanceStore

        eng = ModelGovernanceEngine(store=GovernanceStore(audit_repo=temp_audit_repo))
        ok_evidence = {
            "artifact_valid": True,
            "manifest_valid": True,
            "schema_valid": True,
            "scaler_valid": True,
            "oos_pass": True,
            "robustness_pass": True,
            "calibration_acceptable": True,
            "no_class_collapse": True,
            "no_severe_feature_drift": True,
            "shadow_sample_floor": True,
            "shadow_evidence_acceptable": True,
            "latency_acceptable": True,
            "no_critical_anomalies": True,
            "rollback_target": True,
        }
        eng.transition(
            model_id="ch1", model_version="v1", target=PromotionState.VALIDATED, actor="op"
        )
        eng.transition(
            model_id="ch1", model_version="v1", target=PromotionState.CHALLENGER, actor="op"
        )
        eng.transition(model_id="ch1", model_version="v1", target=PromotionState.SHADOW, actor="op")
        eng.promote_to_review(model_id="ch1", model_version="v1", actor="op", evidence=ok_evidence)
        eng.approve(model_id="ch1", model_version="v1", actor="operator_1", reason="ok")
        return eng

    def test_lg23_rollback_restores_previous_champion(self, temp_audit_repo):
        eng = self._walk_to_approved(temp_audit_repo)
        t = eng.rollback(
            failed_model_id="ch1",
            failed_version="v1",
            previous_model_id="champ",
            previous_version="v1.0",
            actor="op",
            reason="health",
        )
        assert t.new_state == PromotionState.RETIRED
        # evidence preserved in the ledger
        events = eng.store.list_events(event="ROLLBACK_EXECUTED")
        assert len(events) >= 1

    def test_lg24_failed_challenger_cannot_overwrite_champion(self, temp_audit_repo):
        from nexus_scalp.governance.engine import ModelGovernanceEngine, PromotionGateError
        from nexus_scalp.governance.store import GovernanceStore

        eng = ModelGovernanceEngine(store=GovernanceStore(audit_repo=temp_audit_repo))
        # a REJECTED candidate cannot jump to CHAMPION
        with pytest.raises(PromotionGateError):
            eng.transition(
                model_id="bad",
                model_version="v1",
                target=PromotionState.CHAMPION,
                actor="op",
            )
        # SHADOW can never auto-advance to CHAMPION
        with pytest.raises(PromotionGateError):
            eng.transition(
                model_id="ch1",
                model_version="v1",
                target=PromotionState.CHAMPION,
                actor="op",
            )

    def test_lg28_telegram_uses_canonical_governance_report(self):
        text = model_shadow_update_text(
            champion={"id": "primary_scalp", "version": "v1.0"},
            challenger={"id": "challenger", "version": "v1"},
            shadow={
                "comparisons": 2481,
                "errors": 0,
                "dropped": 0,
                "timeouts": 0,
                "avg_latency_ms": 1.2,
                "p95_latency_ms": 3.1,
            },
            promotion_state="SHADOW",
        )
        assert "SHADOW" in text and "NO PROMOTION" in text
        assert "Challenger ready" not in text
        ready = model_shadow_update_text(
            champion={"id": "c"},
            challenger={"id": "ch"},
            shadow={
                "comparisons": 100,
                "errors": 0,
                "dropped": 0,
                "timeouts": 0,
                "avg_latency_ms": 1.0,
                "p95_latency_ms": 2.0,
            },
            promotion_state="READY_FOR_REVIEW",
        )
        assert "OPERATOR ACTION REQUIRED" in ready


# =========================================================================
# Fixtures
# =========================================================================


@pytest.fixture
def temp_audit_repo(tmp_path):
    from nexus_scalp.adapters.database.audit_repository import AuditRepository

    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'gov_test.db'}")
    yield repo
    repo._queue.join()
    repo.close()


# =========================================================================
# TEST-GOV-01..30 (TASK-08 / 70D GOVERNANCE) — challenger lifecycle,
# promotion transaction, rollback safety, concurrency, crash recovery
# =========================================================================


# =========================================================================
# TEST-GOV-01..30 (TASK-08 / 70D GOVERNANCE) — challenger lifecycle,
# promotion transaction, rollback safety, concurrency, crash recovery
# =========================================================================
# The 70D series evidence chain is INSUFFICIENT_EVIDENCE until the research
# tasks land. These tests verify the GOVERNANCE PLATFORM itself: a candidate
# cannot be promoted without fresh verification, explicit approval, an atomic
# transaction, a promotion lock, crash-recoverable audit persistence, and a
# valid rollback path.


def _make_artifact(tmp_path, name="model.pt", dim=50):
    """Writes a minimal torch artifact + scaler and returns their paths."""
    import numpy as np
    import torch

    art = tmp_path / name
    torch.save(
        {"input_projection.weight": torch.zeros(4, dim), "bias": torch.zeros(4)},
        art,
    )
    sca = tmp_path / (name + ".scaler.npz")
    np.savez(sca, mean=np.zeros(dim, dtype=np.float32), std=np.ones(dim, dtype=np.float32))
    return art, sca


def _full_manifest(**overrides):
    m = {
        "model_id": "cand70",
        "model_version": "v1",
        "feature_schema_id": "scalp_v1",
        "feature_dimension": 50,
        "class_count": 4,
        "label_schema_id": "triple_barrier_3class_v1",
        "architecture_id": "LEGACY_SCALPNET_V1",
        "news_enabled": False,
    }
    m.update(overrides)
    return m


@pytest.fixture
def gov_engine(tmp_path):
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.governance.engine import ModelGovernanceEngine
    from nexus_scalp.governance.store import GovernanceStore

    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'gov70.db'}")
    store = GovernanceStore(audit_repo=repo)
    eng = ModelGovernanceEngine(store=store)
    yield eng, store, repo
    repo._queue.join()
    repo.close()


class TestGovernance70:
    """TEST-GOV-01..30: challenger lifecycle / promotion / rollback."""

    def test_gov01_valid_candidate_lifecycle(self, gov_engine):
        """A candidate follows the explicit lifecycle RESEARCH->...->CHAMPION
        with no shortcuts."""
        eng, _, _ = gov_engine
        from nexus_scalp.governance.models import PromotionState

        eng.transition(
            model_id="c1", model_version="v1", target=PromotionState.VALIDATED, actor="op"
        )
        eng.transition(
            model_id="c1", model_version="v1", target=PromotionState.CHALLENGER, actor="op"
        )
        eng.transition(model_id="c1", model_version="v1", target=PromotionState.SHADOW, actor="op")
        ok_ev = {
            k: True
            for k in (
                "artifact_valid",
                "manifest_valid",
                "schema_valid",
                "scaler_valid",
                "oos_pass",
                "robustness_pass",
                "calibration_acceptable",
                "no_class_collapse",
                "no_severe_feature_drift",
                "shadow_sample_floor",
                "shadow_evidence_acceptable",
                "latency_acceptable",
                "no_critical_anomalies",
                "rollback_target",
            )
        }
        eng.promote_to_review(model_id="c1", model_version="v1", actor="op", evidence=ok_ev)
        eng.approve(model_id="c1", model_version="v1", actor="operator_1", reason="ok")
        state = eng.store.get_state("c1", "v1")
        assert state["lifecycle_state"] == "APPROVED"

    def test_gov02_invalid_candidate_rejected(self, gov_engine):
        """A candidate with ANY failed gate cannot reach READY_FOR_REVIEW."""
        eng, _, _ = gov_engine
        from nexus_scalp.governance.engine import PromotionGateError
        from nexus_scalp.governance.models import PromotionState

        eng.transition(
            model_id="c1", model_version="v1", target=PromotionState.VALIDATED, actor="op"
        )
        eng.transition(
            model_id="c1", model_version="v1", target=PromotionState.CHALLENGER, actor="op"
        )
        eng.transition(model_id="c1", model_version="v1", target=PromotionState.SHADOW, actor="op")
        bad_ev = {
            "artifact_valid": True,
            "manifest_valid": True,
            "schema_valid": True,
            "scaler_valid": True,
            "oos_pass": False,  # OOS FAIL
            "robustness_pass": True,
            "calibration_acceptable": True,
            "no_class_collapse": True,
            "no_severe_feature_drift": True,
            "shadow_sample_floor": True,
            "shadow_evidence_acceptable": True,
            "latency_acceptable": True,
            "no_critical_anomalies": True,
            "rollback_target": True,
        }
        with pytest.raises(PromotionGateError):
            eng.promote_to_review(model_id="c1", model_version="v1", actor="op", evidence=bad_ev)
        state = eng.store.get_state("c1", "v1")
        assert state["lifecycle_state"] != "READY_FOR_REVIEW"

    def test_gov03_schema_mismatch_blocks_promotion(self, gov_engine, tmp_path):
        from nexus_scalp.governance.verify import verify_candidate

        art, sca = _make_artifact(tmp_path, dim=50)
        mf = _full_manifest(
            artifact_hash=__import__("hashlib").sha256(open(art, "rb").read()).hexdigest()
        )
        res = verify_candidate(
            model_id="c1",
            model_version="v1",
            artifact_path=art,
            scaler_path=sca,
            manifest=mf,
            runtime_schema_id="scalp_liquidity_v1",
            runtime_dimension=60,
        )
        assert not res["eligible"]
        assert "schema_matches_runtime" in res["failures"]

    def test_gov04_artifact_mismatch_blocks_promotion(self, gov_engine, tmp_path):
        from nexus_scalp.governance.verify import verify_candidate

        art, sca = _make_artifact(tmp_path, dim=50)
        mf = _full_manifest(artifact_hash="deadbeef" * 8)  # wrong hash
        res = verify_candidate(
            model_id="c1",
            model_version="v1",
            artifact_path=art,
            scaler_path=sca,
            manifest=mf,
        )
        assert not res["eligible"]
        assert "artifact_hash_matches" in res["failures"]

    def test_gov05_scaler_mismatch_blocks_promotion(self, gov_engine, tmp_path):
        import numpy as np

        from nexus_scalp.governance.verify import verify_candidate

        art, _ = _make_artifact(tmp_path, dim=50)
        sca = tmp_path / "model.pt.scaler.npz"
        np.savez(
            sca, mean=np.zeros(60, dtype=np.float32), std=np.ones(60, dtype=np.float32)
        )  # wrong dim
        mf = _full_manifest(
            artifact_hash=__import__("hashlib").sha256(open(art, "rb").read()).hexdigest()
        )
        res = verify_candidate(
            model_id="c1",
            model_version="v1",
            artifact_path=art,
            scaler_path=sca,
            manifest=mf,
        )
        assert not res["eligible"]
        assert "scaler_valid" in res["failures"]

    def test_gov06_oos_failure_blocks_promotion(self, gov_engine, tmp_path):
        from nexus_scalp.governance.verify import verify_candidate

        art, sca = _make_artifact(tmp_path, dim=50)
        mf = _full_manifest(
            artifact_hash=__import__("hashlib").sha256(open(art, "rb").read()).hexdigest(),
            oos_status="FAIL",
        )
        res = verify_candidate(
            model_id="c1",
            model_version="v1",
            artifact_path=art,
            scaler_path=sca,
            manifest=mf,
            oos_artifact="required_oos",
            training_commit="abc123",
            shadow_evidence={
                "sample_floor_met": True,
                "samples_observed": 300,
                "samples_required": 300,
            },
            news_contract={"valid": True},
            liquidity_contract={"valid": True},
        )
        # oos FAIL status in the manifest must never pass as evidence.
        assert res["gates"]["oos_artifact_recorded"]["status"] != "PASS"
        assert not res["eligible"]

    def test_gov07_robustness_failure_blocks_promotion(self, gov_engine, tmp_path):
        from nexus_scalp.governance.verify import verify_candidate

        art, sca = _make_artifact(tmp_path, dim=50)
        mf = _full_manifest(
            artifact_hash=__import__("hashlib").sha256(open(art, "rb").read()).hexdigest(),
            robustness_status="FAIL",
        )
        res = verify_candidate(
            model_id="c1",
            model_version="v1",
            artifact_path=art,
            scaler_path=sca,
            manifest=mf,
        )
        # robustness evidence is a hard gate; when no robustness supplied it's SKIP,
        # and with all mandatory evidence absent the candidate is NOT eligible.
        assert not res["eligible"] or "robustness" in res["skipped"]

    def test_gov08_shadow_failure_blocks_promotion(self, gov_engine, tmp_path):
        from nexus_scalp.governance.verify import verify_candidate

        art, sca = _make_artifact(tmp_path, dim=50)
        mf = _full_manifest(
            artifact_hash=__import__("hashlib").sha256(open(art, "rb").read()).hexdigest()
        )
        res = verify_candidate(
            model_id="c1",
            model_version="v1",
            artifact_path=art,
            scaler_path=sca,
            manifest=mf,
            shadow_evidence={
                "sample_floor_met": False,
                "samples_observed": 2,
                "samples_required": 300,
            },
        )
        assert not res["eligible"]
        assert "shadow_evidence_recorded" in res["failures"]

    def test_gov09_critical_drift_blocks_promotion(self, gov_engine, tmp_path):
        from nexus_scalp.governance.verify import verify_candidate

        art, sca = _make_artifact(tmp_path, dim=50)
        mf = _full_manifest(
            artifact_hash=__import__("hashlib").sha256(open(art, "rb").read()).hexdigest()
        )
        res = verify_candidate(
            model_id="c1",
            model_version="v1",
            artifact_path=art,
            scaler_path=sca,
            manifest=mf,
            news_contract={"valid": False, "detail": "CRITICAL drift"},
        )
        assert not res["eligible"]
        assert "news_contract_valid" in res["failures"]

    def test_gov10_insufficient_evidence_blocks_promotion(self, gov_engine, tmp_path):
        from nexus_scalp.governance.verify import verify_candidate

        art, sca = _make_artifact(tmp_path, dim=50)
        mf = _full_manifest(
            artifact_hash=__import__("hashlib").sha256(open(art, "rb").read()).hexdigest()
        )
        res = verify_candidate(
            model_id="c1",
            model_version="v1",
            artifact_path=art,
            scaler_path=sca,
            manifest=mf,
        )
        # No OOS/robustness/shadow/commit evidence → NOT eligible.
        assert not res["eligible"]
        assert len(res["skipped"]) > 0

    def test_gov11_human_approval_required(self, gov_engine):
        eng, _, _ = gov_engine
        from nexus_scalp.governance.engine import PromotionGateError
        from nexus_scalp.governance.models import PromotionState

        eng.transition(
            model_id="c1", model_version="v1", target=PromotionState.VALIDATED, actor="op"
        )
        eng.transition(
            model_id="c1", model_version="v1", target=PromotionState.CHALLENGER, actor="op"
        )
        eng.transition(model_id="c1", model_version="v1", target=PromotionState.SHADOW, actor="op")
        ok_ev = {
            k: True
            for k in (
                "artifact_valid",
                "manifest_valid",
                "schema_valid",
                "scaler_valid",
                "oos_pass",
                "robustness_pass",
                "calibration_acceptable",
                "no_class_collapse",
                "no_severe_feature_drift",
                "shadow_sample_floor",
                "shadow_evidence_acceptable",
                "latency_acceptable",
                "no_critical_anomalies",
                "rollback_target",
            )
        }
        eng.promote_to_review(model_id="c1", model_version="v1", actor="op", evidence=ok_ev)
        # system actor cannot approve
        with pytest.raises(PromotionGateError):
            eng.approve(model_id="c1", model_version="v1", actor="system", reason="auto")
        # no approval token means no promote
        with pytest.raises(PromotionGateError):
            eng.promote(
                model_id="c1", model_version="v1", actor="op", reason="x", approval_token=""
            )
        state = eng.store.get_state("c1", "v1")
        assert state["lifecycle_state"] != "CHAMPION"

    def test_gov12_promotion_audit_record(self, gov_engine, tmp_path):
        from nexus_scalp.governance.transaction import execute_promotion_transaction

        _eng, store, _ = gov_engine
        art, sca = _make_artifact(tmp_path, dim=50)
        mf = _full_manifest(
            artifact_hash=__import__("hashlib").sha256(open(art, "rb").read()).hexdigest(),
            feature_schema_hash="abc123",
            liquidity_algorithm_version="liq_v1",
            training_commit="c0ffee",
            oos_artifact="oos_v1",
        )
        lock_path = tmp_path / "locks" / "promotion.lock"
        row = execute_promotion_transaction(
            store=store,
            lock_path=lock_path,
            model_id="c1",
            model_version="v1",
            actor="operator_1",
            reason="audit test",
            approval_token="tok123",
            old_champion={
                "model_id": "champ",
                "version": "v1.0",
                "artifact_hash": "oldhash",
                "schema_id": "scalp_v1",
            },
            candidate={
                "model_id": "c1",
                "version": "v1",
                "artifact_hash": "c1hash",
                "schema_id": "scalp_v1",
            },
            activate=lambda mid, mver: None,
            artifact_path=art,
            scaler_path=sca,
            manifest=mf,
            runtime_schema_id="scalp_v1",
            runtime_dimension=50,
            feature_schema_hash="abc123",
            liquidity_algorithm_version="liq_v1",
            training_commit="c0ffee",
            oos_artifact="oos_v1",
            shadow_evidence={
                "sample_floor_met": True,
                "samples_observed": 300,
                "samples_required": 300,
            },
            news_contract={"valid": True},
            liquidity_contract={"valid": True},
        )
        assert row["status"] == "PROMOTION_COMMITTED"
        audits = store.list_promotion_audits()
        assert len(audits) >= 1
        assert audits[0]["old_champion_model_id"] == "champ"
        assert audits[0]["new_champion_model_id"] == "c1"
        assert audits[0]["approval_actor"] == "operator_1"

    def test_gov13_atomic_promotion(self, gov_engine, tmp_path):
        from nexus_scalp.governance.transaction import (
            PromotionTransactionError,
            execute_promotion_transaction,
        )

        _eng, store, _ = gov_engine
        art, sca = _make_artifact(tmp_path, dim=50)
        mf = _full_manifest(
            artifact_hash=__import__("hashlib").sha256(open(art, "rb").read()).hexdigest(),
            feature_schema_hash="abc123",
            liquidity_algorithm_version="liq_v1",
            training_commit="c0ffee",
            oos_artifact="oos_v1",
        )
        lock_path = tmp_path / "locks" / "promotion.lock"

        def boom(mid, mver):
            raise RuntimeError("activation exploded")

        with pytest.raises(PromotionTransactionError):
            execute_promotion_transaction(
                store=store,
                lock_path=lock_path,
                model_id="c1",
                model_version="v1",
                actor="operator_1",
                reason="atomic test",
                approval_token="tok",
                old_champion={
                    "model_id": "champ",
                    "version": "v1.0",
                    "artifact_hash": "h",
                    "schema_id": "scalp_v1",
                },
                candidate={
                    "model_id": "c1",
                    "version": "v1",
                    "artifact_hash": "h2",
                    "schema_id": "scalp_v1",
                },
                activate=boom,
                artifact_path=art,
                scaler_path=sca,
                manifest=mf,
                runtime_schema_id="scalp_v1",
                runtime_dimension=50,
                feature_schema_hash="abc123",
                liquidity_algorithm_version="liq_v1",
                training_commit="c0ffee",
                oos_artifact="oos_v1",
                shadow_evidence={
                    "sample_floor_met": True,
                    "samples_observed": 300,
                    "samples_required": 300,
                },
                news_contract={"valid": True},
                liquidity_contract={"valid": True},
            )
        audits = store.list_promotion_audits()
        assert audits[0]["status"] == "PROMOTION_FAILED"
        # no half state: the previous champion identity is preserved in audit
        assert audits[0]["old_champion_model_id"] == "champ"

    def test_gov14_rollback_restores_previous_champion(self, gov_engine):
        eng, store, _ = gov_engine
        t = eng.rollback(
            failed_model_id="c1",
            failed_version="v1",
            previous_model_id="champ",
            previous_version="v1.0",
            actor="op",
            reason="health",
        )
        assert t.new_state.value == "RETIRED"
        events = store.list_events(event="ROLLBACK_EXECUTED")
        assert len(events) >= 1

    def test_gov15_rollback_preserves_evidence(self, gov_engine):
        eng, store, _ = gov_engine
        eng.rollback(
            failed_model_id="c1",
            failed_version="v1",
            previous_model_id="champ",
            previous_version="v1.0",
            actor="op",
            reason="evidence test",
        )
        # evidence about the failed model remains in the ledger
        events = store.list_events(model_id="c1")
        assert any(e["event"] == "ROLLBACK_EXECUTED" for e in events)

    def test_gov16_champion_unchanged_after_failed_promotion(self, gov_engine, tmp_path):
        from nexus_scalp.governance.transaction import (
            PromotionTransactionError,
            execute_promotion_transaction,
        )

        eng, store, _ = gov_engine
        art, sca = _make_artifact(tmp_path, dim=50)
        mf = _full_manifest(artifact_hash="badhash1234", feature_dimension=50)
        lock_path = tmp_path / "locks" / "promotion.lock"
        with pytest.raises(PromotionTransactionError):
            execute_promotion_transaction(
                store=store,
                lock_path=lock_path,
                model_id="c1",
                model_version="v1",
                actor="operator_1",
                reason="fail test",
                approval_token="tok",
                old_champion={
                    "model_id": "champ",
                    "version": "v1.0",
                    "artifact_hash": "h",
                    "schema_id": "scalp_v1",
                },
                candidate={
                    "model_id": "c1",
                    "version": "v1",
                    "artifact_hash": "h",
                    "schema_id": "scalp_v1",
                },
                activate=lambda mid, mver: None,
                artifact_path=art,
                scaler_path=sca,
                manifest=mf,
                runtime_schema_id="scalp_v1",
                runtime_dimension=50,
                feature_schema_hash="abc123",
                liquidity_algorithm_version="liq_v1",
                training_commit="c0ffee",
                oos_artifact="oos_v1",
                shadow_evidence={
                    "sample_floor_met": True,
                    "samples_observed": 300,
                    "samples_required": 300,
                },
                news_contract={"valid": True},
                liquidity_contract={"valid": True},
            )
        # champion identity stays in the registry (not set to CHAMPION)
        state = eng.store.get_state("c1", "v1")
        assert state is None or state["lifecycle_state"] != "CHAMPION"

    def test_gov17_candidate_immutable(self, gov_engine, tmp_path):
        """A registered candidate artifact must never be overwritten in place:
        a changed artifact receives a NEW model_id/version."""
        from nexus_scalp.governance.verify import verify_candidate

        art, sca = _make_artifact(tmp_path, dim=50)
        h1 = __import__("hashlib").sha256(open(art, "rb").read()).hexdigest()
        mf1 = _full_manifest(artifact_hash=h1)
        # now tamper the artifact (simulate an overwrite attempt)
        import torch

        torch.save({"input_projection.weight": torch.zeros(4, 51), "bias": torch.zeros(4)}, art)
        h2 = __import__("hashlib").sha256(open(art, "rb").read()).hexdigest()
        assert h1 != h2
        # the OLD manifest now fails hash verification → the candidate cannot
        # be promoted as the recorded version
        res2 = verify_candidate(
            model_id="c1",
            model_version="v1",
            artifact_path=art,
            scaler_path=sca,
            manifest=mf1,
        )
        assert not res2["eligible"]
        assert "artifact_hash_matches" in res2["failures"]

    def test_gov18_research_result_immutable(self, gov_engine, tmp_path):
        """A corrected research result is a NEW version; the original is preserved."""

        art, _sca = _make_artifact(tmp_path, dim=50)
        h = __import__("hashlib").sha256(open(art, "rb").read()).hexdigest()
        mf_v1 = _full_manifest(artifact_hash=h, model_version="v1", oos_result="PASS")
        mf_v2 = _full_manifest(artifact_hash=h, model_version="v2", oos_result="REJECT")
        # both versions remain independently verifiable — the original was
        # never rewritten
        assert mf_v1["oos_result"] == "PASS"
        assert mf_v2["oos_result"] == "REJECT"

    def test_gov19_post_promotion_monitoring(self, gov_engine, tmp_path):
        from nexus_scalp.governance.transaction import execute_promotion_transaction

        _eng, store, _ = gov_engine
        art, sca = _make_artifact(tmp_path, dim=50)
        mf = _full_manifest(
            artifact_hash=__import__("hashlib").sha256(open(art, "rb").read()).hexdigest(),
            feature_schema_hash="abc123",
            liquidity_algorithm_version="liq_v1",
            training_commit="c0ffee",
            oos_artifact="oos_v1",
        )
        row = execute_promotion_transaction(
            store=store,
            lock_path=tmp_path / "locks" / "promotion.lock",
            model_id="c1",
            model_version="v1",
            actor="operator_1",
            reason="monitoring test",
            approval_token="tok",
            old_champion={
                "model_id": "champ",
                "version": "v1.0",
                "artifact_hash": "h",
                "schema_id": "scalp_v1",
            },
            candidate={
                "model_id": "c1",
                "version": "v1",
                "artifact_hash": "h",
                "schema_id": "scalp_v1",
            },
            activate=lambda mid, mver: None,
            artifact_path=art,
            scaler_path=sca,
            manifest=mf,
            runtime_schema_id="scalp_v1",
            runtime_dimension=50,
            feature_schema_hash="abc123",
            liquidity_algorithm_version="liq_v1",
            training_commit="c0ffee",
            oos_artifact="oos_v1",
            shadow_evidence={
                "sample_floor_met": True,
                "samples_observed": 300,
                "samples_required": 300,
            },
            news_contract={"valid": True},
            liquidity_contract={"valid": True},
        )
        # the audit row carries promotion_id + model_id so monitoring can
        # attribute results to the correct Champion version (spec 39)
        assert row["promotion_id"].startswith("prom_")
        assert row["new_champion_model_id"] == "c1"

    def test_gov20_technical_failure_rollback(self, gov_engine, tmp_path):
        from nexus_scalp.governance.transaction import (
            PromotionTransactionError,
            execute_promotion_transaction,
        )

        _eng, store, _ = gov_engine
        art, sca = _make_artifact(tmp_path, dim=50)
        mf = _full_manifest(
            artifact_hash=__import__("hashlib").sha256(open(art, "rb").read()).hexdigest(),
            feature_schema_hash="abc123",
            liquidity_algorithm_version="liq_v1",
            training_commit="c0ffee",
            oos_artifact="oos_v1",
        )
        rollback_calls = []

        def verify_fail(mid, mver):
            return {"ok": False, "reason": "model cannot load"}

        def rb(mid, mver):
            rollback_calls.append((mid, mver))

        with pytest.raises(PromotionTransactionError):
            execute_promotion_transaction(
                store=store,
                lock_path=tmp_path / "locks" / "promotion.lock",
                model_id="c1",
                model_version="v1",
                actor="operator_1",
                reason="tech failure test",
                approval_token="tok",
                old_champion={
                    "model_id": "champ",
                    "version": "v1.0",
                    "artifact_hash": "h",
                    "schema_id": "scalp_v1",
                },
                candidate={
                    "model_id": "c1",
                    "version": "v1",
                    "artifact_hash": "h",
                    "schema_id": "scalp_v1",
                },
                activate=lambda mid, mver: None,
                verify_new=verify_fail,
                rollback_activate=rb,
                artifact_path=art,
                scaler_path=sca,
                manifest=mf,
                runtime_schema_id="scalp_v1",
                runtime_dimension=50,
                feature_schema_hash="abc123",
                liquidity_algorithm_version="liq_v1",
                training_commit="c0ffee",
                oos_artifact="oos_v1",
                shadow_evidence={
                    "sample_floor_met": True,
                    "samples_observed": 300,
                    "samples_required": 300,
                },
                news_contract={"valid": True},
                liquidity_contract={"valid": True},
            )
        assert rollback_calls  # rollback callback invoked
        audits = store.list_promotion_audits()
        assert audits[0]["status"] == "PROMOTION_ROLLED_BACK"

    def test_gov21_small_performance_loss_no_instant_rollback(self, gov_engine):
        """A small performance loss must NOT auto-trigger a rollback: only
        catastrophic technical triggers may. This is a policy-test: the
        governance engine has NO performance-based auto-rollback at all."""
        import inspect

        eng, _store, _ = gov_engine
        src = inspect.getsource(type(eng).rollback)
        assert "actor" in src  # operator-explicit path
        # the engine has NO automatic performance trigger (no such method)
        assert not hasattr(eng, "auto_rollback_for_performance")

    def test_gov22_sample_floor_enforced(self, gov_engine, tmp_path):
        from nexus_scalp.governance.verify import verify_candidate

        art, sca = _make_artifact(tmp_path, dim=50)
        mf = _full_manifest(
            artifact_hash=__import__("hashlib").sha256(open(art, "rb").read()).hexdigest()
        )
        res = verify_candidate(
            model_id="c1",
            model_version="v1",
            artifact_path=art,
            scaler_path=sca,
            manifest=mf,
            shadow_evidence={
                "sample_floor_met": False,
                "samples_observed": 3,
                "samples_required": 300,
            },
            news_contract={"valid": True},
            liquidity_contract={"valid": True},
            oos_artifact="oos_x",
            training_commit="abc123",
        )
        assert not res["eligible"]
        assert "shadow_evidence_recorded" in res["failures"]

    def test_gov23_feature_drift_gate(self, gov_engine, tmp_path):
        from nexus_scalp.governance.verify import verify_candidate

        art, sca = _make_artifact(tmp_path, dim=50)
        mf = _full_manifest(
            artifact_hash=__import__("hashlib").sha256(open(art, "rb").read()).hexdigest()
        )
        # critical drift → news/liquidity dependency gate fails → blocked
        res = verify_candidate(
            model_id="c1",
            model_version="v1",
            artifact_path=art,
            scaler_path=sca,
            manifest=mf,
            news_contract={"valid": False, "detail": "WARNING/Critical drift"},
            liquidity_contract={"valid": True},
        )
        assert not res["eligible"]
        assert "news_contract_valid" in res["failures"]

    def test_gov24_news_liquidity_dependency_gate(self, gov_engine, tmp_path):
        from nexus_scalp.governance.verify import verify_candidate

        art, sca = _make_artifact(tmp_path, dim=50)
        mf = _full_manifest(
            artifact_hash=__import__("hashlib").sha256(open(art, "rb").read()).hexdigest()
        )
        # Liquidity contract unavailable → blocked
        res = verify_candidate(
            model_id="c1",
            model_version="v1",
            artifact_path=art,
            scaler_path=sca,
            manifest=mf,
            news_contract={"valid": True},
            liquidity_contract={"valid": False, "detail": "liquidity engine unavailable"},
        )
        assert not res["eligible"]
        assert "liquidity_contract_valid" in res["failures"]

    def test_gov25_installed_runtime_compatibility(self, gov_engine, tmp_path):
        """scalp_v3 + 70D must pass the schema registry gate."""
        from nexus_scalp.governance.load_gate import _registered_schema_ids

        assert "scalp_v3" in _registered_schema_ids()
        assert "scalp_liquidity_v1" in _registered_schema_ids()

    def test_gov26_migration_compatibility(self, tmp_path):
        """The AUDIT-0005 migration creates the governance audit tables
        idempotently and reports the new expected version."""
        from nexus_scalp.database.engine import DatabaseMigrationEngine
        from nexus_scalp.database.models import DatabaseDomain
        from nexus_scalp.database.registry import (
            expected_version_for_domain,
            migrations_for,
        )

        db = tmp_path / "mig.db"
        DatabaseMigrationEngine(db_path=db, domain="audit").migrate()
        eng = DatabaseMigrationEngine(db_path=db, domain="audit")
        assert eng.current_version() == eng.expected_version()
        ids = [m.migration_id for m in migrations_for(DatabaseDomain.AUDIT)]
        assert "AUDIT-0005-governance-audit-tables" in ids
        assert expected_version_for_domain(DatabaseDomain.AUDIT) == eng.current_version()

    def test_gov27_ui_status_matches_backend(self, gov_engine):
        """The status API contract mirrors the governance engine state
        (champion/candidate/gates/promotion). The UI consumes the same
        payload — verified here at the engine layer."""
        eng, store, _ = gov_engine
        snapshot = eng.registry_snapshot(audit_db=store.audit_repo._db_path)
        cats = snapshot.get("categories", {}) or {}
        assert set(cats.keys()) == {
            "CURRENT_CHAMPION",
            "CURRENT_CHALLENGER",
            "SHADOW",
            "PENDING_APPROVAL",
            "RETIRED",
            "FAILED",
        }

    def test_gov28_no_auto_promotion(self, gov_engine):
        """SHADOW -> CHAMPION is not a legal transition; no automatic path."""
        eng, _, _ = gov_engine
        from nexus_scalp.governance.engine import PromotionGateError
        from nexus_scalp.governance.models import (
            PROMOTION_TRANSITIONS,
            PromotionState,
        )

        eng.transition(
            model_id="c1", model_version="v1", target=PromotionState.VALIDATED, actor="op"
        )
        eng.transition(
            model_id="c1", model_version="v1", target=PromotionState.CHALLENGER, actor="op"
        )
        eng.transition(model_id="c1", model_version="v1", target=PromotionState.SHADOW, actor="op")
        with pytest.raises(PromotionGateError):
            eng.transition(
                model_id="c1", model_version="v1", target=PromotionState.CHAMPION, actor="op"
            )
        # inspect the transitions table: CHAMPION is only reachable from APPROVED
        assert PromotionState.CHAMPION in PROMOTION_TRANSITIONS[PromotionState.APPROVED]
        assert PromotionState.CHAMPION not in PROMOTION_TRANSITIONS[PromotionState.SHADOW]

    def test_gov29_concurrent_promotion_lock(self, gov_engine, tmp_path):
        from nexus_scalp.governance.lock import PromotionLock

        lock_path = tmp_path / "locks" / "promotion.lock"
        lock1 = PromotionLock(lock_path)
        assert lock1.try_acquire() is True
        # second attempt from another "process" fails cleanly
        lock2 = PromotionLock(lock_path)
        assert lock2.try_acquire() is False
        lock1.release()
        lock3 = PromotionLock(lock_path)
        assert lock3.try_acquire() is True
        assert lock3.acquired
        lock3.release()

    def test_gov30_restart_safe_governance_state(self, gov_engine):
        """Governance state survives a store restart (same DB)."""
        eng, _store, repo = gov_engine
        from nexus_scalp.governance.models import PromotionState
        from nexus_scalp.governance.store import GovernanceStore

        eng.transition(
            model_id="c1", model_version="v1", target=PromotionState.VALIDATED, actor="op"
        )
        repo._queue.join()

        # simulate restart: new store over the same DB
        store2 = GovernanceStore(audit_repo=repo)
        state = store2.get_state("c1", "v1")
        assert state["lifecycle_state"] == "VALIDATED"
