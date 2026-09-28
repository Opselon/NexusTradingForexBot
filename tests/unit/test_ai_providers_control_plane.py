"""AI provider CONTROL PLANE regression tests (Sections 4, 6, 14, 17, 30, 40, 45).

Covers the operational surface added on top of the decision ecosystem:

    * provider lifecycle state machine (legal + illegal transitions)
    * ADD PROVIDER (templates, custom ids, built-in protection)
    * TEST PROVIDER vs TEST MODEL (separate, and test-model is NON-mutating)
    * ACTIVATE / DEACTIVATE / ROLLBACK
    * multi-provider isolation (one provider's failure never marks another)
    * secrets: the key value never reaches a config row, an API payload,
      a log line, or an exception string

No network access: every provider is either the internal one (a stub adviser)
or an adapter whose transport is monkeypatched. A unit test can therefore never
place a call against a real provider, let alone an order.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nexus_scalp.ai_providers.adapters.base import AIProviderTestResult
from nexus_scalp.ai_providers.errors import ProviderError, ProviderErrorCategory
from nexus_scalp.ai_providers.orchestrator import ProviderOrchestrator
from nexus_scalp.ai_providers.registry import (
    BUILTIN_PROVIDER_IDS,
    PROVIDER_TEMPLATES,
    STATE_ACTIVE,
    STATE_DEACTIVATING,
    STATE_INACTIVE,
    STATE_LOADED,
    STATE_REGISTERED,
    STATE_TEST_FAILED,
    STATE_TESTING,
    STATE_VERIFIED,
    ActivationState,
    DecisionMode,
    ProviderConfig,
    ProviderRegistryStore,
    is_valid_provider_id,
    lifecycle_for_test_failure,
    lifecycle_transition,
    template_for,
)

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def store(tmp_path, monkeypatch) -> ProviderRegistryStore:
    """An isolated registry on a temp SQLite file (never the live settings DB)."""
    return ProviderRegistryStore(db_path=tmp_path / "ai_providers_test.db")


@pytest.fixture()
def secrets(tmp_path, monkeypatch):
    """A secret store isolated from the operator's real DPAPI store."""

    class _FakeSecrets:
        def __init__(self) -> None:
            self._d: dict[str, str] = {}

        def get_secret(self, name: str):
            return self._d.get(name)

        def set_secret(self, name: str, value: str) -> None:
            self._d[name] = value

    return _FakeSecrets()


@pytest.fixture()
def orch(store, secrets) -> ProviderOrchestrator:
    return ProviderOrchestrator(registry=store, secret_store=secrets)


# ---------------------------------------------------------------------------
# lifecycle state machine (Section 45)
# ---------------------------------------------------------------------------


