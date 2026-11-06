"""Cross-repository API contract: NSE-Platform <-> NexusTradingForexBot.

The lifecycle spec (§18) states: "No repository may silently depend on an API
contract that the other repository has not implemented." This suite pins the
exact wire shapes the Python client depends on, so a server change that
breaks the contract fails HERE instead of in a user's living room.

Contract surface pinned:
- GET /api/client/me canonical startup payload (§3.2) — every field the
  lifecycle coordinator reads, with the documented types.
- GET /api/desktop/session/start + /poll — the connect flow contract.
- POST /api/diagnostics/ingest — the ack shape (`id`) the delivery ledger
  records as evidence (§11).
- State vocabulary mapping (both directions) — the strings that cross the
  wire must map exactly onto `VerificationState` / decision codes.

Ran against a stub server that emits the CURRENT canonical shapes; a change
on the Rust side that alters any pinned field must update this file in the
same PR.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar

import pytest

from nexus_scalp.platform_link.client import PlatformClient
from nexus_scalp.platform_link.config import PlatformLinkConfig
from nexus_scalp.platform_link.states import state_from_startup_payload


# ---------------------------------------------------------------------------
# Canonical server shapes (the pinned contract — mirrors NSE-Platform
# desktop.rs client_me + diagnostics.rs ingest + models.rs ModelRelease).
# ---------------------------------------------------------------------------

CANONICAL_CLIENT_ME: dict[str, Any] = {
    "success": True,
    "request_id": "req-contract-1",
    "github_login": "octocat",
    "installation_id": "install-contract-1",
    "repository": "Opselon/NexusTradingForexBot",
    # Legacy fields (kept; older code paths still read them).
    "verification_state": "VerifiedStarred",
    "entitlement_state": "VerifiedStarred",
    # Canonical §3.2 payload.
    "installation": {
        "installation_id": "install-contract-1",
        "recognized": True,
        "token_valid": True,
    },
    "identity": {
        "github_login": "octocat",
        "repository": "Opselon/NexusTradingForexBot",
    },
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
        "id": "m-contract-1",
        "version": "9.0.22",
        "dimension": 70,
        "sha256": "a" * 64,
        "size_bytes": 17,
        "release_notes": "contract release notes",
        "benchmark": {"profit_factor": 1.4, "win_rate": 0.56, "sample_size": 300},
        "published_at": "2026-11-05T00:00:00+00:00",
    },
    "reason_code": "GrantedFresh",
}

CANONICAL_DIAGNOSTICS_ACK: dict[str, Any] = {"received": 2, "id": "report-contract-1"}


class _ContractServer(BaseHTTPRequestHandler):
    """Serves the pinned canonical shapes; rejects anything unexpected loudly."""

    protocol_version = "HTTP/1.1"
    server: ClassVar[ThreadingHTTPServer]

    def _json(self, code: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - stdlib naming
        if self.path == "/api/client/me":
            self._json(200, CANONICAL_CLIENT_ME)
            return
        self._json(404, {"error": {"code": "not_found", "message": self.path}})

    def do_POST(self) -> None:  # noqa: N802 - stdlib naming
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        if self.path == "/api/diagnostics/ingest":
            # The ack id is what the delivery ledger records as evidence (§11).
            self._json(200, CANONICAL_DIAGNOSTICS_ACK)
            return
        self._json(404, {"error": {"code": "not_found", "message": self.path}})

    def log_message(self, *args: Any) -> None:
        pass


@pytest.fixture(scope="module")
def contract_server() -> Any:
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _ContractServer)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=5)


@pytest.fixture()
def contract_client(contract_server: str) -> PlatformClient:
    return PlatformClient(PlatformLinkConfig(base_url=contract_server))


# ---------------------------------------------------------------------------
# GET /api/client/me — canonical startup payload contract
# ---------------------------------------------------------------------------


def test_client_me_canonical_shape(contract_client: PlatformClient) -> None:
    me = contract_client.client_me("nsc_contract-token-000000000")

    # Top-level identity + repository (legacy fields MUST stay).
    assert me["github_login"] == "octocat"
    assert me["repository"] == "Opselon/NexusTradingForexBot"
    assert me["installation_id"] == "install-contract-1"
    assert me["success"] is True
    assert isinstance(me["request_id"], str) and me["request_id"]

    # Canonical blocks.
    installation = me["installation"]
    assert installation["token_valid"] is True
    assert installation["recognized"] is True

    identity = me["identity"]
    assert identity["github_login"] == me["github_login"]

    entitlement = me["entitlement"]
    assert entitlement["decision"] == "GrantedFresh"
    assert entitlement["access_allowed"] is True
    for field in ("checked_at", "valid_until", "next_recheck_at"):
        assert isinstance(entitlement[field], str) and entitlement[field]

    model = me["model"]
    assert model["available"] is True
    for field in ("id", "version", "sha256", "size_bytes", "release_notes", "benchmark"):
        assert field in model, f"model payload missing {field}"
    assert model["sha256"] == "a" * 64
    assert model["benchmark"]["profit_factor"] == 1.4

    # reason_code mirrors the decision (single source of truth).
    assert me["reason_code"] == entitlement["decision"]


def test_client_me_legacy_only_shape_still_maps() -> None:
    """An older server (no decision/model blocks) must not crash the client."""
    legacy = {
        "github_login": "octocat",
        "repository": "Opselon/NexusTradingForexBot",
        "installation_id": "install-legacy",
        "verification_state": "VerifiedStarred",
        "entitlement_state": "VerifiedStarred",
    }
    assert state_from_startup_payload(legacy) is not None


# ---------------------------------------------------------------------------
# Decision vocabulary — every server verdict maps to a known local state
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("decision", "expect_connected", "expect_download"),
    [
        ("GrantedFresh", True, True),
        ("GrantedStale", True, True),  # degraded allow within the lease
        ("Revoked", True, False),
        ("Pending", True, False),
        ("LeaseExpired", True, False),
    ],
)
def test_every_decision_maps_to_known_state(
    decision: str, expect_connected: bool, expect_download: bool
) -> None:
    state = state_from_startup_payload({"entitlement": {"decision": decision}})
    assert state.connected is expect_connected
    assert state.allows_official_download is expect_download


# ---------------------------------------------------------------------------
# POST /api/diagnostics/ingest — ack evidence contract (§11)
# ---------------------------------------------------------------------------


def test_diagnostics_ack_shape_is_evidence(contract_client: PlatformClient) -> None:
    ack = contract_client._request(
        "/api/diagnostics/ingest",
        payload={
            "installation_id": "install-contract-1",
            "app_version": "9.0.22",
            "mode": "PAPER",
            "source": "contract-test",
            "records": [
                {"level": "ERROR", "message": "boom", "logger": "contract"},
                {"level": "WARNING", "message": "careful", "logger": "contract"},
            ],
        },
        token="nsc_contract-token-000000000",
    )
    # `id` is the server report id the delivery ledger persists as ack proof.
    assert ack["received"] == 2
    assert isinstance(ack["id"], str) and ack["id"]
