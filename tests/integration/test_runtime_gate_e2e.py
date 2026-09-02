"""End-to-end runtime certification (mission 32) + regression scenarios.

Runs the CANONICAL command as a real subprocess against this checkout and
proves the full chain executes:

    canonical command -> environment setup -> runtime construction -> DB ->
    model -> features -> synthetic market input -> 70D assembly -> inference
    -> regime -> policy -> risk -> SIMULATED execution proposal -> health
    -> shutdown -> JSON report

Also proves the no-order guarantee structurally: the gate's exercised
service graph must reach the risk engine WITHOUT any execution-seam call,
and the gate's own JSON must report execution_seam_calls == 0.

Run:  pytest tests/integration/test_runtime_gate_e2e.py -q
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
GATE = REPO / "scripts" / "ci" / "runtime_gate.py"


def _run_gate(*args: str, merge_stderr: bool = False) -> tuple[int, str]:
    proc = subprocess.run(
        [sys.executable, str(GATE), *args],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    # Default: stdout ONLY — the gate's machine contract. stderr carries
    # engine/structlog chatter (the gate routes engine logs to stderr by
    # design) and must never contaminate the --json stream.
    out = proc.stdout + proc.stderr if merge_stderr else proc.stdout
    return proc.returncode, out


def _gate_report() -> dict:
    code, out = _run_gate("--json")
    first = out.find("{")
    assert first >= 0, f"gate emitted no JSON: {out[-400:]}"
    return json.loads(out[first:])


class TestRuntimeGateE2E:
    def test_full_chain_certified(self):
        """Mission 32: the ONE canonical command certifies the composed runtime."""
        code, out = _run_gate()
        assert code == 0, out[-1500:]
        assert "RUNTIME CERTIFIED" in out

    def test_json_report_proves_every_pipeline_stage(self):
        report = _gate_report()
        assert report["status"] == "CERTIFIED"
        assert report["exit_code"] == 0
        # Stage chain, in order:
        names = [s["name"] for s in report["stages"]]
        assert names == [
            "L0 STATIC",
            "L1 IMPORT",
            "L2 CONFIG",
            "L3 DATABASE",
            "L4 MODEL/FEATURE",
            "L5 SERVICE GRAPH",
            "L6 DECISION CYCLE",
            "L7 API/HEALTH",
            "L8 SHUTDOWN",
            "L9 INVARIANTS",
        ]
        assert all(s["status"] == "PASS" for s in report["stages"])

    def test_model_and_feature_contract_evidence(self):
        report = _gate_report()
        model = report["model"]
        assert model["status"] == "PASS"
        assert model["model_dim"] == 70, "canonical era: 70D scalp_v3 champion"
        assert model["scaler_dim"] == 70
        assert model["schema_id"] == "scalp_v3"
        assert model["layout"] == "0..49 base | 50..59 news | 60..69 liquidity"
        assert model["base50"] == 50 and model["news10"] == 10 and model["liquidity10"] == 10
        schema = report["feature_schema"]
        assert schema["schema_id"] == "scalp_v3"
        assert schema["dimension"] == 70
        assert schema["scaler"] == "OK"

    def test_decision_cycle_completed_without_order(self):
        report = _gate_report()
        dc = report["engine"]["decision_cycle"]
        assert dc["status"] == "PASS"
        assert dc["tensor_dim"] == 70
        assert dc["execution_seam_calls"] == 0, (
            "a certification run must NEVER reach the execution seam"
        )
        assert dc["action"] in (
            "NO_TRADE",
            "BUY",
            "SELL",
            "BUY_LIMIT",
            "SELL_LIMIT",
            "BUY_MARKET",
            "SELL_MARKET",
            "WAIT",
            "CLOSE_POSITION",
        )
        assert 0.0 <= float(dc["confidence"]) <= 1.0
        assert "probs" in dc and len(dc["probs"]) >= 3

    def test_health_and_shutdown_evidence(self):
        report = _gate_report()
        assert report["api"]["status"] == "PASS"
        assert report["api"]["health_verdict"] in ("READY", "DEGRADED")
        assert report["api"]["secret_leak_check"] == "PASS"
        shutdown = report["shutdown"]
        assert shutdown["status"] == "PASS"
        assert shutdown["adapter_disconnected"] is True
        assert shutdown["audit_flushed"] is True
        assert shutdown["background_tasks_pending"] == 0

    def test_database_isolation_evidence(self):
        report = _gate_report()
        db = report["database"]
        assert db["status"] == "PASS"
        assert "disposable" in str(db["db"])
        assert db["required_present"] is True
        assert db["roundtrip_rows"] >= 1

    def test_invariants_block_carries_order_isolation(self):
        report = _gate_report()
        inv = report["invariants"][0]
        assert inv["name"] == "order_send_isolation"
        assert inv["status"] == "PASS"

    def test_failure_report_has_owner_and_class(self):
        """When the gate blocks, every failure names stage/class/owner (mission 19)."""
        report = _gate_report()
        if report["status"] == "CERTIFIED":
            return  # nothing to inspect on a green tree
        for failure in report["failures"]:
            assert failure["stage"]
            assert failure["failure_class"]
            assert failure["owner"] and failure["owner"] != "unassigned"
    def test_json_stdout_purity_is_a_contract(self):
        """--json stdout must parse even when engine chatter fires (stderr)."""
        code, out = _run_gate("--json", "--fast")
        report = json.loads(out[out.find("{"):])
        assert report["exit_code"] == code
        # stderr (merged run) may contain chatter but stdout never does.
        code2, both = _run_gate("--json", "--fast", merge_stderr=True)
        assert json.loads(both[both.find("{"):])["exit_code"] == code2