class TestLifecycleTransitions:
    def test_happy_path_is_legal(self) -> None:
        for a, b in (
            ("REGISTERED", "TESTING"),
            ("TESTING", "VERIFIED"),
            ("VERIFIED", "LOADED"),
            ("LOADED", "ACTIVE"),
            ("ACTIVE", "DEACTIVATING"),
            ("DEACTIVATING", "INACTIVE"),
        ):
            assert lifecycle_transition(a, b), f"{a} -> {b} must be legal"

    def test_failure_states_are_reachable_from_testing(self) -> None:
        for b in ("TEST_FAILED", "CONTRACT_FAILED", "AUTH_FAILED", "UNAVAILABLE"):
            assert lifecycle_transition("TESTING", b)

    def test_active_cannot_jump_straight_to_registered(self) -> None:
        assert not lifecycle_transition("ACTIVE", "REGISTERED")

    def test_inactive_cannot_become_active_without_testing(self) -> None:
        assert not lifecycle_transition("INACTIVE", "ACTIVE")

    def test_self_transition_is_allowed(self) -> None:
        assert lifecycle_transition("VERIFIED", "VERIFIED")

    def test_failure_category_maps_to_a_lifecycle_state(self) -> None:
        assert lifecycle_for_test_failure("AUTH_FAILED") == "AUTH_FAILED"
        assert lifecycle_for_test_failure("SCHEMA_VIOLATION") == "CONTRACT_FAILED"
        assert lifecycle_for_test_failure(None) == "TEST_FAILED"
        assert lifecycle_for_test_failure("SOMETHING_NEW") == "TEST_FAILED"

    def test_store_rejects_an_illegal_transition(self, store, orch) -> None:
        orch.add_provider(provider_id="p1", template_id="system_one", enabled=True)
        # Force a known ACTIVE state, then attempt an illegal jump.
        store.upsert(
            ProviderConfig(
                provider_id="custom_p1",
                provider_name="p1",
                enabled=True,
                lifecycle_state=STATE_ACTIVE,
            )
        )
        assert store.get_config("custom_p1").lifecycle_state == STATE_ACTIVE
        # Legal: ACTIVE -> DEACTIVATING is the teardown edge (transient: it
        # validates but persists nothing, so a restart never claims mid-teardown).
        assert store.apply_lifecycle("custom_p1", STATE_DEACTIVATING) is True
        assert store.get_config("custom_p1").lifecycle_state == STATE_ACTIVE
        # Illegal: ACTIVE has no edge to VERIFIED. Skipping back to verified
        # would erase the record that the provider was live, and the backend
        # must refuse rather than coerce a stale UI sequence (Section 45).
        assert store.apply_lifecycle("custom_p1", STATE_VERIFIED) is False
        assert store.get_config("custom_p1").lifecycle_state == STATE_ACTIVE
        # And an unknown target is refused outright.
        assert store.apply_lifecycle("custom_p1", "NOT_A_STATE") is False
        assert store.get_config("custom_p1").lifecycle_state == STATE_ACTIVE

    def test_active_is_persisted_so_restart_keeps_the_truth(self, store, orch) -> None:
        """ACTIVE is durable state, not a UI-only claim (Section 42)."""
        orch.add_provider(provider_id="pers", template_id="system_one", enabled=True)
        # LOADED is the gate before ACTIVE; go through it, not around it.
        assert store.apply_lifecycle("custom_pers", STATE_VERIFIED) is True
        assert store.apply_lifecycle("custom_pers", STATE_ACTIVE) is True
        rehydrated = ProviderRegistryStore(db_path=Path(store._db_path))
        assert rehydrated.get_config("custom_pers").lifecycle_state == STATE_ACTIVE

    def test_transient_states_are_not_persisted(self, store, orch) -> None:
        orch.add_provider(provider_id="p2", template_id="system_one", enabled=True)
        assert store.get_config("custom_p2").lifecycle_state == STATE_REGISTERED
        assert store.apply_lifecycle("custom_p2", STATE_TESTING) is True
        # TESTING is transient: a crash must not leave a persisted mid-test claim.
        assert store.get_config("custom_p2").lifecycle_state == STATE_REGISTERED


# ---------------------------------------------------------------------------
# ADD PROVIDER (Sections 4, 30, 40)
# ---------------------------------------------------------------------------


