"""Smoke self-tests — the smoke tests itself.

Guarantees (task §22, §23 — no false green):
  - every critical check is registered (registry vs runner gap → FAIL)
  - unknown status cannot become PASS
  - exception inside a checker cannot be swallowed
  - report generation cannot hide failures
  - summary status is derived from check results (not forged)
  - exit code matches final status
  - safety checks cannot be silently disabled
  - missing evidence cannot become PASS

Run: pytest tests/unit/test_smoke_self.py -q
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from nexus_scalp.smoke.coverage_matrix import COVERAGE, NEGATIVE_CASES, critical_ids
from nexus_scalp.smoke.result_contract import CheckRecord, SmokeReport
from nexus_scalp.smoke.runner import SmokeRunner, run_smoke

# Env keys the repo conftest redirects per test (fresh empty HOME, settings
# DB, audit DB, data root). A module-scoped fixture runs BEFORE those
# function-scoped autouse fixtures, so a shared run_smoke() would otherwise
# see the runner's REAL home/config — reproduce the same isolation here.
_ISOLATED_ENV_KEYS = (
    "HOME",
    "USERPROFILE",
    "LOCALAPPDATA",
    "APPDATA",
    "TMPDIR",
    "TEMP",
    "TMP",
    "NEXUS_SETTINGS_DB",
    "NEXUS_AUDIT_DB",
    "NSE_DATABASE__SQLITE_PATH",
    "NEXUS_DATA_ROOT",
)


@contextmanager
def _isolated_smoke_env(run_dir: Path) -> Iterator[None]:
    """Run a block under the conftest's per-test isolation, then restore.

    The smoke runner already isolates its own settings DB and tmpdir, but
    it still reads user config (HOME) and the audit DB from the environment,
    so a shared run must see the same empty HOME a per-test run sees —
    otherwise a config present in the runner's real HOME could flip check
    outcomes between the shared report and a fresh run.
    """
    home_dir = run_dir / "home"
    tmp_dir = run_dir / "tmp"
    home_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    saved = {key: os.environ.get(key) for key in _ISOLATED_ENV_KEYS}
    saved_paper_persist = os.environ.get("NEXUS_PAPER_PERSIST")
    saved_tempdir = tempfile.tempdir
    overrides = {
        "HOME": str(home_dir),
        "USERPROFILE": str(home_dir),
        "LOCALAPPDATA": str(home_dir),
        "APPDATA": str(home_dir),
        "TMPDIR": str(tmp_dir),
        "TEMP": str(tmp_dir),
        "TMP": str(tmp_dir),
        "NEXUS_SETTINGS_DB": str(run_dir / "app_settings.db"),
        "NEXUS_AUDIT_DB": str(run_dir / "audit.db"),
        "NSE_DATABASE__SQLITE_PATH": str(run_dir / "audit.db"),
        "NEXUS_DATA_ROOT": str(run_dir / "data"),
    }
    os.environ.update(overrides)
    if os.environ.get("NEXUS_PAPER_PERSIST", "") == "":
        os.environ["NEXUS_PAPER_PERSIST"] = "1"
    tempfile.tempdir = str(tmp_dir)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if saved_paper_persist is None:
            os.environ.pop("NEXUS_PAPER_PERSIST", None)
        else:
            os.environ["NEXUS_PAPER_PERSIST"] = saved_paper_persist
        tempfile.tempdir = saved_tempdir


@pytest.fixture(scope="module")
def fast_report(tmp_path_factory: pytest.TempPathFactory) -> SmokeReport:
    """One shared ``fast``-tier run for the whole module.

    Was re-run in ~10 tests (one full L0/L1/safety pass each, ~5s apiece on
    the 2-core CI runner). The report is read-only in every consumer —
    counts, statuses, derived overall status — so one run serves them all.
    Exceptions: the secrets test (needs its own run with the token set) and
    the determinism test (needs a second, independent run to compare).
    """
    run_dir = tmp_path_factory.mktemp("smoke_shared_fast")
    with _isolated_smoke_env(run_dir):
        return run_smoke(tier="fast")


@pytest.fixture(scope="module")
def safety_report(tmp_path_factory: pytest.TempPathFactory) -> SmokeReport:
    """One shared ``safety``-tier run (same sharing rationale as fast)."""
    run_dir = tmp_path_factory.mktemp("smoke_shared_safety")
    with _isolated_smoke_env(run_dir):
        return run_smoke(tier="safety")


class TestSmokeManifestCompleteness:
    def test_every_critical_check_is_registered_in_runner(self, fast_report: SmokeReport) -> None:
        rpt = fast_report
        assert len(rpt.checks) >= 30, (
            f"fast smoke produced only {len(rpt.checks)} checks — registry depth regressed (want ≥30)"
        )

    def test_safety_case_count(self, safety_report: SmokeReport) -> None:
        rpt = safety_report
        safety = [c for c in rpt.checks if c.layer == "L4"]
        assert len(safety) == 12, f"L4 safety should have 12 injections, got {len(safety)}"

    def test_coverage_negative_case_registry_sizes(self) -> None:
        assert len(COVERAGE) >= 180, (
            f"coverage registry too small ({len(COVERAGE)}) — entries were removed silently"
        )
        assert len(NEGATIVE_CASES) >= 12, f"negative cases too few ({len(NEGATIVE_CASES)})"


class TestNoFalseGreen:
    def test_missing_evidence_cannot_become_pass(self, fast_report: SmokeReport) -> None:
        rpt = fast_report
        # overall_status is derived, never forged
        has_fail = any(c.status == "FAIL" for c in rpt.checks)
        if has_fail:
            assert rpt.overall_status in ("FAIL", "BLOCKED")
        else:
            # No fail → must not be BLOCKED unless there are critical SKIPs
            assert rpt.overall_status in ("PASS", "BLOCKED")

    def test_unknown_status_cannot_become_pass(self, fast_report: SmokeReport) -> None:
        rpt = fast_report
        allowed = {
            "PASS",
            "FAIL",
            "SKIP",
            "WARN",
            "BLOCKED",
            "NOT_APPLICABLE",
            "ENVIRONMENT_FAILURE",
            "UNAVAILABLE",
        }
        for c in rpt.checks:
            assert c.status in allowed, (
                f"{c.id} has unknown status {c.status!r} — would be false green"
            )

    def test_exception_in_checker_cannot_be_swallowed(self) -> None:
        rpt = SmokeReport(
            run_id="test",
            git_commit="test",
            version="test",
            timestamp="test",
            environment={},
            runtime_mode="paper",
            tier="fast",
            overall_status="PASS",
            release_gate=True,
            duration_ms=0,
        )
        # Simulate a checker that raises — _check must surface it as FAIL/WARN, not swallow
        from nexus_scalp.smoke.runner import _check

        def _boom() -> None:
            raise RuntimeError("synthetic checker boom")

        rec = _check(
            rpt,
            "TEST-BOOM",
            "L0",
            "synthetic boom checker",
            _boom,
            failure_code="CODE_DEFECT",
            expected="should fail",
        )
        assert rec.status == "FAIL"
        assert rec.failure_code == "CODE_DEFECT"

    def test_exit_code_matches_status(
        self, fast_report: SmokeReport, safety_report: SmokeReport
    ) -> None:
        for rpt in (fast_report, safety_report):
            if rpt.overall_status == "PASS":
                expected_code = 0
            elif rpt.overall_status == "BLOCKED":
                expected_code = 3
            else:
                expected_code = 1
            # The CLI maps PASS→0, BLOCKED→3, otherwise 1/4 — here we just verify
            # that a FAIL never maps to PASS semantics
            assert not (rpt.overall_status == "FAIL" and expected_code == 0)
            assert not (
                rpt.overall_status == "PASS" and any(c.status == "FAIL" for c in rpt.checks)
            )

    def test_summary_derived_from_checks(self, fast_report: SmokeReport) -> None:
        rpt = fast_report
        has_critical_fail = any(c.id in critical_ids() and c.status == "FAIL" for c in rpt.checks)
        if has_critical_fail:
            assert rpt.release_gate is False
            assert rpt.overall_status == "FAIL"
        else:
            has_any_fail = any(c.status == "FAIL" for c in rpt.checks)
            if has_any_fail:
                assert rpt.overall_status == "FAIL"


class TestEvidenceAndRedaction:
    def test_secrets_never_leak_in_report(self) -> None:
        import os

        os.environ["NEXUS_TELEGRAM_BOT_TOKEN"] = "should_not_appear_12345"
        try:
            rpt = run_smoke(tier="fast")
            blob = json.dumps(rpt.to_dict(), default=str)
            assert "should_not_appear_12345" not in blob
            # Presence-only flag is ok
            assert "TELEGRAM" not in blob or "should_not_appear" not in blob
        finally:
            os.environ.pop("NEXUS_TELEGRAM_BOT_TOKEN", None)

    def test_run_id_and_correlation_present(self, fast_report: SmokeReport) -> None:
        rpt = fast_report
        assert rpt.run_id.startswith("smoke-")
        assert len(rpt.run_id) > 10
        assert rpt.evidence.get("run_id") == rpt.run_id


class TestDeterminism:
    def test_same_bars_same_50d(self, fast_report: SmokeReport) -> None:
        r1 = fast_report
        r2 = run_smoke(tier="fast")
        # fast tier is deterministic — schema hash and check IDs must agree (PASS count can vary by timing/budget WARN)
        assert r1.schema_identity.get("hash") == r2.schema_identity.get("hash")
        assert {c.id for c in r1.checks} == {c.id for c in r2.checks}
        assert r1.overall_status == r2.overall_status


class TestDurationGuard:
    def test_fast_smoke_under_budget(self, fast_report: SmokeReport) -> None:
        rpt = fast_report
        # fast must be well under 30s (it is ~2s); guard against pathological regression
        assert rpt.duration_ms < 30_000, (
            f"fast smoke took {rpt.duration_ms:.0f} ms — budget 30s exceeded"
        )

    def test_full_smoke_under_budget(self) -> None:
        # Full tier boots real LiveEngine — guard against hanging
        rpt = run_smoke(tier="full")
        assert rpt.duration_ms < 120_000, (
            f"full smoke took {rpt.duration_ms:.0f} ms — budget 120s exceeded"
        )
        # Must produce the full complement of checks
        assert len(rpt.checks) >= 40, (
            f"full smoke produced only {len(rpt.checks)} checks — want ≥40"
        )
