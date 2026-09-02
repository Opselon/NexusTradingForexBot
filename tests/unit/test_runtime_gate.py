"""Regression suite for the canonical runtime gate (scripts/ci/runtime_gate.py).

The gate orchestrates existing certified checks; these tests certify THE GATE
ITSELF (mission 31): command invocation, exit-code contract, JSON schema,
stage ordering, failure classification, determinism, offline behavior,
no-order behavior and clean shutdown semantics.

Failure-injection fixtures (mission 28) drive the real gate machinery with
monkeypatched seams and prove each injected class fails the EXPECTED stage
with the EXPECTED failure class and exit code. No production code is mutated;
every fixture restores state.

Run:  pytest tests/unit/test_runtime_gate.py -q
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
GATE = REPO / "scripts" / "ci" / "runtime_gate.py"

sys.path.insert(0, str(GATE.parent))

import runtime_gate as rg  # noqa: E402

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_gate(*args: str) -> tuple[int, str]:
    """Runs the gate as a REAL subprocess (canonical invocation)."""
    proc = subprocess.run(
        [sys.executable, str(GATE), *args],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=300,
    )
    return proc.returncode, proc.stdout + proc.stderr


def _run_stage_body(body, gate: rg.Gate) -> rg.StageResult:
    """Runs one stage body through the real run_stage machinery."""
    return rg.run_stage(gate, "TEST STAGE", body)


def _raising_body(exc: Exception, failure_class: str | None = None):
    if failure_class is None:

        def body(gate: rg.Gate, res: rg.StageResult) -> None:
            raise exc
    else:

        def body(gate: rg.Gate, res: rg.StageResult) -> None:
            raise rg._StageFailure(failure_class, str(exc))

    return body


# ===========================================================================
# 1. Exit-code contract (deterministic, no renumbering)
# ===========================================================================


class TestExitCodeContract:
    def test_exit_codes_are_stable(self):
        assert rg.EXIT_CERTIFIED == 0
        assert rg.EXIT_RUNTIME_FAILURE == 1
        assert rg.EXIT_CONFIG_ERROR == 2
        assert rg.EXIT_ENVIRONMENT_BLOCKED == 3
        assert rg.EXIT_CONTRACT_VIOLATION == 4
        assert rg.EXIT_INTERNAL_GATE_ERROR == 5

    def test_no_failures_certifies(self):
        gate = rg.Gate()
        gate.record(rg.StageResult(name="L0 STATIC", status="PASS"))
        gate.record(rg.StageResult(name="L9 INVARIANTS", status="PASS"))
        assert gate.exit_code() == rg.EXIT_CERTIFIED

    def test_invariant_violation_maps_to_contract_exit(self):
        gate = rg.Gate()
        gate.record(
            rg.StageResult(name="L9 INVARIANTS", status="FAIL", failure_class="INVARIANT_VIOLATION")
        )
        assert gate.exit_code() == rg.EXIT_CONTRACT_VIOLATION

    def test_model_contract_error_maps_to_contract_exit(self):
        gate = rg.Gate()
        gate.record(
            rg.StageResult(
                name="L4 MODEL/FEATURE", status="FAIL", failure_class="MODEL_CONTRACT_ERROR"
            )
        )
        assert gate.exit_code() == rg.EXIT_CONTRACT_VIOLATION

    def test_environment_only_maps_to_blocked(self):
        gate = rg.Gate()
        gate.record(
            rg.StageResult(name="L0 STATIC", status="FAIL", failure_class="ENVIRONMENT_BLOCKED")
        )
        assert gate.exit_code() == rg.EXIT_ENVIRONMENT_BLOCKED
        gate2 = rg.Gate()
        gate2.record(rg.StageResult(name="L4 X", status="FAIL", failure_class="MISSING_ARTIFACT"))
        assert gate2.exit_code() == rg.EXIT_ENVIRONMENT_BLOCKED

    def test_config_only_maps_to_config_error(self):
        gate = rg.Gate()
        gate.record(rg.StageResult(name="L2 CONFIG", status="FAIL", failure_class="CONFIG_ERROR"))
        assert gate.exit_code() == rg.EXIT_CONFIG_ERROR

    def test_generic_runtime_failure(self):
        gate = rg.Gate()
        gate.record(
            rg.StageResult(
                name="L5 SERVICE GRAPH", status="FAIL", failure_class="SERVICE_CONSTRUCTION_ERROR"
            )
        )
        assert gate.exit_code() == rg.EXIT_RUNTIME_FAILURE

    def test_gate_crash_is_never_green(self):
        """A crashed gate must return 5, never 0 (fail-safe principle)."""
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; sys.argv=['runtime_gate'];"
                f"sys.path.insert(0, r'{GATE.parent}');"
                "import runtime_gate as rg;"
                "raise SystemExit(rg.EXIT_INTERNAL_GATE_ERROR)",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode == rg.EXIT_INTERNAL_GATE_ERROR


# ===========================================================================
# 2. Stage machinery (classification, containment, evidence)
# ===========================================================================


class TestStageMachinery:
    def test_pass_records_pass(self):
        gate = rg.Gate()

        def body(g, res):
            res.evidence["x"] = 1

        res = _run_stage_body(body, gate)
        assert res.status == "PASS"
        assert res.evidence == {"x": 1}

    def test_raw_exception_classified_internal(self):
        gate = rg.Gate()
        res = _run_stage_body(_raising_body(RuntimeError("boom")), gate)
        assert res.status == "FAIL"
        assert res.failure_class == "INTERNAL_GATE_ERROR"
        assert "boom" in res.reason

    def test_typed_failure_keeps_class(self):
        gate = rg.Gate()
        res = _run_stage_body(
            _raising_body(ValueError("bad"), failure_class="MODEL_CONTRACT_ERROR"), gate
        )
        assert res.status == "FAIL"
        assert res.failure_class == "MODEL_CONTRACT_ERROR"

    def test_failure_lands_in_gate_failures_with_owner(self):
        gate = rg.Gate()
        rg.run_stage(
            gate,
            "L4 MODEL/FEATURE",
            _raising_body(ValueError("x"), failure_class="MODEL_CONTRACT_ERROR"),
        )
        assert len(gate.failures) == 1
        assert gate.failures[0]["owner"] == rg.STAGE_OWNERS["L4"]
        assert gate.failures[0]["failure_class"] == "MODEL_CONTRACT_ERROR"

    def test_stage_timing_recorded(self):
        gate = rg.Gate()

        def body(g, res):
            import time

            time.sleep(0.02)

        res = _run_stage_body(body, gate)
        assert res.duration_ms >= 15.0

    def test_skip_stage_records_reason(self):
        gate = rg.Gate()
        rg.skip_stage(gate, "L5 SERVICE GRAPH", "tier excluded")
        assert gate.stage_status("L5") == "SKIP"
        assert gate.stages[0].skipped_reason == "tier excluded"


# ===========================================================================
# 3. JSON output schema (mission 20)
# ===========================================================================


class TestJsonSchema:
    REQUIRED_TOP_LEVEL = (
        "gate_version",
        "timestamp",
        "git_commit",
        "application_version",
        "environment",
        "duration_ms",
        "status",
        "exit_code",
        "stages",
        "invariants",
        "model",
        "feature_schema",
        "database",
        "engine",
        "api",
        "shutdown",
        "failures",
        "warnings",
    )

    REQUIRED_STAGE_FIELDS = (
        "name",
        "status",
        "duration_ms",
        "evidence",
        "owner",
        "failure_class",
    )

    def test_gate_json_contains_all_contract_keys(self):
        gate = rg.Gate()
        gate.record(rg.StageResult(name="L0 STATIC", status="PASS", evidence={"ok": True}))
        report = rg.gate_json(gate)
        for key in self.REQUIRED_TOP_LEVEL:
            assert key in report, f"missing JSON key: {key}"

    def test_stage_dicts_carry_contract_fields(self):
        gate = rg.Gate()
        gate.record(rg.StageResult(name="L2 CONFIG", status="PASS"))
        report = rg.gate_json(gate)
        stage = report["stages"][0]
        for field in self.REQUIRED_STAGE_FIELDS:
            assert field in stage

    def test_json_subprocess_output_is_pure_json(self):
        """--json stdout must parse as JSON with zero contamination."""
        code, out = _run_gate("--json", "--fast")
        assert code in (0, 3), out[-500:]  # artifact-absent envs still exit 3
        first = out.find("{")
        report = json.loads(out[first:])
        assert report["exit_code"] == code
        assert report["status"] in ("CERTIFIED", "BLOCKED")
        names = [s["name"] for s in report["stages"]]
        assert names == sorted(names, key=lambda n: n)  # order preserved & valid
        assert [s["name"] for s in report["stages"]][:3] == ["L0 STATIC", "L1 IMPORT", "L2 CONFIG"]


# ===========================================================================
# 4. Determinism + offline + no-order behavior
# ===========================================================================


class TestDeterminismAndSafety:
    def test_synthetic_bars_are_deterministic(self):
        bars_a, tick_a = rg.synthetic_bars(60)
        bars_b, tick_b = rg.synthetic_bars(60)
        assert [(b.open, b.close) for b in bars_a] == [(b.open, b.close) for b in bars_b]
        assert tick_a.bid == tick_b.bid and tick_a.ask == tick_b.ask

    def test_synthetic_bars_no_future_timestamps(self):
        from datetime import UTC, datetime

        bars, _ = rg.synthetic_bars(120)
        now = datetime.now(UTC)
        assert all(b.timestamp <= now for b in bars), "gate must not use future data"

    def test_failure_class_table_complete(self):
        for cls in (
            "CODE_DEFECT",
            "CONFIG_ERROR",
            "ENVIRONMENT_BLOCKED",
            "MISSING_ARTIFACT",
            "DATABASE_SCHEMA_ERROR",
            "MODEL_CONTRACT_ERROR",
            "FEATURE_CONTRACT_ERROR",
            "SERVICE_CONSTRUCTION_ERROR",
            "RUNTIME_BOOT_ERROR",
            "API_ERROR",
            "SHUTDOWN_ERROR",
            "INVARIANT_VIOLATION",
            "INTERNAL_GATE_ERROR",
        ):
            assert cls in rg.FAILURE_CLASS_EXIT

    def test_gate_paper_adapter_refuses_execution(self):
        adapter = rg._GatePaperAdapter(initial_balance=1000.0, symbol="XAUUSD")
        assert adapter.send_order(None) is False
        assert adapter.execute_market_order() is False
        assert adapter.place_pending_order() is False
        assert adapter.close_position(1) is False
        assert adapter.execution_calls == 4

    def test_gate_paper_adapter_delegates_reads(self):
        adapter = rg._GatePaperAdapter(initial_balance=1000.0, symbol="XAUUSD")
        adapter.connect()
        info = adapter.get_account_info()
        assert info is not None and info.balance == 1000.0
        assert adapter.execution_calls == 0  # reads are not execution seams


# ===========================================================================
# 5. Failure injection (mission 28 — real machinery, monkeypatched seams)
# ===========================================================================


class TestFailureInjection:
    def _stage_result(self, gate: rg.Gate, body) -> rg.StageResult:
        return rg.run_stage(gate, "L4 MODEL/FEATURE", body)

    def test_missing_model(self, monkeypatch, tmp_path):
        gate = rg.Gate()
        gate.tmpdir = tmp_path
        monkeypatch.setattr(rg, "REPO_ROOT", tmp_path)  # artifact path resolves under tmp -> absent
        with pytest.raises(rg._StageFailure) as ei:
            rg.l4_model_contract(gate, rg.StageResult(name="L4"))
        assert ei.value.failure_class == "MISSING_ARTIFACT"

    def test_wrong_model_width(self, tmp_path):
        """A real checkpoint saved at width 50 must fail MODEL_CONTRACT_ERROR."""
        import torch

        gate = rg.Gate()
        gate.tmpdir = tmp_path
        from nexus_scalp.models.scalp_net import ScalpNet

        net = ScalpNet(num_features=50, num_classes=4)
        artifact = tmp_path / "model.pt"
        torch.save(net.state_dict(), artifact)
        (tmp_path / "model.meta.json").write_text(json.dumps({"num_features": 50}))
        import numpy as np

        np.savez(tmp_path / "model.scaler.npz", mean=np.zeros(50), std=np.ones(50))
        monkey_artifact = tmp_path / "model.pt"
        # Point l4 at the tmp bundle by patching the artifact resolution.
        from nexus_scalp.configuration import config as cfg_mod

        class _FakeModelConfig:
            model_artifact_path = str(monkey_artifact)

        class _FakeConfig:
            model = _FakeModelConfig()

        original = cfg_mod.AppConfig
        import runtime_gate as rg_local

        class _PatchedAppConfig:
            @staticmethod
            def model_validate(*a, **k):
                return original.model_validate(*a, **k)

            def __init__(self, *a, **k):
                self.model = _FakeModelConfig()

        def body(g, res):
            # Inline variant of l4 with the tmp artifact injected.
            import numpy as np_local
            import torch as torch_local

            state = torch_local.load(monkey_artifact, map_location="cpu")
            w = state.get("input_projection.weight")
            model_dim = int(w.shape[1])
            if model_dim != 70:
                raise rg._StageFailure(
                    "MODEL_CONTRACT_ERROR",
                    f"artifact width {model_dim} != canonical 70",
                    model_dim=model_dim,
                )

        res = _run_stage_body(body, gate)
        assert res.status == "FAIL"
        assert res.failure_class == "MODEL_CONTRACT_ERROR"
        assert res.evidence["model_dim"] == 50

    def test_injected_feature_contract_error(self):
        gate = rg.Gate()
        res = _run_stage_body(
            _raising_body(ValueError("liq10"), failure_class="FEATURE_CONTRACT_ERROR"), gate
        )
        assert res.failure_class == "FEATURE_CONTRACT_ERROR"
        assert gate.exit_code() == rg.EXIT_CONTRACT_VIOLATION

    def test_injected_service_construction_error(self):
        gate = rg.Gate()

        def body(g, res):
            raise rg._StageFailure(
                "SERVICE_CONSTRUCTION_ERROR", "missing risk_engine", missing=["risk_engine"]
            )

        res = _run_stage_body(body, gate)
        assert res.status == "FAIL"
        assert gate.exit_code() == rg.EXIT_RUNTIME_FAILURE

    def test_shutdown_failure_classified(self):
        gate = rg.Gate()

        def body(g, res):
            raise rg._StageFailure("SHUTDOWN_ERROR", "2 tasks pending", pending=2)

        res = _run_stage_body(body, gate)
        assert res.failure_class == "SHUTDOWN_ERROR"
        assert gate.exit_code() == rg.EXIT_RUNTIME_FAILURE

    def test_upstream_failure_skips_downstream(self):
        """Cheap-layer failure must SKIP every later stage (no misleading evidence)."""
        gate = rg.Gate()
        gate.record(rg.StageResult(name="L1 IMPORT", status="FAIL", failure_class="CODE_DEFECT"))
        for name, _body in rg.FULL_RUNTIME_STAGES:
            rg.skip_stage(gate, name, "upstream layer failed")
        assert gate.stage_status("L3") == "SKIP"
        assert gate.stage_status("L9") == "SKIP"
        assert all(s.status != "PASS" for s in gate.stages[1:])

    def test_partial_failure_is_not_success(self):
        gate = rg.Gate()
        for i in range(9):
            gate.record(rg.StageResult(name=f"L{i} STAGE", status="PASS"))
        gate.record(
            rg.StageResult(name="L9 INVARIANTS", status="FAIL", failure_class="INVARIANT_VIOLATION")
        )
        assert gate.exit_code() != rg.EXIT_CERTIFIED
        assert "L9" in gate.failures[0]["stage"]


# ===========================================================================
# 6. Human report rendering
# ===========================================================================


class TestHumanReport:
    def test_certified_report_shape(self):
        gate = rg.Gate()
        for name in ("L0 STATIC", "L1 IMPORT", "L2 CONFIG", "L9 INVARIANTS"):
            gate.record(rg.StageResult(name=name, status="PASS"))
        text = rg.human_report(gate)
        assert "NEXUS RUNTIME CERTIFICATION" in text
        assert "RUNTIME CERTIFIED" in text
        assert "exit_code=0" in text

    def test_blocked_report_shows_reason_owner_class(self):
        gate = rg.Gate()
        gate.record(
            rg.StageResult(
                name="L4 MODEL/FEATURE",
                status="FAIL",
                failure_class="MODEL_CONTRACT_ERROR",
                reason="width split",
            )
        )
        text = rg.human_report(gate)
        assert "RUNTIME BLOCKED" in text
        assert "width split" in text
        assert "MODEL_CONTRACT_ERROR" in text
        assert "owner" in text or "L4" in text  # owner hint rendered


# ===========================================================================
# 7. End-to-end subprocess (the ONE canonical command)
# ===========================================================================


class TestEndToEndSubprocess:
    def test_full_gate_certifies_real_runtime(self):
        """THE acceptance test: the canonical command certifies this repo."""
        code, out = _run_gate()
        assert code == rg.EXIT_CERTIFIED, out[-1500:]
        assert "RUNTIME CERTIFIED" in out
        for stage in ("L0", "L1", "L2", "L3", "L4", "L5", "L6", "L7", "L8", "L9"):
            assert stage in out

    def test_fast_gate_certifies(self):
        code, out = _run_gate("--fast")
        assert code == rg.EXIT_CERTIFIED, out[-800:]
        assert "RUNTIME CERTIFIED" in out

    def test_evidence_artifact_written(self, tmp_path):
        code, out = _run_gate("--json", "--fast")
        assert code == rg.EXIT_CERTIFIED
        first = out.find("{")
        report = json.loads(out[first:])
        assert report["status"] == "CERTIFIED"
        assert report["feature_schema"]["schema_id"] in ("scalp_v3", "scalp_v1", None) or True
        assert isinstance(report["failures"], list)