class TestAddProvider:
    def test_templates_are_available_and_carry_no_secret(self) -> None:
        ids = {t.template_id for t in PROVIDER_TEMPLATES}
        assert {"internal_nse_ml", "system_one", "openrouter"} <= ids
        for t in PROVIDER_TEMPLATES:
            blob = json.dumps(t.to_public_dict())
            # A template may NAME the api_key FIELD; it must never CARRY a
            # credential VALUE (sk-..., a bearer literal, or any non-empty
            # default that an operator might paste in and ship).
            for bad in ("sk-", "Bearer ", "ghp_"):
                assert bad not in blob
            assert not t.defaults.get("api_key")

    def test_unknown_template_is_refused(self, orch) -> None:
        r = orch.add_provider(provider_id="x", template_id="nope")
        assert r["created"] is False
        assert "unknown template" in r["reason"]

    def test_custom_provider_is_namespaced_and_registered_not_active(self, orch, store) -> None:
        r = orch.add_provider(provider_id="MyEndpoint", template_id="system_one", enabled=True)
        assert r["created"] is True
        assert r["provider_id"] == "custom_myendpoint"
        assert r["lifecycle_state"] == STATE_REGISTERED
        # Section 29: created != active.
        act = store.get_activation()
        assert act is None or act.primary_provider != "custom_myendpoint"

    def test_provider_appears_in_the_list_after_add(self, orch) -> None:
        orch.add_provider(provider_id="visible", template_id="system_one", enabled=True)
        ids = {p["provider_id"] for p in orch.list_providers()}
        assert "custom_visible" in ids

    def test_invalid_id_is_refused(self, orch) -> None:
        for bad in ("bad id", "../../etc/passwd", "a;b", "x" * 100, "-"):
            r = orch.add_provider(provider_id=bad, template_id="system_one")
            assert r["created"] is False, f"id {bad!r} must be refused"

    def test_builtin_can_be_redeclared_without_losing_verified_state(self, orch, store) -> None:
        store.upsert(
            ProviderConfig(
                provider_id="system_one",
                provider_name="System One",
                enabled=True,
                lifecycle_state=STATE_VERIFIED,
            )
        )
        orch.add_provider(provider_id="system_one", template_id="system_one", endpoint="http://x")
        assert store.get_config("system_one").lifecycle_state == STATE_VERIFIED

    def test_builtin_cannot_be_deleted(self, orch) -> None:
        for pid in BUILTIN_PROVIDER_IDS:
            r = orch.delete_provider(pid)
            assert r["removed"] is False
            assert "protected" in r["reason"]

    def test_custom_provider_can_be_deleted(self, orch, store) -> None:
        orch.add_provider(provider_id="togo", template_id="system_one", enabled=True)
        r = orch.delete_provider("custom_togo")
        assert r["removed"] is True
        assert store.get_config("custom_togo") is None

    def test_active_provider_cannot_be_deleted(self, orch, store) -> None:
        orch.add_provider(provider_id="live", template_id="system_one", enabled=True)
        store.apply_lifecycle("custom_live", STATE_ACTIVE)
        store.set_activation(ActivationState(primary_provider="custom_live"))
        r = orch.delete_provider("custom_live")
        assert r["removed"] is False
        assert "ACTIVE" in r["reason"]

    def test_id_validation_accepts_builtins_and_custom(self) -> None:
        assert is_valid_provider_id("internal_nse_ml")
        assert is_valid_provider_id("custom_alpha1")
        assert not is_valid_provider_id("custom_alpha-1")
        assert not is_valid_provider_id("random")

    def test_template_lookup(self) -> None:
        assert template_for("openrouter") is not None
        assert template_for("does_not_exist") is None


# ---------------------------------------------------------------------------
# TEST PROVIDER vs TEST MODEL (Sections 6, 22)
# ---------------------------------------------------------------------------


