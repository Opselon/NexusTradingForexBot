"""Platform link subsystem tests.

Covers:
- state machine mapping (platform vocabulary -> local states)
- client error taxonomy (network/5xx NEVER maps to NOT_STARRED)
- fail-closed gate: no token / not starred / expired / unavailable / fresh
- end-to-end connect flow against a mock platform server (in-process HTTP)
- enforcement at the FirstRunCoordinator.download_official choke point
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

from nexus_scalp.platform_link import (
    LinkStatus,
    PlatformAuthorizationRequiredError,
    PlatformLinkService,
    VerificationState,
)
from nexus_scalp.platform_link.client import (
    PlatformClient,
    PlatformInvalidResponseError,
    PlatformTokenRejectedError,
    PlatformUnavailableError,
)
from nexus_scalp.platform_link.config import PlatformLinkConfig
from nexus_scalp.platform_link.states import state_from_platform_payload
from nexus_scalp.platform_link.store import PlatformLinkStore

# ---------------------------------------------------------------------------
# Fixtures: isolated stores (tmp user-data) so the real profile is untouched.
# ---------------------------------------------------------------------------


@pytest.fixture()
def isolated_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> PlatformLinkStore:
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


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ver", "ent", "expected"),
    [
        ("VerifiedStarred", "VerifiedStarred", VerificationState.STARRED_VERIFIED),
        ("VerifiedStarred", "NotStarred", VerificationState.VERIFICATION_PENDING),
        ("VerifiedStarred", "", VerificationState.VERIFICATION_PENDING),
        ("NotStarred", "NotStarred", VerificationState.NOT_STARRED),
        # A pending re-check does not resurrect access: the entitlement's last
        # definitive answer (NotStarred) stays authoritative.
        ("VerificationPending", "NotStarred", VerificationState.NOT_STARRED),
        ("VerificationFailed", "None", VerificationState.VERIFICATION_PENDING),
        ("AuthorizationExpired", "None", VerificationState.VERIFICATION_EXPIRED),
        ("", "", VerificationState.VERIFICATION_PENDING),
    ],
)
def test_state_mapping(ver: str, ent: str, expected: VerificationState) -> None:
    assert state_from_platform_payload(ver, ent) is expected


def test_only_starred_allows_downloads() -> None:
    for state in VerificationState:
        assert state.allows_official_download == (state is VerificationState.STARRED_VERIFIED)


# ---------------------------------------------------------------------------
# Client error taxonomy
# ---------------------------------------------------------------------------


class _MockServer(ThreadingHTTPServer):
    def __init__(self, handler_cls: type[BaseHTTPRequestHandler]) -> None:
        super().__init__(("127.0.0.1", 0), handler_cls)
        self.url = f"http://127.0.0.1:{self.server_port}"


def _client_for(handler_cls: type[BaseHTTPRequestHandler]) -> PlatformClient:
    server = _MockServer(handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    config = PlatformLinkConfig(base_url=server.url, connect_timeout=2.0, read_timeout=2.0)
    client = PlatformClient(config)
    client._server = server  # type: ignore[attr-defined] (keep alive on client)
    return client


class _Handler500(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(503)
        self.end_headers()

    def log_message(self, *args: object) -> None:  # silence
        return None


class _Handler401(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(401)
        self.end_headers()

    def log_message(self, *args: object) -> None:
        return None


class _HandlerGarbage(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(b"<html>not json</html>")

    def log_message(self, *args: object) -> None:
        return None


_ME_PAYLOAD: ClassVar[dict] = {
    "verification_state": "VerifiedStarred",
    "entitlement_state": "VerifiedStarred",
    "github_login": "octocat",
}


class _HandlerMe(BaseHTTPRequestHandler):
    payload: ClassVar[dict] = _ME_PAYLOAD

    def do_GET(self) -> None:
        body = json.dumps(self.payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return None


def test_5xx_maps_to_unavailable_not_not_starred() -> None:
    client = _client_for(_Handler500)
    try:
        with pytest.raises(PlatformUnavailableError):
            client.client_me("nsc_test")
    finally:
        client._server.shutdown()  # type: ignore[attr-defined]


def test_401_maps_to_token_rejected() -> None:
    client = _client_for(_Handler401)
    try:
        with pytest.raises(PlatformTokenRejectedError):
            client.client_me("nsc_test")
    finally:
        client._server.shutdown()  # type: ignore[attr-defined]


def test_non_json_maps_to_invalid_response() -> None:
    client = _client_for(_HandlerGarbage)
    try:
        with pytest.raises(PlatformInvalidResponseError):
            client.client_me("nsc_test")
    finally:
        client._server.shutdown()  # type: ignore[attr-defined]


def test_dead_server_maps_to_unavailable() -> None:
    client = PlatformClient(
        PlatformLinkConfig(base_url="http://127.0.0.1:9", connect_timeout=1.0, read_timeout=1.0)
    )
    with pytest.raises(PlatformUnavailableError):
        client.client_me("nsc_test")


def test_me_payload_parses() -> None:
    client = _client_for(_HandlerMe)
    try:
        out = client.client_me("nsc_ok")
        assert out["github_login"] == "octocat"
    finally:
        client._server.shutdown()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Gate behavior (service-level, no network)
# ---------------------------------------------------------------------------


class _FixedClient:
    """Stub PlatformClient with canned responses."""

    def __init__(self, me_payload: dict | None = None, error: Exception | None = None) -> None:
        self.me_payload = me_payload
        self.error = error
        self.me_calls = 0

    def start_desktop_session(self, installation_id: str) -> dict:  # pragma: no cover
        raise AssertionError("not expected in gate tests")

    def poll_desktop_session(
        self, session_id: str, session_secret: str
    ) -> dict:  # pragma: no cover
        raise AssertionError("not expected in gate tests")

    def client_me(self, token: str) -> dict:
        self.me_calls += 1
        if self.error is not None:
            raise self.error
        assert self.me_payload is not None
        return self.me_payload


def _service_with(client: _FixedClient, store: PlatformLinkStore) -> PlatformLinkService:
    config = PlatformLinkConfig(base_url="http://127.0.0.1:1", cache_ttl_hours=24.0)
    return PlatformLinkService(config=config, store=store, client=client)  # type: ignore[arg-type]


def test_gate_denies_without_token(svc: PlatformLinkService) -> None:
    with pytest.raises(PlatformAuthorizationRequiredError) as excinfo:
        svc.require_download_authorization()
    assert excinfo.value.status.state is VerificationState.NOT_CONNECTED


def test_gate_allows_when_verified(
    svc: PlatformLinkService, isolated_store: PlatformLinkStore
) -> None:
    isolated_store.save_client_token("nsc_good")
    svc.client = _FixedClient(
        me_payload={
            "verification_state": "VerifiedStarred",
            "entitlement_state": "VerifiedStarred",
            "github_login": "octocat",
            "repository": "Opselon/NexusTradingForexBot",
        }
    )  # type: ignore[assignment]
    status = svc.require_download_authorization()
    assert status.state is VerificationState.STARRED_VERIFIED


def test_gate_denies_when_not_starred(
    svc: PlatformLinkService, isolated_store: PlatformLinkStore
) -> None:
    isolated_store.save_client_token("nsc_nostar")
    svc.client = _FixedClient(
        me_payload={
            "verification_state": "NotStarred",
            "entitlement_state": "NotStarred",
            "github_login": "octocat",
        }
    )  # type: ignore[assignment]
    with pytest.raises(PlatformAuthorizationRequiredError) as excinfo:
        svc.require_download_authorization()
    assert excinfo.value.status.state is VerificationState.NOT_STARRED


def test_gate_denies_when_token_rejected(
    svc: PlatformLinkService, isolated_store: PlatformLinkStore
) -> None:
    isolated_store.save_client_token("nsc_expired")
    svc.client = _FixedClient(error=PlatformTokenRejectedError("rejected"))  # type: ignore[assignment]
    with pytest.raises(PlatformAuthorizationRequiredError) as excinfo:
        svc.require_download_authorization()
    assert excinfo.value.status.state is VerificationState.VERIFICATION_EXPIRED
    # The rejected token must be cleared locally.
    assert isolated_store.client_token() is None


def test_gate_offline_grace_within_ttl(
    svc: PlatformLinkService, isolated_store: PlatformLinkStore
) -> None:
    isolated_store.save_client_token("nsc_cached")
    svc.client = _FixedClient(
        me_payload={
            "verification_state": "VerifiedStarred",
            "entitlement_state": "VerifiedStarred",
            "github_login": "octocat",
        }
    )  # type: ignore[assignment]
    svc.require_download_authorization()  # primes the cache as STARRED_VERIFIED

    # Platform goes dark, but the cache is fresh -> offline grace applies.
    svc.client = _FixedClient(error=PlatformUnavailableError("dark"))  # type: ignore[assignment]
    status = svc.require_download_authorization()
    assert status.state is VerificationState.STARRED_VERIFIED


def test_gate_offline_stale_cache_denies(
    svc: PlatformLinkService, isolated_store: PlatformLinkStore
) -> None:
    isolated_store.save_client_token("nsc_stale")
    svc.client = _FixedClient(
        me_payload={
            "verification_state": "VerifiedStarred",
            "entitlement_state": "VerifiedStarred",
            "github_login": "octocat",
        }
    )  # type: ignore[assignment]
    svc.require_download_authorization()

    # Age the cache beyond the TTL.
    cached = isolated_store.load_status() or {}
    cached["checked_at"] = time.time() - 25 * 3600
    isolated_store.save_status(cached)

    svc.client = _FixedClient(error=PlatformUnavailableError("dark"))  # type: ignore[assignment]
    with pytest.raises(PlatformAuthorizationRequiredError) as excinfo:
        svc.require_download_authorization()
    assert excinfo.value.status.state is VerificationState.VERIFICATION_UNAVAILABLE


def test_gate_never_confuses_unavailable_with_not_starred(
    svc: PlatformLinkService, isolated_store: PlatformLinkStore
) -> None:
    isolated_store.save_client_token("nsc_dark")
    svc.client = _FixedClient(error=PlatformUnavailableError("boom"))  # type: ignore[assignment]
    with pytest.raises(PlatformAuthorizationRequiredError) as excinfo:
        svc.require_download_authorization()
    assert excinfo.value.status.state is not VerificationState.NOT_STARRED


# ---------------------------------------------------------------------------
# Connect flow end-to-end against a mock platform
# ---------------------------------------------------------------------------


class _MockPlatformHandler(BaseHTTPRequestHandler):
    """Minimal faithful mock of the NSE-Platform desktop-session endpoints."""

    server: _MockServer  # type: ignore[assignment]
    next_me: ClassVar[dict] = _ME_PAYLOAD

    def log_message(self, *args: object) -> None:
        return None

    def _json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length) or b"{}") if length else {}
        if self.path == "/api/desktop/session/start":
            session_id = str(uuid.uuid4())
            self._json(
                {
                    "session_id": session_id,
                    "session_secret": "d" * 64,
                    "authorize_url": "https://github.com/login/oauth/authorize?state=x",
                    "expires_in": 600,
                    "poll_interval": 3,
                }
            )
            return
        if self.path == "/api/desktop/session/poll":
            session_id = body.get("session_id", "")
            if body.get("session_secret") != "d" * 64:
                self._json({"error": "unauthorized"}, status=401)
                return
            state = _MockPlatformHandler.poll_state.get(session_id, "complete")
            if state == "pending":
                self._json({"status": "pending", "poll_interval": 3})
            elif state == "expired":
                self._json({"status": "expired"})
            else:
                self._json(
                    {
                        "status": "complete",
                        "client_token": "nsc_mocktoken123",
                        "verification_state": "VerifiedStarred",
                        "entitlement_state": "VerifiedStarred",
                        "repository": "Opselon/NexusTradingForexBot",
                    }
                )
            return
        self._json({"error": "not found"}, status=404)

    def do_GET(self) -> None:
        if self.path == "/api/client/me":
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Bearer nsc_") or auth.endswith("bad"):
                self._json({"error": "unauthorized"}, status=401)
                return
            self._json(dict(_MockPlatformHandler.next_me))
            return
        self._json({"error": "not found"}, status=404)

    poll_state: ClassVar[dict[str, str]] = {}


def test_connect_flow_end_to_end(isolated_store: PlatformLinkStore) -> None:
    client = _client_for(_MockPlatformHandler)
    config = PlatformLinkConfig(base_url=client.config.base_url)
    svc = PlatformLinkService(config=config, store=isolated_store, client=client)

    started = svc.start_connect()
    assert started.state is VerificationState.AUTHENTICATION_PENDING
    # The URL handed to the browser is the official GitHub authorize entry.
    assert started.verification_url.startswith("https://github.com/login")

    # User has not approved yet.
    session = isolated_store.load_desktop_session()
    assert session is not None
    _MockPlatformHandler.poll_state = {session[0]: "pending"}
    pending = svc.poll_once()
    assert pending.state is VerificationState.AUTHENTICATION_PENDING

    # GitHub authorization completed server-side.
    _MockPlatformHandler.poll_state = {session[0]: "complete"}
    done = svc.poll_once()
    assert done.state is VerificationState.STARRED_VERIFIED
    assert done.github_login == "octocat"
    # Session secrets cleared, token stored.
    assert isolated_store.load_desktop_session() is None
    assert isolated_store.client_token() == "nsc_mocktoken123"

    # Fresh reverify keeps the verified state.
    again = svc.reverify()
    assert again.state is VerificationState.STARRED_VERIFIED

    # Disconnect clears everything.
    out = svc.disconnect()
    assert out.state is VerificationState.NOT_CONNECTED
    assert isolated_store.client_token() is None
    client._server.shutdown()  # type: ignore[attr-defined]


def test_expired_session_returns_not_connected(isolated_store: PlatformLinkStore) -> None:
    client = _client_for(_MockPlatformHandler)
    svc = PlatformLinkService(config=client.config, store=isolated_store, client=client)
    svc.start_connect()
    session = isolated_store.load_desktop_session()
    assert session is not None
    _MockPlatformHandler.poll_state = {session[0]: "expired"}
    out = svc.poll_once()
    assert out.state is VerificationState.NOT_CONNECTED
    assert "expired" in (out.reason or "").lower()
    client._server.shutdown()  # type: ignore[attr-defined]


# ---------------------------------------------------------------------------
# Choke-point enforcement: FirstRunCoordinator.download_official
# ---------------------------------------------------------------------------


def test_download_official_refuses_without_verification(
    isolated_store: PlatformLinkStore, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from nexus_scalp.model_provisioning import FirstRunCoordinator
    from nexus_scalp.platform_link import reset_platform_link_service

    reset_platform_link_service()
    monkeypatch.setenv("NSE_USER_DATA_DIR", str(tmp_path))
    coord = FirstRunCoordinator()
    with pytest.raises(PlatformAuthorizationRequiredError):
        coord.download_official()
    reset_platform_link_service()


def test_link_status_roundtrip_from_cache() -> None:
    payload = {
        "state": "STARRED_VERIFIED",
        "github_login": "octocat",
        "checked_at": 123.0,
    }
    status = LinkStatus.from_cache(payload)
    assert status.state is VerificationState.STARRED_VERIFIED
    assert status.github_login == "octocat"
    # Corrupt payload degrades to NOT_CONNECTED, never crashes.
    broken = LinkStatus.from_cache({"state": "GARBAGE"})
    assert broken.state is VerificationState.NOT_CONNECTED


# ---------------------------------------------------------------------------
# Spec-alignment upgrades (2026-11 platform API review):
# §1 universal error envelope · §3.3 401-restart · §5 invariant-4 reason
# branching · §6.1 model.benchmark surfacing · §8 installation_mismatch.
# ---------------------------------------------------------------------------


class _Handler403Envelope(BaseHTTPRequestHandler):
    """403 with the FULL universal error envelope (reason field present)."""

    reason: ClassVar[str] = "LeaseExpired"

    def do_GET(self) -> None:
        body = json.dumps(
            {
                "error": {
                    "code": "forbidden",
                    "message": f"forbidden: {self.reason}",
                    "reason": self.reason,
                }
            }
        ).encode()
        self.send_response(403)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return None


class _Handler403MessageOnly(BaseHTTPRequestHandler):
    """403 carrying ONLY the message suffix ('forbidden: Revoked')."""

    def do_GET(self) -> None:
        body = json.dumps(
            {"error": {"code": "forbidden", "message": "forbidden: Revoked"}}
        ).encode()
        self.send_response(403)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: object) -> None:
        return None


def test_403_maps_to_forbidden_with_envelope_reason() -> None:
    from nexus_scalp.platform_link.client import PlatformForbiddenError

    client = _client_for(_Handler403Envelope)
    try:
        with pytest.raises(PlatformForbiddenError) as excinfo:
            client.client_me("nsc_test")
    finally:
        client._server.shutdown()  # type: ignore[attr-defined]
    assert excinfo.value.reason == "LeaseExpired"


def test_403_reason_falls_back_to_message_suffix() -> None:
    from nexus_scalp.platform_link.client import PlatformForbiddenError

    client = _client_for(_Handler403MessageOnly)
    try:
        with pytest.raises(PlatformForbiddenError) as excinfo:
            client.client_me("nsc_test")
    finally:
        client._server.shutdown()  # type: ignore[attr-defined]
    assert excinfo.value.reason == "Revoked"


def test_403_revoked_maps_to_not_starred_and_keeps_token(
    svc: PlatformLinkService, isolated_store: PlatformLinkStore
) -> None:
    """§5: Revoked denies immediately, but the token itself stays valid."""
    from nexus_scalp.platform_link.client import PlatformForbiddenError

    isolated_store.save_client_token("nsc_still_valid")
    svc.client = _FixedClient(error=PlatformForbiddenError("forbidden: Revoked", reason="Revoked"))  # type: ignore[assignment]
    status = svc.reverify()
    assert status.state is VerificationState.NOT_STARRED
    assert isolated_store.client_token() == "nsc_still_valid", "403 must never evict the token"


def test_403_lease_expired_maps_to_pending_not_expired(
    svc: PlatformLinkService, isolated_store: PlatformLinkStore
) -> None:
    """LeaseExpired ≠ expired token: re-verify, do NOT force a reconnect."""
    from nexus_scalp.platform_link.client import PlatformForbiddenError

    isolated_store.save_client_token("nsc_valid")
    svc.client = _FixedClient(
        error=PlatformForbiddenError("forbidden: LeaseExpired", reason="LeaseExpired")
    )  # type: ignore[assignment]
    status = svc.reverify()
    assert status.state is VerificationState.VERIFICATION_PENDING
    assert isolated_store.client_token() == "nsc_valid"


def test_model_benchmark_cached_from_me_payload(
    svc: PlatformLinkService, isolated_store: PlatformLinkStore
) -> None:
    """§6.1: model.version + benchmark{win_rate, max_drawdown} are cached for UI."""
    isolated_store.save_client_token("nsc_good")
    svc.client = _FixedClient(
        me_payload={
            "verification_state": "VerifiedStarred",
            "entitlement_state": "VerifiedStarred",
            "github_login": "octocat",
            "model": {
                "available": True,
                "id": "2d3e4f50",
                "version": "9.0.14",
                "dimension": 70,
                "sha256": "9f86d081",
                "benchmark": {"win_rate": 0.58, "max_drawdown": 0.07},
            },
        }
    )  # type: ignore[assignment]
    svc.reverify()
    info = svc.model_info()
    assert info["version"] == "9.0.14"
    assert info["win_rate"] == pytest.approx(0.58)
    assert info["max_drawdown"] == pytest.approx(0.07)


def test_session_poll_interval_persists_and_clears(isolated_store: PlatformLinkStore) -> None:
    """§3.1: the server's poll cadence is honored per-flow, then cleared."""
    assert isolated_store.load_session_poll_interval() is None
    isolated_store.save_desktop_session("sid", "secret", poll_interval=3.0)
    assert isolated_store.load_session_poll_interval() == 3.0
    isolated_store.clear_desktop_session()
    assert isolated_store.load_session_poll_interval() is None


