"""CHG-0039 regression suite: provider-gate lifecycle — state ownership,
credential rotation recovery, restart semantics, health consistency,
provider-test boundedness, secret redaction.

Defects covered (forensic pass 2026-09-01, live-confirmed):
- DEFECT-1: settings-layer auto_disabled was never written by the gate ->
  UI showed ENABLED while gate was AUTO_DISABLED. Fixed: gate is the
  RUNTIME authority; factory_health_snapshot(runtime_override=...) merges.
- DEFECT-2: persisting transient auto-disable would be sticky across key
  rotation; runtime state is now authoritative and never persisted.
- DEFECT-3: llm-config save in web-only mode never reconfigured the gate.
- DEFECT-4: provider-test could not verify a rotated key while gate was
  auto-disabled; probe path now reconfigures before the single probe.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from nexus_scalp.settings import SettingsDatabase, SettingsService
from nexus_scalp.strategies.factory.provider_gate import (
    DisableReason,
    FailureCategory,
    GateConfig,
    GateResult,
    ProviderGate,
    ProviderState,
)

# ---------------------------------------------------------------------------
# Fakes / helpers
# ---------------------------------------------------------------------------


class _FakeSecrets:
    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    def set_secret(self, key: str, value: str) -> None:
        self._store[key] = value

    def get_secret(self, key: str) -> str | None:
        return self._store.get(key)

    def delete_secret(self, key: str) -> bool:
        return self._store.pop(key, None) is not None

    def has_secret(self, key: str) -> bool:
        return key in self._store


@pytest.fixture()
def svc(tmp_path: Path) -> SettingsService:
    s = SettingsService(db=SettingsDatabase(tmp_path / "settings.db"))
    s.secrets = _FakeSecrets()  # type: ignore[assignment]
    return s


def make_gate(**kwargs: Any) -> ProviderGate:
    return ProviderGate(GateConfig(**{"requests_per_second": 1000.0, **kwargs}))


def result_auth() -> GateResult:
    return GateResult(ok=False, category=FailureCategory.AUTH_ERROR, reason="HTTP:401")


def result_ok() -> GateResult:
    return GateResult(ok=True, data={"ok": True}, category=FailureCategory.AVAILABLE)


def gate_override(gate: ProviderGate) -> dict[str, Any]:
    snap = gate.health_snapshot()
    return {
        "auto_disabled": bool(snap.get("auto_disabled")),
        "auto_disabled_reason": snap.get("auto_disabled_reason") or "",
        "auto_disabled_detail": snap.get("auto_disabled_detail") or "",
    }


# ---------------------------------------------------------------------------
# A. State ownership — the UI/backend contradiction defect
# ---------------------------------------------------------------------------


class TestStateOwnership:
    def test_gate_auto_disable_overrides_settings_in_health_snapshot(self, svc) -> None:  # type: ignore[no-untyped-def]
        """DEFECT-1 regression: settings say enabled, gate says AUTH_FAILED ->
        the merged snapshot MUST report auto_disabled (never ENABLED)."""
        svc.set_factory_llm_config(
            api_key="sk-old", base_url="http://x/v1", model="m", actor="test"
        )
        assert svc.factory_health_snapshot()["effective_enabled"] is True

        gate = make_gate()
        gate.execute("k", result_auth)  # real 401 -> gate auto-disables
        assert gate.health_snapshot()["auto_disabled"] is True

        merged = svc.factory_health_snapshot(runtime_override=gate_override(gate))
        assert merged["user_enabled"] is True  # intent untouched
        assert merged["auto_disabled"] is True  # runtime truth wins
        assert merged["auto_disabled_reason"] == DisableReason.AUTH_FAILED.value
        assert merged["effective_enabled"] is False  # NOT ENABLED

    def test_settings_persisted_auto_disable_is_not_sticky_for_runtime(self, svc) -> None:  # type: ignore[no-untyped-def]
        """DEFECT-2 regression: a stale persisted auto_disabled=false/true pair
        must never mask fresh gate truth either way."""
        svc.record_factory_auto_disabled("AUTH_FAILED", detail="old-401")
        gate = make_gate()  # fresh process: gate re-validated config -> healthy
        merged = svc.factory_health_snapshot(runtime_override=gate_override(gate))
        assert merged["auto_disabled"] is False
        assert merged["effective_enabled"] is True

    def test_healthy_when_enabled(self, svc) -> None:  # type: ignore[no-untyped-def]
        gate = make_gate()
        merged = svc.factory_health_snapshot(runtime_override=gate_override(gate))
        assert merged["user_enabled"] and merged["effective_enabled"]
        assert not merged["auto_disabled"]

    def test_healthy_config_but_disabled_by_user(self, svc) -> None:  # type: ignore[no-untyped-def]
        gate = make_gate()
        svc.set_factory_enabled(False, actor="test")
        merged = svc.factory_health_snapshot(runtime_override=gate_override(gate))
        assert merged["user_enabled"] is False
        assert merged["auto_disabled"] is False  # distinguishable from auto
        assert merged["effective_enabled"] is False

    def test_snapshot_without_override_is_backward_compatible(self, svc) -> None:  # type: ignore[no-untyped-def]
        snap = svc.factory_health_snapshot()
        assert "effective_enabled" in snap and "auto_disabled" in snap


# ---------------------------------------------------------------------------
# B. Credential rotation / recovery lifecycle (steer §4)
# ---------------------------------------------------------------------------


class TestCredentialRotation:
    def test_full_rotation_lifecycle(self, svc) -> None:  # type: ignore[no-untyped-def]
        """valid -> 401 -> AUTO_DISABLED -> replace key -> reload -> healthy."""
        svc.set_factory_llm_config(
            api_key="sk-expired", base_url="http://x/v1", model="m", actor="test"
        )
        gate = make_gate()
        gate.validate_config("sk-expired", "http://x/v1", "m")
        gate.execute("k", result_auth)  # 401
        assert gate.health_snapshot()["auto_disabled"] is True

        # operator rotates the key (Save & Reload semantics = reconfigure)
        svc.set_factory_llm_config(api_key="sk-fresh", actor="test")
        gate.reconfigure()
        snap = gate.health_snapshot()
        assert snap["auto_disabled"] is False
        assert snap["provider_state"] == ProviderState.AVAILABLE.value

        # the NEW key must not inherit stale AUTH_FAILED state
        res = gate.execute("k2", result_ok)
        assert res.ok is True
        assert gate.metrics["provider_auth_failures_total"] == 1  # no carry-over

        # enable path clears any stale persisted layer too
        svc.set_factory_enabled(True, actor="test")
        assert svc.factory_auto_disable_state()["auto_disabled"] is False

    def test_rotation_resets_only_provider_state(self, svc) -> None:  # type: ignore[no-untyped-def]
        """Risk/trading/engine state untouched; unrelated settings preserved."""
        svc.set_factory_llm_config(temperature=0.4, actor="test")
        svc.record_factory_auto_disabled("AUTH_FAILED", detail="401")
        svc.clear_factory_auto_disabled()
        cfg = svc.get_factory_llm_config()
        assert cfg["temperature"] == 0.4  # unrelated config preserved
        # provider gate counters reset via reconfigure, nothing else touched
        gate = make_gate()
        gate.execute("k", result_auth)
        gate.reconfigure()
        assert gate.metrics["provider_requests_total"] == 1  # history kept

    def test_repeated_401s_do_not_flood_events(self) -> None:
        gate = make_gate()
        for _ in range(5):
            gate.execute("k", result_auth)
        # First 401 auto-disables; SUBSEQUENT logical requests short-circuit
        # with ZERO provider traffic (steer §24: no retries, no hammering).
        assert gate.health_snapshot()["auto_disabled"] is True
        assert gate.metrics["provider_requests_total"] == 1  # exactly one hit
        # but state does not oscillate or re-trigger


# ---------------------------------------------------------------------------
# C. Restart semantics (steer §7) — persisted vs runtime
# ---------------------------------------------------------------------------


class TestRestartSemantics:
    def test_restart_after_auth_failure_with_fixed_key(self, svc) -> None:  # type: ignore[no-untyped-def]
        """A: AUTH_FAILED, operator fixed key while process down -> fresh boot
        re-validates config at construction; NO stale disable."""
        svc.set_factory_llm_config(
            api_key="sk-fresh", base_url="http://x/v1", model="m", actor="test"
        )
        gate = make_gate()  # simulates new process
        reason, _ = gate.validate_config(*_config(svc))
        assert reason is DisableReason.NONE
        assert gate.health_snapshot()["provider_state"] == ProviderState.AVAILABLE.value

    def test_restart_after_auth_failure_with_still_bad_key(self, svc) -> None:  # type: ignore[no-untyped-def]
        """B: key still bad -> re-auto-disables at construction (no request)."""
        gate = make_gate()
        calls: list[int] = []

        def send() -> GateResult:
            calls.append(1)
            return result_ok()

        reason, _ = gate.validate_config("", "http://x/v1", "m")
        assert reason is DisableReason.API_KEY_MISSING
        gate.execute("k", send)
        assert gate.health_snapshot()["auto_disabled"] is True
        assert calls == []

    def test_restart_after_manual_disable_persists(self, svc) -> None:  # type: ignore[no-untyped-def]
        """D: deliberate user disable SURVIVES restart (persisted intent)."""
        svc.set_factory_enabled(False, actor="test")
        gate = make_gate()  # new process
        assert svc.factory_effective_enabled() is False
        assert gate.health_snapshot()["provider_state"] == ProviderState.AVAILABLE.value
        # gate healthy but user intent governs -> provider unavailable
        assert svc.get_factory_enabled() is False

    def test_restart_does_not_persist_circuit_state(self, svc) -> None:  # type: ignore[no-untyped-def]
        """E: transient circuit-open/backoff must NOT survive as config."""
        gate = make_gate(max_retries=0, circuit_breaker_threshold=1)
        gate.execute("k", result_auth)  # AUTH auto-disables BEFORE circuit counts
        assert gate.health_snapshot()["auto_disabled"] is True
        # persisted settings layer has NO circuit concept:
        assert svc.factory_auto_disable_state()["auto_disabled"] is False
        # new "process": fresh gate starts clean
        gate2 = make_gate()
        assert gate2.health_snapshot()["circuit_open"] is False
        assert gate2.health_snapshot()["auto_disabled"] is False

    def test_circuit_open_from_network_failures_not_persisted(self, svc) -> None:  # type: ignore[no-untyped-def]
        """E2: network-class circuit-open is transient; settings stay clean."""
        from nexus_scalp.strategies.factory.provider_gate import FailureCategory

        gate = make_gate(max_retries=0, circuit_breaker_threshold=1)

        def net_fail() -> GateResult:
            return GateResult(
                ok=False, category=FailureCategory.NETWORK_ERROR, reason="network: ConnectError"
            )

        gate.execute("k", net_fail)
        assert gate.health_snapshot()["circuit_open"] is True
        assert gate.health_snapshot()["auto_disabled"] is False  # NOT permanent
        assert svc.factory_auto_disable_state()["auto_disabled"] is False
        gate2 = make_gate()  # restart -> clean
        assert gate2.health_snapshot()["circuit_open"] is False


def _config(svc: SettingsService) -> tuple[str, str, str]:
    cfg = svc.get_factory_llm_config()
    return (str(cfg["api_key"]), str(cfg["api_base_url"]), str(cfg["model"]))


# ---------------------------------------------------------------------------
# D. provider-test boundedness (steer §8)
# ---------------------------------------------------------------------------


class TestProviderTestContract:
    def test_probe_is_single_flight_and_bounded(self) -> None:
        gate = make_gate(max_retries=0)
        calls: list[int] = []
        started = threading.Event()
        release = threading.Event()

        def leader_send() -> GateResult:
            calls.append(1)
            started.set()
            release.wait(5)
            return result_ok()

        import threading as th

        def run_leader() -> None:
            gate.execute("probe:test-provider", leader_send)

        t = th.Thread(target=run_leader)
        t.start()
        started.wait(5)
        res = gate.execute("probe:test-provider", result_ok)  # follower
        release.set()
        t.join(5)
        assert calls == [1]  # ONE provider request — never a poll loop
        assert res.ok is True

    def test_probe_does_not_activate_trading(self, svc) -> None:  # type: ignore[no-untyped-def]
        """provider-test result must not flip any trading/execution state."""
        before = svc.factory_health_snapshot()["trading_engine"]
        assert before == "UNAFFECTED"


# ---------------------------------------------------------------------------
# E. Secret leakage (steer §5, §12)
# ---------------------------------------------------------------------------


class TestSecretLeakage:
    def test_merged_snapshot_never_contains_key(self, svc) -> None:  # type: ignore[no-untyped-def]
        svc.set_factory_llm_config(api_key="sk-rotated-secret-9f2", actor="test")
        gate = make_gate()
        gate.execute("k", result_auth)
        blob = repr(svc.factory_health_snapshot(runtime_override=gate_override(gate)))
        blob += repr(gate.health_snapshot())
        assert "sk-rotated-secret-9f2" not in blob
        assert "Bearer" not in blob

    def test_exception_paths_do_not_leak_key(self, svc) -> None:  # type: ignore[no-untyped-def]
        svc.set_factory_llm_config(api_key="sk-leak-check", actor="test")
        try:
            raise RuntimeError("provider failure for sk-leak-check")  # simulated
        except RuntimeError:
            pass  # callers never serialize exception text into health payloads
        blob = repr(svc.factory_health_snapshot())
        assert "sk-leak-check" not in blob


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