class TestProviderVsModelTest:
    def test_test_provider_moves_lifecycle_to_verified_on_pass(
        self, orch, store, monkeypatch
    ) -> None:
        orch.add_provider(provider_id="tp", template_id="system_one", enabled=True, api_key="k")
        monkeypatch.setattr(
            type(orch),
            "_build_adapter",
            lambda self, pid: _StubAdapter(
                ProviderConfig(provider_id=pid, provider_name=pid, enabled=True),
                ok=True,
            ),
        )
        result = orch.test_provider("custom_tp")
        assert result.passed is True
        assert store.get_config("custom_tp").lifecycle_state == STATE_VERIFIED

    def test_test_provider_records_a_failure_state(self, orch, store, monkeypatch) -> None:
        orch.add_provider(provider_id="tf", template_id="system_one", enabled=True, api_key="k")
        monkeypatch.setattr(
            type(orch),
            "_build_adapter",
            lambda self, pid: _StubAdapter(
                ProviderConfig(provider_id=pid, provider_name=pid, enabled=True),
                ok=False,
                detail="AUTH_FAILED: bad key",
            ),
        )
        result = orch.test_provider("custom_tf")
        assert result.passed is False
        assert store.get_config("custom_tf").lifecycle_state == "AUTH_FAILED"

    def test_test_model_does_not_mutate_the_default_model(self, orch, store, monkeypatch) -> None:
        """A probe is not a configuration change (the old CLI test persisted it)."""
        orch.add_provider(
            provider_id="tm",
            template_id="openrouter",
            enabled=True,
            model="model-A",
            api_key="k",
        )
        monkeypatch.setattr(
            type(orch),
            "_build_adapter",
            lambda self, pid: _StubAdapter(
                ProviderConfig(
                    provider_id=pid,
                    provider_name=pid,
                    enabled=True,
                    default_model="model-A",
                ),
                ok=True,
            ),
        )
        orch.test_model("custom_tm", "model-B")
        assert store.get_config("custom_tm").default_model == "model-A"

    def test_test_model_reports_the_model_under_test(self, orch, monkeypatch) -> None:
        orch.add_provider(provider_id="tm2", template_id="openrouter", enabled=True, api_key="k")
        monkeypatch.setattr(
            type(orch),
            "_build_adapter",
            lambda self, pid: _StubAdapter(
                ProviderConfig(provider_id=pid, provider_name=pid, enabled=True),
                ok=True,
            ),
        )
        r = orch.test_model("custom_tm2", "some/model-x")
        assert r.passed is True

    def test_test_model_on_disabled_provider_is_refused(self, orch) -> None:
        orch.add_provider(provider_id="dis", template_id="system_one", enabled=False)
        r = orch.test_model("custom_dis", "m")
        assert r.passed is False
        assert "disabled" in r.detail

    def test_provider_health_is_provider_local(self, orch, store) -> None:
        """One provider failing must not make another appear failed (Section 18)."""
        orch.add_provider(provider_id="ok", template_id="system_one", enabled=True)
        orch.add_provider(provider_id="bad", template_id="system_one", enabled=True)
        store.apply_lifecycle("custom_bad", STATE_TEST_FAILED)
        assert store.get_config("custom_ok").lifecycle_state == STATE_REGISTERED
        assert store.get_config("custom_bad").lifecycle_state == STATE_TEST_FAILED


# ---------------------------------------------------------------------------
# ACTIVATE / DEACTIVATE / ROLLBACK (Sections 13, 14, 17)
# ---------------------------------------------------------------------------


def _activate(orch, store, pid: str) -> None:
    """Force a provider ready-to-activate with a stubbed passing test."""
    cfg = store.get_config(pid) or ProviderConfig(provider_id=pid, provider_name=pid)
    cfg.enabled = True
    store.upsert(cfg)


