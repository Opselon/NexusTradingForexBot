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
    settings = SettingsService(db=SettingsDatabase(db_path=tmp_path / "settings.db"), secret_store=SecureSecretStore(root=tmp_path))
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
    config = PlatformLinkConfig(
        base_url=server.url, connect_timeout=2.0, read_timeout=2.0
    )
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

    def poll_desktop_session(self, session_id: str, session_secret: str) -> dict:  # pragma: no cover
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


def test_gate_allows_when_verified(svc: PlatformLinkService, isolated_store: PlatformLinkStore) -> None:
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


def test_gate_denies_when_not_starred(svc: PlatformLinkService, isolated_store: PlatformLinkStore) -> None:
    isolated_store.save_client_token("nsc_nostar")
    svc.client = _FixedClient(
        me_payload={"verification_state": "NotStarred", "entitlement_state": "NotStarred", "github_login": "octocat"}
    )  # type: ignore[assignment]
    with pytest.raises(PlatformAuthorizationRequiredError) as excinfo:
        svc.require_download_authorization()
    assert excinfo.value.status.state is VerificationState.NOT_STARRED


def test_gate_denies_when_token_rejected(svc: PlatformLinkService, isolated_store: PlatformLinkStore) -> None:
    isolated_store.save_client_token("nsc_expired")
    svc.client = _FixedClient(error=PlatformTokenRejectedError("rejected"))  # type: ignore[assignment]
    with pytest.raises(PlatformAuthorizationRequiredError) as excinfo:
        svc.require_download_authorization()
    assert excinfo.value.status.state is VerificationState.VERIFICATION_EXPIRED
    # The rejected token must be cleared locally.
    assert isolated_store.client_token() is None


def test_gate_offline_grace_within_ttl(svc: PlatformLinkService, isolated_store: PlatformLinkStore) -> None:
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
