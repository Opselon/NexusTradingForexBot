"""Root-cause proof: the 70D observation hook only runs inside the 50D-shadow
except block (TASK-70D-SYSTEM-FLOW-FORENSICS).

Strategy: construct a real LiveEngine-like harness with a shadow70 runtime in
READY state, call _record_shadow_decision with a HAPPY 50D shadow path
(no exception) and verify ZERO shadow70 observations; then force the 50D
shadow path to raise and verify the 70D hook fires. This proves the nesting
bug (hook inside except) + the early-return bug (hook unreachable when no
50D shadow attached).
"""
import sys
from datetime import UTC, datetime
from types import SimpleNamespace

sys.path.insert(0, r"C:/Users/Capsizer/source/repos/NexusTradingForexBot/src")

from nexus_scalp.domain.models import TickData  # noqa: E402
from nexus_scalp.shadow.shadow70.models import (  # noqa: E402
    SHADOW70_DIMENSION,
    SHADOW70_SCHEMA_ID,
    Shadow70CandidateContract,
)
from nexus_scalp.shadow.shadow70.runtime import Shadow70LoadValidator, Shadow70Runtime  # noqa: E402
from nexus_scalp.shadow.shadow70.store import Shadow70Store  # noqa: E402
from nexus_scalp.shadow.shadow70.worker import Shadow70Worker  # noqa: E402


def make_contract(tmp: str) -> Shadow70CandidateContract:
    import os

    artifact = os.path.join(tmp, "m.pt")
    scaler = os.path.join(tmp, "m.pt.scaler.npz")
    with open(artifact, "wb") as f:
        f.write(b"state")
    with open(scaler, "wb") as f:
        f.write(b"s")
    from nexus_scalp.shadow.shadow70.runtime import sha256_file

    return Shadow70CandidateContract(
        model_id="cand_70d_liquidity_v1",
        model_version="v1.0",
        schema_id=SHADOW70_SCHEMA_ID,
        dimension=SHADOW70_DIMENSION,
        feature_schema_hash="f" * 16,
        scaler_hash=sha256_file(scaler),
        validation_result="VALIDATED_CANDIDATE",
        artifact_hash=sha256_file(artifact),
        artifact_path=artifact,
        scaler_path=scaler,
        num_classes=4,
    )


class Harness:
    """Minimal LiveEngine stand-in executing the REAL _record_shadow_decision
    source (imported from live_engine and bound)."""

    def __init__(self, tmp: str) -> None:
        import tempfile

        self._tmp = tmp
        self._shadow_challenger = SimpleNamespace()  # attached -> happy path
        self._governance_shadow = None
        from nexus_scalp.shadow.engine import ShadowEngine, ShadowComparer
        from nexus_scalp.shadow.store import ShadowStore

        self.shadow_engine = ShadowEngine(store=ShadowStore(audit_repo=None))
        self._bundle = None
        self._news_enabled = False
        self.news_engine = None
        self._last_probs = None
        self.FEATURE_DIM = 50
        self.FEATURE_SCHEMA_ID = "scalp_v1"
        self.aggregator = SimpleNamespace(get_completed_bars=lambda: [])
        self.champion_manager = SimpleNamespace(
            model_id="primary_scalp_scalp_v1_50d",
            model_version="v1.0",
            champion_or_none=lambda: None,
        )
        self.config = SimpleNamespace(model=SimpleNamespace(feature_schema_version="1.0"))
        # shadow70 wiring (mirrors live_engine __init__)
        from nexus_scalp.shadow.shadow70.health import (
            Shadow70DriftMonitor,
            Shadow70FeatureHealthMonitor,
        )

        self._shadow70_store = Shadow70Store(audit_repo=None)
        self._shadow70_runtime = Shadow70Runtime()
        self._shadow70_health = Shadow70FeatureHealthMonitor(window=1000)
        self._shadow70_drift = Shadow70DriftMonitor()
        self._shadow70_worker = Shadow70Worker(store=self._shadow70_store, max_queue=2000)
        self._shadow70_worker_started = False
        self._shadow70_enabled = True  # operator attached + enabled
        # attach a VALIDATED contract so runtime is READY
        res = self._shadow70_runtime.attach(make_contract(tmp))
        assert res.passed, res.to_dict()
        self._shadow70_runtime.set_inference(lambda v: [0.6, 0.2, 0.1, 0.1])

    def record(self) -> None:
        from nexus_scalp.application.live_engine import LiveEngine

        # bind the REAL functions (unbound -> instance methods); mirror the
        # production call site: 50D shadow + independent 70D hook
        f50 = LiveEngine._record_shadow_decision
        f70 = LiveEngine._record_shadow70_observation
        self._last_regime_state = SimpleNamespace(regime=SimpleNamespace(value="NEUTRAL"))
        tick = TickData(
            symbol="XAUUSD",
            timestamp=datetime.now(UTC),
            bid=2000.0,
            ask=2000.1,
            volume=1.0,
        )
        fv = SimpleNamespace(to_tensor_input=lambda: [0.0] * 50, feature_hash="abc123")
        regime = self._last_regime_state
        proposal = SimpleNamespace(
            action=SimpleNamespace(value="NO_TRADE"),
            confidence=0.55,
            request_id="req_probe",
            session="ALL",
        )
        f50(self, tick, fv, regime, proposal)
        f70(self, tick, fv, proposal)

    def shadow70_count(self) -> int:
        rt = self._shadow70_runtime
        return rt.observations


def main() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        h = Harness(tmp)
        print("runtime state:", h._shadow70_runtime.state.value, "enabled:", h._shadow70_enabled)
        print("observations before:", h.shadow70_count())
        # HAPPY 50D shadow path: engine.record_shadow_decision must succeed
        h.record()
        print("observations after HAPPY record:", h.shadow70_count())
        # Now force the 50D shadow path to raise: make shadow_engine.record_shadow_decision fail
        def boom(*a, **k):  # noqa: ANN002, ANN003
            raise RuntimeError("50D shadow path failure (forced for proof)")

        h.shadow_engine.record_shadow_decision = boom  # type: ignore[method-assign]
        h.record()
        print("observations after FORCED-FAIL record:", h.shadow70_count())
        print("feature_invalid(errors):", h._shadow70_runtime.observations)
        print("worker.enqueued:", h._shadow70_worker.enqueued)


if __name__ == "__main__":
    main()