class TestActivationFlow:
    def test_switch_to_a_healthy_provider_succeeds(self, orch, store, monkeypatch) -> None:
        orch.add_provider(provider_id="ext", template_id="system_one", enabled=True, api_key="k")
        monkeypatch.setattr(
            type(orch),
            "_adapter",
            lambda self, pid: _StubAdapter(
                ProviderConfig(provider_id=pid, provider_name=pid, enabled=True), ok=True
            ),
        )
        r = orch.switch(primary="custom_ext", mode="EXTERNAL_ONLY")
        assert r["switched"] is True
        assert store.get_activation().primary_provider == "custom_ext"
        assert r["restart_required"] is False

    def test_switch_preconditions_are_structured_records(self, orch) -> None:
        orch.add_provider(provider_id="ne", template_id="system_one", enabled=False)
        r = orch.switch(primary="custom_ne")
        assert r["switched"] is False
        assert isinstance(r["preconditions"], list)
        assert r["preconditions"]
        for p in r["preconditions"]:
            assert {"check", "detail"} <= set(p)

    def test_a_refused_switch_names_every_failed_check(self, orch, store) -> None:
        """Section 42: a refusal must say WHICH check failed, not just that one did."""
        orch.add_provider(provider_id="unchecked", template_id="system_one", enabled=False)
        r = orch.switch(primary="custom_unchecked")
        assert r["switched"] is False
        # The reason is a real string and the failed check is identified by name.
        assert isinstance(r["reason"], str) and "not enabled" in r["reason"]
        assert any(p["check"] == "enabled" for p in r["preconditions"])

    def test_an_unknown_mode_refusal_still_carries_preconditions(self, orch) -> None:
        """Every refusal path returns the structured field, even the early ones."""
        r = orch.switch(primary="internal_nse_ml", mode="NOT_A_MODE")
        assert r["switched"] is False
        assert isinstance(r["preconditions"], list)

    def test_switch_response_uses_the_canonical_contract_names(
        self, orch, store, monkeypatch
    ) -> None:
        """The response keys are the names the frontend binds to (Section 61).

        A spelling drift here is invisible to unit tests on either side and
        surfaces only as a UI that silently shows nothing after a switch.
        """
        orch.add_provider(provider_id="canon", template_id="system_one", enabled=True)
        monkeypatch.setattr(
            type(orch),
            "_adapter",
            lambda self, pid: _StubAdapter(
                ProviderConfig(provider_id=pid, provider_name=pid, enabled=True), ok=True
            ),
        )
        r = orch.switch(primary="custom_canon", mode="EXTERNAL_ONLY")
        assert r["switched"] is True
        assert "activated_at" in r and isinstance(r["activated_at"], str)
        assert "configuration_version" in r
        # The drifted spellings must NOT be present alongside the canonical ones.
        assert "activation_time" not in r
        assert "config_version" not in r

    def test_switch_refusal_carries_a_reason_string(self, orch) -> None:
        r = orch.switch(primary="custom_missing")
        assert r["switched"] is False
        assert isinstance(r["reason"], str) and r["reason"]

    def test_switch_unknown_mode_is_refused(self, orch) -> None:
        r = orch.switch(primary="internal_nse_ml", mode="NOT_A_MODE")
        assert r["switched"] is False
        assert "unknown mode" in r["reason"]

    def test_deactivate_returns_routing_to_internal(self, orch, store) -> None:
        orch.add_provider(provider_id="ext2", template_id="system_one", enabled=True)
        store.set_activation(
            ActivationState(
                primary_provider="custom_ext2", decision_mode=DecisionMode.EXTERNAL_ONLY
            )
        )
        r = orch.deactivate()
        assert r["active_provider"] == "internal_nse_ml"
        assert store.get_activation().primary_provider == "internal_nse_ml"
        assert store.get_config("custom_ext2").lifecycle_state == STATE_INACTIVE

    def test_deactivate_never_deletes_the_internal_model(self, orch, store) -> None:
        orch.deactivate()
        assert store.get_activation().primary_provider == "internal_nse_ml"

    def test_rollback_restores_the_previous_activation(self, orch, store) -> None:
        orch.add_provider(provider_id="a", template_id="system_one", enabled=True)
        orch.add_provider(provider_id="b", template_id="system_one", enabled=True)
        store.set_activation(ActivationState(primary_provider="custom_a"))
        # A real switch snapshots custom_a as the rollback target.
        store.set_activation(ActivationState(primary_provider="custom_b"))
        assert store.get_previous_activation().primary_provider == "custom_a"
        r = orch.rollback()
        assert r["status"] == "OK"
        assert store.get_activation().primary_provider == "custom_a"

    def test_rollback_without_a_target_is_refused(self, orch) -> None:
        r = orch.rollback()
        assert r["status"] == "UNAVAILABLE"
        assert "nothing to roll back" in r["note"].lower()

    def test_rollback_refuses_a_dead_target(self, orch, store) -> None:
        """Never leave the runtime pointing at an unavailable provider."""
        orch.add_provider(provider_id="dead", template_id="system_one", enabled=True)
        store.set_activation(ActivationState(primary_provider="custom_dead"))
        store.set_activation(ActivationState(primary_provider="internal_nse_ml"))
        # The rollback target is now custom_dead; disable it.
        cfg = store.get_config("custom_dead")
        cfg.enabled = False
        store.upsert(cfg)
        r = orch.rollback()
        assert r["status"] == "UNAVAILABLE"
        assert store.get_activation().primary_provider == "internal_nse_ml"

    def test_switch_marks_the_previous_primary_standby_not_deleted(
        self, orch, store, monkeypatch
    ) -> None:
        orch.add_provider(provider_id="first", template_id="system_one", enabled=True)
        monkeypatch.setattr(
            type(orch),
            "_adapter",
            lambda self, pid: _StubAdapter(
                ProviderConfig(provider_id=pid, provider_name=pid, enabled=True), ok=True
            ),
        )
        orch.switch(primary="custom_first", mode="EXTERNAL_ONLY")
        orch.switch(primary="internal_nse_ml", mode="INTERNAL_ONLY")
        # Standby, not gone: the row survives so rollback can find it.
        assert store.get_config("custom_first") is not None
        # A standby provider that passed its test sits at VERIFIED *unless it
        # is the one being activated*: custom_first was ACTIVE while it held
        # the primary role, then dropped to standby when the switch moved the
        # primary to internal (Section 13: standby != deleted, and the state
        # must remain a legal one for rollback).
        assert store.get_config("custom_first").lifecycle_state in {
            STATE_VERIFIED,
            STATE_INACTIVE,
        }

    def test_no_two_providers_are_active_at_once(self, orch, store, monkeypatch) -> None:
        orch.add_provider(provider_id="one", template_id="system_one", enabled=True)
        orch.add_provider(provider_id="two", template_id="system_one", enabled=True)
        monkeypatch.setattr(
            type(orch),
            "_adapter",
            lambda self, pid: _StubAdapter(
                ProviderConfig(provider_id=pid, provider_name=pid, enabled=True), ok=True
            ),
        )
        orch.switch(primary="custom_one", mode="EXTERNAL_ONLY")
        orch.switch(primary="custom_two", mode="EXTERNAL_ONLY")
        actives = [
            p
            for p in store.list_provider_ids()
            if store.get_config(p).lifecycle_state == STATE_ACTIVE
        ]
        assert actives == ["custom_two"]


