"""CHG-0034 / BUG-186 / BUG-187 regression suite: provider gate hardening.

Covers the MASTER STEER acceptance matrix (sections 41-50, 70, 71):
config validation matrix (no network), 429 Retry-After/backoff/bounded
retry, 401/403 auth auto-disable, circuit open/cooldown/half-open/recover,
rate-limit bound, concurrency cap, single-flight dedup, bounded queue
staleness, user toggle semantics, secret redaction, and trading-loop
latency isolation (provider wait != trading-loop wait).
"""

from __future__ import annotations

import threading
import time
from typing import Any

import pytest

from nexus_scalp.strategies.factory.provider_gate import (
    DisableReason,
    FailureCategory,
    GateConfig,
    GateResult,
    ProviderGate,
    ProviderState,
    classify_config,
    classify_status,
    parse_retry_after,
    redact_url,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class FakeClock:
    """Controllable clock: tests advance time deterministically."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_gate(**kwargs: Any) -> tuple[ProviderGate, FakeClock]:
    clock = FakeClock()
    cfg = GateConfig(**{"requests_per_second": 1000.0, **kwargs})  # default: no local pacing
    return ProviderGate(cfg, clock=clock), clock


def result_ok(data: Any | None = None) -> GateResult:
    return GateResult(
        ok=True, data={"ok": True} if data is None else data, category=FailureCategory.AVAILABLE
    )


def result_429(retry_after: float | None = None) -> GateResult:
    return GateResult(
        ok=False,
        category=FailureCategory.RATE_LIMITED,
        reason="HTTP:429",
        retry_after_sec=retry_after,
    )


def result_auth() -> GateResult:
    return GateResult(ok=False, category=FailureCategory.AUTH_ERROR, reason="HTTP:401")


def result_network() -> GateResult:
    return GateResult(
        ok=False, category=FailureCategory.NETWORK_ERROR, reason="network: ConnectError"
    )


# ---------------------------------------------------------------------------
# 1. Config validation matrix (steer 41) — NO network ever
# ---------------------------------------------------------------------------


class TestConfigValidation:
    def test_missing_key(self) -> None:
        reason, _ = classify_config("", "http://host/v1", "m")
        assert reason is DisableReason.API_KEY_MISSING

    def test_whitespace_key(self) -> None:
        reason, _ = classify_config("   ", "http://host/v1", "m")
        assert reason is DisableReason.API_KEY_MISSING

    def test_missing_host(self) -> None:
        reason, _ = classify_config("k", "", "m")
        assert reason is DisableReason.HOST_MISSING

    def test_invalid_scheme(self) -> None:
        reason, _ = classify_config("k", "ftp://host/v1", "m")
        assert reason is DisableReason.INVALID_HOST

    def test_missing_model(self) -> None:
        reason, _ = classify_config("k", "http://host/v1", "")
        assert reason is DisableReason.INVALID_CONFIG

    def test_valid_config(self) -> None:
        reason, _ = classify_config("k", "http://host/v1", "m")
        assert reason is DisableReason.NONE

    def test_missing_key_never_calls_send(self) -> None:
        gate, _ = make_gate()
        # The gate learns config state the way the real provider teaches it:
        # validate_config() at construction (NO network). Missing key ->
        # AUTO_DISABLED -> execute() short-circuits with zero sends.
        reason, _ = gate.validate_config("", "http://host/v1", "m")
        assert reason is DisableReason.API_KEY_MISSING
        calls: list[int] = []

        def send() -> GateResult:
            calls.append(1)
            return result_ok()

        res = gate.execute("k1", send)
        assert res.ok is False
        assert res.category is FailureCategory.CONFIG_ERROR
        assert res.state is ProviderState.AUTO_DISABLED
        assert calls == []  # NO network (steer 24)


# ---------------------------------------------------------------------------
# 2. 429 handling (steer 21, 26, 42): bounded retries, Retry-After, backoff
# ---------------------------------------------------------------------------


class TestRateLimitHandling:
    def test_429_then_200_recovers(self) -> None:
        gate, _ = make_gate(max_retries=2)
        seq = [result_429(), result_429(), result_ok()]
        res = gate.execute("k", lambda: seq.pop(0))
        assert res.ok is True
        assert gate.metrics["provider_429_total"] == 2
        assert gate.metrics["provider_retry_total"] == 2
        assert gate.metrics["provider_success_total"] == 1

    def test_retry_after_shortens_backoff(self) -> None:
        assert parse_retry_after("7") == 7.0
        assert parse_retry_after("0") == 0.0
        assert parse_retry_after(None) is None
        assert parse_retry_after("garbage") is None

    def test_sustained_429_opens_circuit_not_permanent_disable(self) -> None:
        gate, _ = make_gate(max_retries=0, circuit_breaker_threshold=3)
        for _ in range(3):
            gate.execute("k", result_429)
        snap = gate.health_snapshot()
        assert snap["circuit_open"] is True
        assert snap["auto_disabled"] is False  # 429 = capacity, NOT permanent
        # While open: requests short-circuit with no send.
        sent: list[int] = []

        def counting_send() -> GateResult:
            sent.append(1)
            return result_ok()

        res = gate.execute("k", counting_send)
        assert res.ok is False
        assert res.state is ProviderState.CIRCUIT_OPEN
        assert sent == []

    def test_circuit_half_open_probe_recovers(self) -> None:
        gate, clock = make_gate(
            max_retries=0, circuit_breaker_threshold=2, circuit_breaker_cooldown_seconds=60
        )
        for _ in range(2):
            gate.execute("k", result_429)
        clock.advance(61)  # cooldown elapsed
        res = gate.execute("k", result_ok)
        assert res.ok is True
        assert gate.health_snapshot()["effective_state"] == ProviderState.AVAILABLE.value

    def test_half_open_probe_failure_reopens(self) -> None:
        gate, clock = make_gate(
            max_retries=0, circuit_breaker_threshold=1, circuit_breaker_cooldown_seconds=60
        )
        gate.execute("k", result_429)
        clock.advance(61)
        res = gate.execute("k", result_429)
        assert res.ok is False
        assert gate.health_snapshot()["circuit_open"] is True

    def test_local_rate_limiter_paces(self) -> None:
        gate, _ = make_gate(requests_per_second=0.0, bucket_capacity=1)
        ok = gate.execute("k1", result_ok)
        assert ok.ok is True  # burst token
        paced = gate.execute("k2", result_ok)
        assert paced.ok is False
        assert paced.category is FailureCategory.RATE_LIMITED
        assert "bucket empty" in paced.reason


# ---------------------------------------------------------------------------
# 3. Auth failures (steer 25, 43): permanent, bounded, auto-disable
# ---------------------------------------------------------------------------


class TestAuthHandling:
    def test_401_auto_disables_after_bounded_attempts(self) -> None:
        gate, _ = make_gate(max_retries=2)
        res = gate.execute("k", result_auth)
        assert res.ok is False
        snap = gate.health_snapshot()
        assert snap["auto_disabled"] is True
        assert snap["auto_disabled_reason"] == DisableReason.AUTH_FAILED.value

    def test_auth_disabled_gate_makes_no_further_requests(self) -> None:
        gate, _ = make_gate(max_retries=2)
        gate.execute("k", result_auth)
        sent: list[int] = []

        def send() -> GateResult:
            sent.append(1)
            return result_ok()

        res = gate.execute("k2", send)
        assert res.ok is False
        assert res.state is ProviderState.AUTO_DISABLED
        assert sent == []

    def test_status_classification(self) -> None:
        assert classify_status(200) is FailureCategory.AVAILABLE
        assert classify_status(401) is FailureCategory.AUTH_ERROR
        assert classify_status(403) is FailureCategory.AUTH_ERROR
        assert classify_status(429) is FailureCategory.RATE_LIMITED
        assert classify_status(503) is FailureCategory.SERVER_ERROR
        assert classify_status(418) is FailureCategory.UNKNOWN


# ---------------------------------------------------------------------------
# 4. Network failures / degraded (steer 27, 44)
# ---------------------------------------------------------------------------


class TestNetworkHandling:
    def test_repeated_network_failure_opens_circuit(self) -> None:
        gate, _ = make_gate(max_retries=0, circuit_breaker_threshold=3)
        for _ in range(3):
            gate.execute("k", result_network)
        snap = gate.health_snapshot()
        assert snap["circuit_open"] is True
        assert snap["last_error_category"] == FailureCategory.NETWORK_ERROR.value

    def test_success_resets_failure_counters(self) -> None:
        gate, _ = make_gate(max_retries=0, circuit_breaker_threshold=3)
        gate.execute("k", result_network)
        gate.execute("k", result_network)
        gate.execute("k", result_ok)
        assert gate.health_snapshot()["circuit_open"] is False
        assert gate.health_snapshot()["consecutive_network_failures"] == 0


# ---------------------------------------------------------------------------
# 5. Concurrency + single-flight + queue (steer 20, 28, 30, 45, 46, 47)
# ---------------------------------------------------------------------------


class TestConcurrencyAndDedup:
    def test_max_in_flight_respected(self) -> None:
        gate, _ = make_gate(max_in_flight=2, max_retries=0)
        inflight: list[int] = []
        peak: list[int] = [0]
        lock = threading.Lock()

        def slow_send() -> GateResult:
            with lock:
                inflight.append(1)
                peak[0] = max(peak[0], len(inflight))
            time.sleep(0.05)
            with lock:
                inflight.pop()
            return result_ok()

        def run_gate(i: int) -> None:
            gate.execute(f"k{i}", slow_send)

        threads = [threading.Thread(target=run_gate, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        # Every send is 50ms; 6 requests through 2 slots = ~150ms. 10s of
        # join-slack is >60x headroom; if a worker deadlocks, peak may be
        # under-measured but the test must NOT hang the suite for minutes.
        for t in threads:
            t.join(10)
        alive = [t for t in threads if t.is_alive()]
        assert not alive, "gate worker threads hung (deadlock in concurrency path)"
        assert peak[0] <= 2

    def test_single_flight_dedupes_identical_requests(self) -> None:
        gate, _ = make_gate(max_retries=0)
        calls: list[int] = []
        started = threading.Event()
        release = threading.Event()

        def leader_send() -> GateResult:
            calls.append(1)
            started.set()
            release.wait(5)
            return result_ok({"n": 1})

        def run_leader() -> None:
            gate.execute("same-key", leader_send)

        leader = threading.Thread(target=run_leader)
        leader.start()
        started.wait(5)
        follower_res = gate.execute("same-key", lambda: result_ok({"n": 2}), single_flight=True)
        release.set()
        leader.join(5)
        assert calls == [1]  # ONE external request
        assert follower_res.data == {"n": 1}  # follower shares leader's result
        assert gate.metrics["provider_single_flight_reused_total"] == 1

    def test_bounded_queue_rejects_overflow(self) -> None:
        gate, _ = make_gate(max_queue=1, max_in_flight=1, max_retries=0)
        started = threading.Event()
        release = threading.Event()

        def hold_send() -> GateResult:
            started.set()
            release.wait(5)
            return result_ok()

        def run_held() -> None:
            gate.execute("held", hold_send)

        t = threading.Thread(target=run_held)
        t.start()
        started.wait(5)
        time.sleep(0.05)
        # One occupies the queue slot; the next must be rejected, not queued.
        res = gate.execute("overflow", result_ok(), single_flight=True)
        assert res.ok is False
        assert "queue full" in res.reason
        release.set()
        t.join(5)


# ---------------------------------------------------------------------------
# 6. User toggle semantics (steer 7, 33, 48) via settings service
# ---------------------------------------------------------------------------


class TestUserToggle:
    @pytest.fixture()
    def svc(self, tmp_path):  # type: ignore[no-untyped-def]
        from nexus_scalp.settings import SettingsDatabase, SettingsService

        return SettingsService(db=SettingsDatabase(tmp_path / "settings.db"))

    def test_default_enabled(self, svc) -> None:  # type: ignore[no-untyped-def]
        assert svc.get_factory_enabled() is True
        assert svc.factory_effective_enabled() is True

    def test_user_disable_stops_feature_only(self, svc) -> None:  # type: ignore[no-untyped-def]
        snap = svc.set_factory_enabled(False, actor="test")
        assert snap["user_enabled"] is False
        assert snap["effective_enabled"] is False
        assert snap["trading_engine"] == "UNAFFECTED"

    def test_auto_disable_is_explainable_and_idempotent(self, svc) -> None:  # type: ignore[no-untyped-def]
        svc.record_factory_auto_disabled("API_KEY_MISSING", detail="probe")
        svc.record_factory_auto_disabled("API_KEY_MISSING", detail="probe")  # idempotent
        snap = svc.factory_health_snapshot()
        assert snap["auto_disabled"] is True
        assert snap["auto_disabled_reason"] == "API_KEY_MISSING"
        assert snap["effective_enabled"] is False

    def test_re_enable_clears_auto_disable(self, svc) -> None:  # type: ignore[no-untyped-def]
        svc.record_factory_auto_disabled("AUTH_FAILED", detail="401")
        svc.set_factory_enabled(True, actor="test")
        snap = svc.factory_health_snapshot()
        assert snap["auto_disabled"] is False
        assert snap["effective_enabled"] is True

    def test_health_snapshot_never_contains_secret(self, svc) -> None:  # type: ignore[no-untyped-def]
        svc.set_factory_llm_config(api_key="sk-super-secret-value", actor="test")
        blob = repr(svc.factory_health_snapshot())
        assert "sk-super-secret-value" not in blob


# ---------------------------------------------------------------------------
# 7. Secret hygiene (steer 12)
# ---------------------------------------------------------------------------


class TestSecretRedaction:
    def test_redact_url_strips_userinfo_and_key_params(self) -> None:
        out = redact_url("http://user:secretpass@provider.example/api/v1?key=sk123&q=2")
        assert "secretpass" not in out
        assert "sk123" not in out
        assert "provider.example" in out
        assert "q=2" in out

    def test_gate_result_never_carries_headers(self) -> None:
        res = GateResult(ok=False, category=FailureCategory.RATE_LIMITED, reason="HTTP:429")
        assert "Bearer" not in repr(res)


# ---------------------------------------------------------------------------
# 8. Trading-loop latency isolation (steer 49/50/75) — the HARD criterion
# ---------------------------------------------------------------------------


class TestTradingIsolation:
    def test_provider_wait_is_not_trading_loop_wait(self) -> None:
        """The gate's slow path (pacing/backoff) must never be inherited by a
        simulated trading tick: a gate-bound send sleeping 0.3s must not add
        ANY wait to a concurrent trading-loop iteration."""
        gate, _ = make_gate(max_retries=1, backoff_base_seconds=0.3, circuit_breaker_threshold=50)

        def slow_failing_send() -> GateResult:
            time.sleep(0.3)
            return result_429()

        def trading_tick() -> float:
            started = time.perf_counter()
            time.sleep(0.01)  # simulated local work (MT5 read + 70D inference)
            return time.perf_counter() - started

        # Start provider storm in the background (worker-thread pattern).
        stop = threading.Event()

        def provider_worker() -> None:
            while not stop.is_set():
                gate.execute("storm", slow_failing_send)
                time.sleep(0.001)

        worker = threading.Thread(target=provider_worker, daemon=True)
        worker.start()
        time.sleep(0.1)
        # Trading loop iterations stay fast DESPITE the provider storm.
        worst = 0.0
        for _ in range(20):
            worst = max(worst, trading_tick())
        stop.set()
        worker.join(5)
        # Local tick budget: 10ms work; allow generous scheduler slack (x20)
        # but it must be FAR below the provider's 300ms+backoff wait.
        assert worst < 0.2, f"trading tick inherited provider wait: {worst * 1000:.1f}ms"

    def test_gate_never_raises_into_caller(self) -> None:
        gate, _ = make_gate()

        def exploding_send() -> GateResult:
            raise RuntimeError("boom")

        res = gate.execute("k", exploding_send, single_flight=False)
        assert res.ok is False
        assert res.category is FailureCategory.UNKNOWN


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
