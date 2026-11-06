"""Lifecycle coordinator, setup state machine, invitation prefs, and the
diagnostics delivery ledger (lifecycle spec §3/§6.5/§11).

Contract under test:
- The startup handshake consumes the CANONICAL /api/client/me payload and
  never mistakes a network failure for a revoked star.
- An interrupted Easy Setup produces the correct resume hint (never a forced
  re-OAuth when a valid token exists).
- An invitation decision is remembered PER MODEL RELEASE.
- Diagnostics delivery records the server's ack id as evidence.
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from nexus_scalp.platform_link import PlatformLinkService, VerificationState
from nexus_scalp.platform_link.config import PlatformLinkConfig
from nexus_scalp.platform_link.invitations import (
    DECISION_DECLINED,
    DECISION_LATER,
    ModelInvitationPrefs,
)
from nexus_scalp.platform_link.lifecycle import (
    StartupHandshake,
    load_last_handshake,
    reset_lifecycle,
    run_startup_handshake,
    start_background_handshake,
)
from nexus_scalp.platform_link.states import state_from_startup_payload
from nexus_scalp.platform_link.store import PlatformLinkStore
from nexus_scalp.platform_link.client import (
    PlatformRateLimitedError,
    PlatformTokenRejectedError,
    PlatformUnavailableError,
)
from nexus_scalp.platform_link.setup_state import (
    SetupStage,
    load_stage,
    mark_stage,
    resume_hint,
)


# ---------------------------------------------------------------------------
# Fixtures (same isolation pattern as test_platform_link.py)
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_store(tmp_path, monkeypatch) -> PlatformLinkStore:
    from nexus_scalp.settings.secret_store import SecureSecretStore
    from nexus_scalp.settings.service import SettingsDatabase, SettingsService

    monkeypatch.setenv("NSE_USER_DATA_DIR", str(tmp_path))
    settings = SettingsService(
        db=SettingsDatabase(db_path=tmp_path / "settings.db"),
        secret_store=SecureSecretStore(root=tmp_path),
    )
    return PlatformLinkStore(settings=settings, secrets=SecureSecretStore(root=tmp_path))


@pytest.fixture()
def svc(isolated_store: PlatformLinkStore) -> PlatformLinkService:
    config = PlatformLinkConfig(base_url="http://127.0.0.1:1", cache_ttl_hours=24.0)
    return PlatformLinkService(config=config, store=isolated_store)


CANONICAL_PAYLOAD: dict[str, Any] = {
    "success": True,
    "request_id": "req-1",
    "github_login": "octocat",
    "installation_id": "install-1",
    "repository": "Opselon/NexusTradingForexBot",
    "verification_state": "VerifiedStarred",
    "entitlement_state": "VerifiedStarred",
    "entitlement": {
        "state": "VerifiedStarred",
        "access_allowed": True,
        "decision": "GrantedFresh",
        "checked_at": "2026-11-06T00:00:00+00:00",
        "valid_until": "2026-11-09T00:00:00+00:00",
        "next_recheck_at": "2026-11-06T00:15:00+00:00",
    },
    "model": {
        "available": True,
        "id": "m1",
        "version": "9.0.22",
        "sha256": "abc",
        "size_bytes": 17,
        "release_notes": "note",
        "benchmark": {"profit_factor": 1.4},
        "published_at": "2026-11-05T00:00:00+00:00",
    },
    "reason_code": "GrantedFresh",
}


class _StubClient:
    def __init__(self, behavior: Any) -> None:
        self.behavior = behavior
        self.calls = 0

    def client_me(self, token: str) -> dict[str, Any]:
        self.calls += 1
        if isinstance(self.behavior, Exception):
            raise self.behavior
        return self.behavior


def _with_client(svc: PlatformLinkService, behavior: Any) -> PlatformLinkService:
    svc.client = _StubClient(behavior)  # type: ignore[assignment]
    return svc


# ---------------------------------------------------------------------------
# Canonical payload mapping
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("decision", "expected"),
    [
        ("GrantedFresh", VerificationState.STARRED_VERIFIED),
        ("GrantedStale", VerificationState.STARRED_VERIFIED),
        ("Revoked", VerificationState.NOT_STARRED),
        ("Pending", VerificationState.VERIFICATION_PENDING),
        ("LeaseExpired", VerificationState.VERIFICATION_EXPIRED),
    ],
)
def test_decision_vocabulary_maps_to_local_states(decision, expected):
    payload = {"entitlement": {"decision": decision}}
    assert state_from_startup_payload(payload) is expected


def test_legacy_payload_still_maps_when_no_decision_present():
    legacy = {
        "verification_state": "VerifiedStarred",
        "entitlement_state": "VerifiedStarred",
    }
    assert state_from_startup_payload(legacy) is VerificationState.STARRED_VERIFIED


# ---------------------------------------------------------------------------
# Startup handshake
# ---------------------------------------------------------------------------


def test_handshake_happy_path_maps_all_fields(svc, isolated_store):
    isolated_store.save_client_token("nsc_test-token-0000000000")
    handshake = run_startup_handshake(_with_client(svc, dict(CANONICAL_PAYLOAD)), snapshot_store=isolated_store)

    assert handshake.ok is True
    assert handshake.state is VerificationState.STARRED_VERIFIED
    assert handshake.github_login == "octocat"
    assert handshake.entitlement_decision == "GrantedFresh"
    assert handshake.access_allowed is True
    assert handshake.checked_at == "2026-11-06T00:00:00+00:00"
    assert handshake.latest_model is not None
    assert handshake.latest_model["version"] == "9.0.22"
    assert handshake.latest_model["benchmark"] == {"profit_factor": 1.4}
    # update_available is advisory client-side comparison
    assert handshake.update_available is True  # nothing installed

    # snapshot persisted + reloadable
    cached = load_last_handshake(isolated_store)
    assert cached is not None and cached.ok and cached.github_login == "octocat"


def test_handshake_token_rejected_expires_and_clears(svc, isolated_store):
    isolated_store.save_client_token("nsc_stale-token-000000000")
    handshake = run_startup_handshake(
        _with_client(svc, PlatformTokenRejectedError("401")), snapshot_store=isolated_store
    )
    assert handshake.state is VerificationState.VERIFICATION_EXPIRED
    assert isolated_store.client_token() is None, "rejected token must be evicted"


def test_handshake_offline_never_reads_not_starred_and_keeps_cache(svc, isolated_store):
    isolated_store.save_client_token("nsc_test-token-0000000000")
    ok = run_startup_handshake(
        _with_client(svc, dict(CANONICAL_PAYLOAD)), snapshot_store=isolated_store
    )
    assert ok.ok

    offline = run_startup_handshake(
        _with_client(svc, PlatformUnavailableError("connection refused")),
        snapshot_store=isolated_store,
    )
    assert offline.state is VerificationState.STARRED_VERIFIED, (
        "offline handshake shows last known state — never NotStarred"
    )
    assert "unavailable" in (offline.reason or "").lower()


def test_handshake_rate_limited_is_unavailable_not_denied(svc, isolated_store):
    isolated_store.save_client_token("nsc_test-token-0000000000")
    handshake = run_startup_handshake(
        _with_client(svc, PlatformRateLimitedError("slow down")), snapshot_store=isolated_store
    )
    assert handshake.state is VerificationState.VERIFICATION_UNAVAILABLE


def test_handshake_without_token_is_not_connected(svc):
    handshake = run_startup_handshake(svc)
    assert handshake.ok is False
    assert handshake.state is VerificationState.NOT_CONNECTED


def test_handshake_update_available_false_when_current(svc, isolated_store, monkeypatch):
    isolated_store.save_client_token("nsc_test-token-0000000000")
    import nexus_scalp.platform_link.lifecycle as lifecycle

    monkeypatch.setattr(lifecycle, "_installed_model_version", lambda: "9.0.22")
    handshake = run_startup_handshake(
        _with_client(svc, dict(CANONICAL_PAYLOAD)), snapshot_store=isolated_store
    )
    assert handshake.installed_model_version == "9.0.22"
    assert handshake.update_available is False


# ---------------------------------------------------------------------------
# Single background owner (no polling chaos)
# ---------------------------------------------------------------------------


def test_background_handshake_is_single_owner(svc, isolated_store, monkeypatch):
    reset_lifecycle()
    import nexus_scalp.platform_link.lifecycle as lifecycle

    svc.store.save_client_token("nsc_test-token-0000000000")
    _with_client(svc, dict(CANONICAL_PAYLOAD))  # stub BEFORE the thread reads it
    threads = [start_background_handshake(svc) for _ in range(4)]
    started = [t for t in threads if t is not None]
    assert len(started) == 1, "exactly one handshake owner per process"
    if started[0].is_alive():
        started[0].join(timeout=10)
    snapshot = lifecycle.run_handshake_snapshot()
    assert snapshot is not None and snapshot.ok
    reset_lifecycle()


def test_handshake_roundtrip_via_dataclass_dict():
    snap = StartupHandshake.from_dict(dict(CANONICAL_PAYLOAD, state="STARRED_VERIFIED", ok=True))
    assert snap.state is VerificationState.STARRED_VERIFIED
    payload = snap.as_dict()
    assert payload["state"] == "STARRED_VERIFIED"
    assert StartupHandshake.from_dict(payload).state is VerificationState.STARRED_VERIFIED


# ---------------------------------------------------------------------------
# Setup state machine (§2.1/§4)
# ---------------------------------------------------------------------------


def test_setup_stage_roundtrip(isolated_store):
    assert load_stage(isolated_store.settings) is SetupStage.NOT_STARTED
    mark_stage(isolated_store.settings, SetupStage.AUTH_PENDING)
    assert load_stage(isolated_store.settings) is SetupStage.AUTH_PENDING


def test_resume_connected_but_model_missing_offers_install_not_reauth(isolated_store):
    mark_stage(isolated_store.settings, SetupStage.MODEL_PENDING)
    hint = resume_hint(
        isolated_store.settings,
        VerificationState.STARRED_VERIFIED,
        official_model_installed=False,
    )
    assert hint is not None
    assert hint.actions == ("install_model",)
    assert "already connected" in hint.headline.lower()


def test_resume_connected_not_verified_continues_setup(isolated_store):
    mark_stage(isolated_store.settings, SetupStage.ENTITLEMENT_PENDING)
    hint = resume_hint(
        isolated_store.settings,
        VerificationState.VERIFICATION_PENDING,
        official_model_installed=False,
    )
    assert hint is not None
    assert "continue_setup" in hint.actions
    assert "already connected" in hint.headline.lower()


def test_resume_cancelled_is_restartable(isolated_store):
    mark_stage(isolated_store.settings, SetupStage.USER_CANCELLED)
    hint = resume_hint(isolated_store.settings, VerificationState.NOT_CONNECTED)
    assert hint is not None
    assert "continue_setup" in hint.actions and "not_now" in hint.actions


def test_completed_setup_has_no_recovery_hint(isolated_store):
    mark_stage(isolated_store.settings, SetupStage.COMPLETED)
    assert (
        resume_hint(isolated_store.settings, VerificationState.STARRED_VERIFIED) is None
    )


def test_never_started_has_no_recovery_hint(isolated_store):
    assert resume_hint(isolated_store.settings, VerificationState.NOT_CONNECTED) is None


# ---------------------------------------------------------------------------
# Invitation preferences (§6.5)
# ---------------------------------------------------------------------------


def test_invitation_decline_remembered_for_release_only(isolated_store):
    prefs = ModelInvitationPrefs(isolated_store.settings)
    assert prefs.should_offer("9.0.22") is True

    prefs.record("9.0.22", DECISION_DECLINED)
    assert prefs.should_offer("9.0.22") is False, "no modal fatigue for the same release"
    assert prefs.should_offer("9.0.23") is True, "a NEW release is a new invitation"


def test_invitation_later_uses_bounded_cooldown(isolated_store):
    prefs = ModelInvitationPrefs(isolated_store.settings, later_cooldown_secs=100.0)
    prefs.record("9.0.22", DECISION_LATER, now=1_000.0)
    assert prefs.should_offer("9.0.22", now=1_050.0) is False
    assert prefs.should_offer("9.0.22", now=1_101.0) is True


def test_invitation_not_offered_when_already_current_or_denied(isolated_store):
    prefs = ModelInvitationPrefs(isolated_store.settings)
    assert prefs.should_offer("9.0.22", installed_version="9.0.22") is False
    assert prefs.should_offer("9.0.22", access_allowed=False) is False


def test_invitation_forget_resets_explicitly(isolated_store):
    prefs = ModelInvitationPrefs(isolated_store.settings)
    prefs.record("9.0.22", DECISION_DECLINED)
    prefs.forget("9.0.22")
    assert prefs.should_offer("9.0.22") is True


def test_invitation_rejects_unknown_decision(isolated_store):
    prefs = ModelInvitationPrefs(isolated_store.settings)
    with pytest.raises(ValueError):
        prefs.record("9.0.22", "maybe")


# ---------------------------------------------------------------------------
# Diagnostics delivery ledger (§11)
# ---------------------------------------------------------------------------


def test_shipper_records_server_ack_as_evidence(tmp_path, monkeypatch):
    from nexus_scalp.log_shipping.shipper import DiagnosticsShipper
    from nexus_scalp.settings.service import SettingsDatabase, SettingsService

    settings = SettingsService(db=SettingsDatabase(db_path=tmp_path / "settings.db"))
    monkeypatch.setattr("nexus_scalp.log_shipping.shipper.is_enabled", lambda s: True)
    monkeypatch.setenv("NSE_USER_DATA_DIR", str(tmp_path))

    class _AckClient:
        def __init__(self) -> None:
            self.batches: list[list[dict[str, Any]]] = []

        def _request(self, path, payload=None, token=None):  # noqa: ANN001
            self.batches.append(payload["records"])
            return {"received": len(payload["records"]), "id": "report-123"}

    shipper = DiagnosticsShipper(
        source="test",
        queue_dir=tmp_path,
        client=_AckClient(),
        settings=settings,
    )
    shipper.collector.add("ERROR", "t", "boom")
    delivered = shipper.flush_once()
    assert delivered == 1

    state = shipper.delivery_state()
    assert state["last_acknowledged_id"] == "report-123"
    assert state["pending_record_count"] == 0
    assert state["last_success"] > 0
    assert state["consent_enabled"] is True


def test_shipper_failure_records_code_and_pending(tmp_path, monkeypatch):
    from nexus_scalp.log_shipping.shipper import DiagnosticsShipper
    from nexus_scalp.settings.service import SettingsDatabase, SettingsService

    settings = SettingsService(db=SettingsDatabase(db_path=tmp_path / "settings.db"))
    monkeypatch.setattr("nexus_scalp.log_shipping.shipper.is_enabled", lambda s: True)
    monkeypatch.setenv("NSE_USER_DATA_DIR", str(tmp_path))

    class _DeadClient:
        def _request(self, path, payload=None, token=None):  # noqa: ANN001
            raise PlatformUnavailableError("connection refused")

    shipper = DiagnosticsShipper(
        source="test",
        queue_dir=tmp_path,
        client=_DeadClient(),
        settings=settings,
    )
    shipper.collector.add("WARNING", "t", "w")
    assert shipper.flush_once() == 0

    state = shipper.delivery_state()
    assert state["last_failure_code"] == "PLATFORM_UNAVAILABLE"
    assert state["pending_record_count"] == 1
    assert "last_success" not in state or not state.get("last_success")


def test_shipper_disabled_records_nothing(tmp_path, monkeypatch):
    from nexus_scalp.log_shipping.shipper import DiagnosticsShipper
    from nexus_scalp.settings.service import SettingsDatabase, SettingsService

    settings = SettingsService(db=SettingsDatabase(db_path=tmp_path / "settings.db"))
    monkeypatch.setattr("nexus_scalp.log_shipping.shipper.is_enabled", lambda s: False)
    shipper = DiagnosticsShipper(
        source="test",
        queue_dir=tmp_path,
        client=_StubClient(dict(CANONICAL_PAYLOAD)),
        settings=settings,
    )
    shipper.collector.add("ERROR", "t", "x")
    assert shipper.flush_once() == 0
    state = shipper.delivery_state()
    assert state["consent_enabled"] is False
    assert "last_attempt" not in state