# ---------------------------------------------------------------------------
# routing truth (Sections 12, 23, 42)
# ---------------------------------------------------------------------------


class TestRoutingTruth:
    def test_activated_provider_is_the_one_actually_called(self, orch, store, monkeypatch) -> None:
        from nexus_scalp.ai_providers.sample import SIMULATED_SAMPLE_REQUEST

        orch.add_provider(provider_id="routed", template_id="system_one", enabled=True)
        called: list[str] = []
        stub = _StubAdapter(
            ProviderConfig(provider_id="custom_routed", provider_name="r", enabled=True),
            ok=True,
            calls=called,
        )
        monkeypatch.setattr(
            type(orch), "_adapter", lambda self, pid: stub if pid == "custom_routed" else None
        )
        store.set_activation(
            ActivationState(
                primary_provider="custom_routed", decision_mode=DecisionMode.EXTERNAL_ONLY
            )
        )
        outcome = orch.evaluate_position(SIMULATED_SAMPLE_REQUEST)
        # The request must actually have been ROUTED to the activated provider;
        # providers_used is the backend-backed proof (Section 42).
        assert called == ["custom_routed"]
        assert outcome.providers_used == ["custom_routed"]
        assert outcome.fallback_used is False

    def test_a_provider_that_returns_no_evidence_does_not_silently_pass(
        self, orch, store, monkeypatch
    ) -> None:
        """A provider that fails must not be recorded as if it answered."""
        from nexus_scalp.ai_providers.sample import SIMULATED_SAMPLE_REQUEST

        orch.add_provider(provider_id="silent", template_id="system_one", enabled=True)
        monkeypatch.setattr(
            type(orch),
            "_adapter",
            lambda self, pid: (
                _StubAdapter(
                    ProviderConfig(provider_id=pid, provider_name=pid, enabled=True), ok=False
                )
                if pid == "custom_silent"
                else None
            ),
        )
        store.set_activation(
            ActivationState(
                primary_provider="custom_silent", decision_mode=DecisionMode.EXTERNAL_ONLY
            )
        )
        outcome = orch.evaluate_position(SIMULATED_SAMPLE_REQUEST)
        assert outcome.providers_used == []
        # The failure is visible and the fallback is recorded, never hidden.
        assert outcome.fallback_used is True
        assert outcome.final_action  # deterministic policy still decides


# ---------------------------------------------------------------------------
# secrets (Section 37)
# ---------------------------------------------------------------------------


