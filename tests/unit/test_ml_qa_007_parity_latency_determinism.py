"""ML-QA-007 — latency-contract determinism for the MT5 parity suite.

Pins the three rules the ML-QA-003 roster's candidate #4 remediation
(``tests/integration/test_mt5_adapter_parity.py``, 6 ``perf_counter`` probes,
cross-OS matrix exposure) established:

1. **CPU time, never wall clock.** A latency *measurement* assert must read
   ``time.process_time()`` (CPU time), never ``perf_counter``/``monotonic``,
   because pytest-xdist runs the critical suite with ``-n auto --dist
   loadgroup`` on 2-core co-tenant runners where scheduler preemption — not
   the code under test — dominates a wall-clock p99.
2. **Warmup before timing.** A latency sample that includes the first call
   measures one-time lazy init (module import, first HMAC, socket connect)
   rather than the steady-state cost the SLA names.
3. **Load-independent invariants stay hard.** Call counts, action identity
   and percentile *ordering* are deterministic and remain asserted exactly;
   only the absolute timing bound moved to CPU time.

WHY THE OLD SHAPE WAS RED
-------------------------
``t0 = time.perf_counter(); adapter.send_order(order); (perf_counter() - t0)``
measures wall clock. Under ``-n auto`` saturation every core is busy, so the
measured p99 was the *scheduler's* tail (observed shape across this repo:
``3.61x < 3.5`` scaling asserts, and the two order-serialization SLA reds
fixed in 9279f1ea by *dropping* the threshold). ``process_time`` excludes
preemption entirely: the time the CPU spent in another process is not ours.
The mean CPU bound (< 1.0 ms per order) is the real serialization contract,
and it holds with large headroom on every OS of the matrix.

CONSEQUENCE OF REMOVING THIS GUARD
----------------------------------
A contributor reintroducing ``perf_counter`` in this module restores a
cross-OS red that triages as a platform bug and costs a full CI-artifact
round trip (``gh run view --log-failed`` returns empty; the failure text
lives in the ``ci-results-*`` artifact's ``pytest/pytest.txt``).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PARITY_MODULE = _REPO_ROOT / "tests" / "integration" / "test_mt5_adapter_parity.py"

# The two serialization SLAs this remediation owns.
_SLA_TESTS = (
    "test_order_serialization_latency_under_1ms",
    "test_market_order_serialization_p99_under_1ms",
)


def _module_text() -> str:
    """The parity module as committed text (read-only, never executed)."""
    assert _PARITY_MODULE.exists(), f"parity module missing: {_PARITY_MODULE}"
    return _PARITY_MODULE.read_text(encoding="utf-8")


def _strip_prose(src: str) -> str:
    """Comments + docstrings removed so prose mentions never false-positive."""
    code = re.sub(r"#.*$", "", src, flags=re.M)
    code = re.sub(r'""".*?"""', "", code, flags=re.S)
    code = re.sub(r"'''.*?'''", "", code, flags=re.S)
    return code


class TestParityModuleUsesCpuTime:
    """Rule 1: every latency *measurement* in the module reads CPU time."""

    def test_module_exists_and_is_in_the_push_gate(self):
        src = _module_text()
        assert "RemoteMT5GatewayAdapter" in src, "parity module must cover the remote gateway"
        manifest = _REPO_ROOT / "tests" / "critical_suite.txt"
        assert "tests/integration/test_mt5_adapter_parity.py" in manifest.read_text(
            encoding="utf-8"
        ), "the OS-matrix lane runs this file from the push-gate manifest"

    def test_no_wall_clock_measurement_anchors(self):
        """No bare ``t0 = time.perf_counter()`` / ``monotonic()`` anchor.

        The only real clock the module may touch for measurement is
        ``process_time``; ``time.sleep`` in the gateway harness is a
        *stimulus*, not a measurement, and is excluded by the anchor regex.
        """
        src = _module_text()
        anchors = re.findall(r"^\s*t\w*\s*=\s*time\.(monotonic|perf_counter)\(\)", src, re.M)
        assert anchors == [], f"wall-clock anchors remain in the parity module: {anchors}"

    def test_no_wall_clock_measurement_asserts(self):
        """No assert compares a perf_counter/monotonic delta against a bound."""
        code = _strip_prose(_module_text())
        offenders = []
        for m in re.finditer(r"assert\s+[^\\\n]*(time\.monotonic|time\.perf_counter)", code):
            offenders.append(m.group(0).strip())
        assert offenders == [], f"wall-clock asserts remain: {offenders}"

    def test_every_timing_probe_reads_process_time(self):
        """The measurement probes that remain are CPU-time probes."""
        code = _strip_prose(_module_text())
        probes = re.findall(r"time\.(process_time|monotonic|perf_counter)\(\)", code)
        assert probes, "expected at least one CPU-time measurement probe"
        assert set(probes) == {"process_time"}, (
            f"measurement probes must be process_time only, got {sorted(set(probes))}"
        )

    def test_timing_helper_import_is_present(self):
        """The loopback round-trip bound uses the shared CPU-time budget helper.

        Reusing ``tests.e2e.chain_clock.budget_cpu_ms`` (rather than a private
        stopwatch) keeps one measurement implementation for the whole tree, so
        a future fix to the budget semantics repairs every consumer at once.
        """
        src = _module_text()
        assert "from tests.e2e.chain_clock import budget_cpu_ms" in src, (
            "loopback round-trip bound must use the shared CPU-time budget helper"
        )
        assert "budget_cpu_ms(" in src


class TestWarmupBeforeTiming:
    """Rule 2: the timed sample measures steady-state cost, not first-call init."""

    @pytest.mark.parametrize("test_name", _SLA_TESTS)
    def test_warmup_precedes_the_timed_loop(self, test_name: str):
        src = _module_text()
        body = _extract_test_body(src, test_name)
        assert body is not None, f"test not found in parity module: {test_name}"

        # The first ``process_time`` probe is the timed loop's anchor.
        first_probe = body.find("process_time()")
        assert first_probe != -1, f"{test_name}: no CPU-time measurement probe"

        head = body[:first_probe]
        # A warmup loop runs BEFORE the first timed probe. It must issue at
        # least one call through the adapter surface so the first timed call
        # is not also the process's first.
        warmup = re.findall(r"for\s+\w+\s+in\s+range\(\s*\d+\s*\):", head)
        assert warmup, (
            f"{test_name}: expected a warmup loop before the first timed probe; "
            "a first-call sample measures lazy init, not the serialization SLA"
        )

    @pytest.mark.parametrize("test_name", _SLA_TESTS)
    def test_warmup_calls_do_not_pollute_the_call_count(self, test_name: str):
        """Warmup calls are cleared before the measured window opens, so the
        ``len(calls) == 1000`` invariant still counts only timed iterations."""
        src = _module_text()
        body = _extract_test_body(src, test_name)
        assert body is not None, f"test not found: {test_name}"
        first_probe = body.find("process_time()")
        head = body[:first_probe]
        assert "calls.clear()" in head, (
            f"{test_name}: warmup must be cleared (calls.clear()) before the "
            "timed loop, or the call-count invariant silently inflates"
        )

    def test_loopback_round_trip_warms_up_before_the_budget(self):
        """The RPC round-trip bound warms the socket before measuring."""
        body = _extract_test_body(_module_text(), "test_round_trip_through_local_bridge_is_bounded")
        assert body is not None
        budget_pos = body.find("budget_cpu_ms(")
        assert budget_pos != -1, "loopback bound must use the CPU-time budget helper"
        head = body[:budget_pos]
        assert "get_last_tick" in head, "a warmup RPC must precede the timed window"


class TestLoadIndependentInvariantsStayHard:
    """Rule 3: the deterministic invariants are still asserted exactly."""

    @pytest.mark.parametrize("test_name", _SLA_TESTS)
    def test_call_count_and_action_invariants(self, test_name: str):
        body = _extract_test_body(_module_text(), test_name)
        assert body is not None, f"test not found: {test_name}"
        assert "assert len(calls) == 1000" in body, (
            f"{test_name}: the 1000-call invariant must stay hard"
        )
        if test_name == "test_order_serialization_latency_under_1ms":
            assert '["SEND_ORDER"] * 1000' in body, "action-identity invariant must stay hard"

    @pytest.mark.parametrize("test_name", _SLA_TESTS)
    def test_percentile_ordering_invariants(self, test_name: str):
        body = _extract_test_body(_module_text(), test_name)
        assert body is not None, f"test not found: {test_name}"
        assert "latencies.sort()" in body, "the sample must be sorted before percentile math"
        assert "p99 >= latencies[0]" in body, "min <= p99 ordering invariant must stay hard"
        assert "latencies[-1] >= p99" in body, "p99 <= max ordering invariant must stay hard"

    @pytest.mark.parametrize("test_name", _SLA_TESTS)
    def test_cpu_mean_bound_is_asserted(self, test_name: str):
        """The serialization SLA survived the remediation as a CPU-time mean
        bound — the contract is measured, not abandoned."""
        body = _extract_test_body(_module_text(), test_name)
        assert body is not None, f"test not found: {test_name}"
        assert re.search(r"mean_cpu_ms\s*<\s*1\.0", body), (
            f"{test_name}: the <1ms serialization SLA must be asserted on CPU time"
        )

    def test_round_trip_bound_is_asserted_on_cpu_time(self):
        body = _extract_test_body(_module_text(), "test_round_trip_through_local_bridge_is_bounded")
        assert body is not None
        assert re.search(r"consumed_ms\s*<\s*500\.0", body), (
            "loopback round-trip bound must be asserted on the CPU-time budget"
        )


class TestRemoteGatewayContractIsUntouched:
    """The remediation must not weaken the contract the suite verifies.

    ``RemoteMT5GatewayAdapter`` and its client contract are frozen (HARD
    CONSTRAINTS of ML-PLAT-001 and the swarm contract): every interaction is
    observed through the stdlib gateway fixture, never by patching adapter
    internals. This class proves the remediation only touched test-side
    measurement.
    """

    def test_no_adapter_source_is_referenced_for_edit(self):
        """The module imports the adapter for observation only — the source
        under ``src/`` is not opened, read or written by this suite."""
        src = _module_text()
        assert "from nexus_scalp.adapters.mt5.remote_gateway import" in src
        # The monkeypatch target is the adapter INSTANCE method (test-side
        # seam, network factored out), never a module-level patch of adapter
        # internals: ``monkeypatch.setattr(adapter, "_send_request", ...)``.
        assert 'monkeypatch.setattr(adapter, "_send_request"' in src

    def test_latency_tests_still_factor_out_the_network(self):
        """Both SLAs replace ``_send_request`` with a recorder, so the bound
        measures serialization cost, not a socket round-trip."""
        for test_name in _SLA_TESTS:
            body = _extract_test_body(_module_text(), test_name)
            assert body is not None, f"test not found: {test_name}"
            assert "_record" in body, f"{test_name}: the network must stay factored out of the SLA"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _extract_test_body(src: str, test_name: str) -> str | None:
    """Return the source of ``def <test_name>`` up to the next top-level def.

    Purely textual: the module is never imported here, so this contract test
    runs in the slim venv with no torch/polars dependency.
    """
    pattern = re.compile(
        r"^def " + re.escape(test_name) + r"\b.*?(?=^def |^class |\Z)",
        re.S | re.M,
    )
    m = pattern.search(src)
    return m.group(0) if m else None