def test_session_poll_interval_out_of_bounds_is_ignored(
    isolated_store: PlatformLinkStore,
) -> None:
    isolated_store.save_desktop_session("sid", "secret", poll_interval=500.0)
    assert isolated_store.load_session_poll_interval() is None


def test_status_panel_shows_benchmark_when_known(isolated_store: PlatformLinkStore) -> None:
    """The unlocked panel surfaces WHAT the star unlocks (display-only truth)."""
    from nexus_scalp.cli.platform_commands import render_status_panel

    class _SvcWithBenchmark:
        def token_status(self) -> None:
            return None

        def model_info(self) -> dict:
            return {"version": "9.0.14", "win_rate": 0.58, "max_drawdown": 0.07}

    panel = render_status_panel(
        LinkStatus(state=VerificationState.STARRED_VERIFIED, github_login="octocat"),
        _SvcWithBenchmark(),  # type: ignore[arg-type]
    )
    text = str(panel.renderable)
    assert "Official model 9.0.14" in text
    assert "win rate 58%" in text
    assert "max drawdown 7%" in text


def test_status_panel_hides_benchmark_when_unknown(svc: PlatformLinkService) -> None:
    """The panel NEVER invents numbers (no cached model info → no line)."""
    from nexus_scalp.cli.platform_commands import render_status_panel

    panel = render_status_panel(
        LinkStatus(state=VerificationState.STARRED_VERIFIED, github_login="octocat"), svc
    )
    text = str(panel.renderable)
    assert "Official model 9." not in text, "no invented version"
    assert "win rate" not in text, "no invented benchmark numbers"