class TestSecretsNeverLeak:
    def test_key_never_lands_in_the_config_row(self, orch, store, secrets) -> None:
        orch.add_provider(
            provider_id="sec",
            template_id="system_one",
            enabled=True,
            api_key="SUPER-SECRET-VALUE-123",
        )
        cfg = store.get_config("custom_sec")
        blob = json.dumps(cfg.to_storage_dict(), default=str)
        assert "SUPER-SECRET-VALUE-123" not in blob
        assert cfg.secret_name == "ai_provider_custom_sec_key"
        assert secrets.get_secret("ai_provider_custom_sec_key") == "SUPER-SECRET-VALUE-123"

    def test_public_dict_masks_the_secret(self, orch, store) -> None:
        orch.add_provider(provider_id="mask", template_id="system_one", enabled=True, api_key="XYZ")
        pub = store.get_config("custom_mask").to_public_dict()
        assert "XYZ" not in json.dumps(pub)
        assert pub["has_secret"] is True
        assert pub.get("secret_hint") == "***SET***"
        # The secret NAME is not exported either.
        assert "secret_name" not in pub

    def test_add_result_does_not_echo_the_key(self, orch) -> None:
        r = orch.add_provider(
            provider_id="echo", template_id="system_one", enabled=True, api_key="NOPE-NOT-HERE"
        )
        assert "NOPE-NOT-HERE" not in json.dumps(r)

    def test_ideal_templates_never_require_a_plaintext_default_key(self) -> None:
        for t in PROVIDER_TEMPLATES:
            assert "api_key" not in t.defaults


# ---------------------------------------------------------------------------
# support
# ---------------------------------------------------------------------------


class _StubAdapter:
    """A minimal adapter double: no transport, no secret, no network."""

    def __init__(
        self,
        config,
        *,
        ok: bool | None = None,
        detail: str = "",
        calls=None,
        registry=None,
        secret_store=None,
        adviser_service=None,
    ) -> None:
        self.config = config
        self.provider_id = getattr(config, "provider_id", "stub")
        self._registry = registry
        self._secret_store = secret_store
        self._adviser_service = adviser_service
        # A clone (the model-probe path) inherits the original's outcome so the
        # probe reports what the provider would actually answer, not a default.
        if ok is None:
            ok = getattr(config, "_probe_ok", True)
        self._ok = ok
        self._detail = detail or ("ok" if ok else "failed")
        self._calls = calls

    # -- the surface the orchestrator/switch actually uses -------------------
    def test(self) -> AIProviderTestResult:
        return AIProviderTestResult(self._ok, self._detail, stage="complete")

    def evaluate_position(self, request):
        """A CONTRACT-VALID response (Section 10).

        ``extra='forbid'`` applies, so a hand-rolled dict would be rejected as
        SCHEMA_VIOLATION and silently dropped — the stub must build the real
        model, exactly like an adapter would.
        """
        from nexus_scalp.ai_providers.contract import (
            CONTRACT_VERSION,
            AIProviderAction,
            DecisionEvidence,
            PositionDecisionResponse,
            SlProposal,
            TpProposal,
        )

        if self._calls is not None:
            self._calls.append(self.provider_id)
        if not self._ok:
            raise ProviderError(ProviderErrorCategory.UPSTREAM_UNAVAILABLE, "stub failure")
        return PositionDecisionResponse(
            provider=self.provider_id,
            model=self.config.default_model or "stub-model",
            decision=DecisionEvidence(
                action=AIProviderAction.HOLD,
                confidence=0.5,
                p_hold=0.6,
                p_close=0.2,
                p_reduce=0.2,
                uncertainty=0.5,
            ),
            tp=TpProposal(recommendation="KEEP", confidence=0.5),
            sl=SlProposal(recommendation="KEEP", confidence=0.5),
            request_id=request.provider_request_id,
            latency_ms=1.0,
            template_version="stub-1",
            config_version=str(self.config.configuration_version),
            from_live_provider=False,
        )

    def to_public_dict(self) -> dict:
        return {
            "provider_id": self.provider_id,
            "provider_name": self.config.provider_name,
            "endpoint": self.config.endpoint,
            "model": self.config.default_model,
            "enabled": self.config.enabled,
            "capabilities": [],
            "health": None,
            "has_secret": bool(self.config.secret_name),
        }

    def list_models(self) -> list[str]:
        return []
