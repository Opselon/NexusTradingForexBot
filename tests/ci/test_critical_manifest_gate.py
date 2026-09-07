"""CRITICAL MANIFEST ENFORCEMENT TESTS (QA hardening mission, P0).

Self-tests for scripts/ci/critical_manifest.py — the CI gate that maps
critical SOURCE modules to critical-suite guardians and enforces
file-level coverage floors. The tests lock the gate's own contract:

  * a floor module with zero guardians -> report not ok (drift visible)
  * a floor module with a guardian -> OK
  * facade linkage: tests importing release.updater guard update_engine.*
  * coverage gate: below-threshold file FAILS; low-coverage unrelated
    files can NOT mask a critical-file failure
  * the REAL repo manifest currently holds (no active drift)
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "ci"))

import critical_manifest as cm


def test_real_manifest_holds_now() -> None:
    report = cm.build_report()
    assert report["ok"], f"active drift: {report['missing_explicit']}"
    assert report["critical_test_files"] >= 70


def test_every_floor_module_claimed() -> None:
    for dotted in cm.REQUIRED_CRITICAL_SOURCES:
        assert dotted.startswith("nexus_scalp."), dotted


def test_floor_module_without_guardian_is_drift(tmp_path: Path) -> None:
    # Simulate: strip every guardian by pointing the builder at an empty
    # critical suite.
    empty = tmp_path / "critical_suite.txt"
    empty.write_text("# nothing\n", encoding="utf-8")
    original = cm.MANIFEST
    cm.MANIFEST = empty
    try:
        report = cm.build_report()
    finally:
        cm.MANIFEST = original
    assert not report["ok"]
    assert len(report["missing_explicit"]) == len(cm.REQUIRED_CRITICAL_SOURCES)


def test_facade_linkage_guards_update_engine() -> None:
    report = cm.build_report()
    rows = {r["module"]: r for r in report["explicit_floor"]}
    for mod in (
        "nexus_scalp.release.update_engine.orchestrator",
        "nexus_scalp.release.update_engine.discovery",
        "nexus_scalp.release.update_engine.rollback_state",
    ):
        assert rows[mod]["status"] == "OK", f"{mod} unguarded"


def test_coverage_gate_fails_below_threshold(tmp_path: Path) -> None:
    xml = tmp_path / "cov.xml"
    # one critical module below threshold, one unrelated file at 0%
    xml.write_text(
        """<?xml version="1.0" ?>
<coverage>
  <packages>
    <package>
      <classes>
        <class filename="nexus_scalp/risk/risk_engine.py"
               line-rate="0.10" branch-rate="0"/>
        <class filename="nexus_scalp/experience/models.py"
               line-rate="0" branch-rate="0"/>
      </classes>
    </package>
  </packages>
</coverage>
""",
        encoding="utf-8",
    )
    gate = cm.coverage_gate(xml)
    assert not gate["ok"]
    failures = " | ".join(gate["failures"])
    assert "risk_engine" in failures
    # the 0% UNRELATED file must NOT be what the gate reports:
    assert all("experience" not in f for f in gate["failures"]), (
        "gate must evaluate critical files, not inflate/mask via dead modules"
    )


def test_coverage_gate_passes_measured_baseline() -> None:
    # The real measured baseline (2026-09-07 critical-suite xdist run):
    # live_engine 26.9, order_manager 64.2, risk_engine 64.9, policy 73.8.
    xml = Path(__file__).resolve().parents[2] / "cov_critical_baseline.xml"
    if not xml.exists():
        import pytest

        pytest.skip("baseline XML not present on this machine")
    gate = cm.coverage_gate(xml)
    assert gate["ok"], gate["failures"]