# ---------------------------------------------------------------------------
# Download transport: §5 invariant 4 + §6.3 denial branching
# ---------------------------------------------------------------------------


def _http_get_against(handler_cls: type[BaseHTTPRequestHandler], monkeypatch, tmp_path):
    from nexus_scalp.model_provisioning import official_transport
    from nexus_scalp.model_provisioning.official import OfficialBundleError

    # official_transport enforces HTTPS-only targets; the mock server is local
    # HTTP — neutralize the URL guard for the in-process mock (the guard is
    # re-verified for real downloads by production code paths).
    monkeypatch.setattr(official_transport, "https_url", lambda _url: None)
    server = _MockServer(handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    dest = tmp_path / "bundle.bin"
    try:
        with pytest.raises(OfficialBundleError) as excinfo:
            official_transport.http_get(
                f"{server.url}/download", dest, timeout=10.0, max_bytes=1024
            )
    finally:
        server.shutdown()
    assert not dest.exists(), "failed downloads must not leave partial bytes"
    return excinfo.value


def test_transport_403_maps_to_lease_denied(monkeypatch, tmp_path) -> None:
    error = _http_get_against(_Handler403Envelope, monkeypatch, tmp_path)
    assert error.code == "LEASE_DENIED"
    assert "LeaseExpired" in str(error)


def test_transport_401_maps_to_authorization_required(monkeypatch, tmp_path) -> None:
    error = _http_get_against(_Handler401, monkeypatch, tmp_path)
    assert error.code == "AUTHORIZATION_REQUIRED"


def test_download_gate_maps_lease_denial_to_star_state(
    svc: PlatformLinkService,
    isolated_store: PlatformLinkStore,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    """A mid-download LEASE_DENIED surfaces as the gate's own denial shape."""
    from nexus_scalp.model_provisioning import service as prov_service
    from nexus_scalp.model_provisioning.official import OfficialBundleError

    isolated_store.save_client_token("nsc_good")
    svc.client = _FixedClient(
        me_payload={
            "verification_state": "VerifiedStarred",
            "entitlement_state": "VerifiedStarred",
        }
    )  # type: ignore[assignment]

    class _ExplodingSource:
        def download_and_verify(self, work_dir=None, *, cancel_event=None):
            raise OfficialBundleError("LEASE_DENIED", "delivery denied: Revoked")

    coordinator = prov_service.FirstRunCoordinator(official=_ExplodingSource())  # type: ignore[arg-type]
    monkeypatch.setattr(prov_service, "write_provisioner_state", lambda *a, **k: None)
    # download_official resolves the gate lazily via the platform_link package
    # getter — route it to THIS test's isolated service.
    import nexus_scalp.platform_link as pl_pkg

    monkeypatch.setattr(pl_pkg, "get_platform_link_service", lambda: svc)
    with pytest.raises(PlatformAuthorizationRequiredError) as excinfo:
        coordinator.download_official(work_dir=tmp_path)
    assert excinfo.value.status.state is VerificationState.NOT_STARRED
