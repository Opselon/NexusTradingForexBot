"""Diagnostics pipeline + CLI platform flow tests.

Covers:
- consent: ask-once persistence, non-interactive never prompts, enable/disable
- collector: WARNING+ filter, credential redaction, bounded buffer
- shipper: durable queue, disabled = no-op, delivery against a mock endpoint
- token_status masking
- resilient await_verification (transient platform errors keep polling)
"""

from __future__ import annotations

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import ClassVar

import pytest

from nexus_scalp.diagnostics import consent as consent_mod
from nexus_scalp.diagnostics.collector import (
    DiagnosticCollector,
    DiagnosticLogHandler,
    redact_message,
)
from nexus_scalp.diagnostics.shipper import DiagnosticsShipper
from nexus_scalp.platform_link import PlatformLinkService, VerificationState
from nexus_scalp.platform_link.config import PlatformLinkConfig
from nexus_scalp.platform_link.service import LinkStatus


@pytest.fixture()
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    from nexus_scalp.settings.secret_store import SecureSecretStore
    from nexus_scalp.settings.service import SettingsDatabase, SettingsService

    monkeypatch.setenv("NSE_USER_DATA_DIR", str(tmp_path))
    return SettingsService(
        db=SettingsDatabase(db_path=tmp_path / "settings.db"),
        secret_store=SecureSecretStore(root=tmp_path),
    )


# ---------------------------------------------------------------------------
# Consent
# ---------------------------------------------------------------------------


def test_consent_defaults_empty_then_persists(settings) -> None:
    assert consent_mod.consent_state(settings) == ""
    assert consent_mod.is_enabled(settings) is False
    consent_mod.set_consent(True, settings)
    assert consent_mod.consent_state(settings) == "on"
    consent_mod.set_consent(False, settings)
    assert consent_mod.consent_state(settings) == "off"


def test_consent_prompt_asked_once(settings) -> None:
    answers = iter(["y"])
    enabled = consent_mod.prompt_for_consent(
        ask=lambda q: next(answers), settings=settings, non_interactive=False
    )
    assert enabled is True
    # Second call never asks again (stored decision wins).
    enabled_again = consent_mod.prompt_for_consent(
        ask=lambda q: pytest.fail("must not ask twice"), settings=settings, non_interactive=False
    )
    assert enabled_again is True


def test_consent_non_interactive_never_prompts(settings) -> None:
    enabled = consent_mod.prompt_for_consent(
        ask=lambda q: pytest.fail("must not prompt"), settings=settings, non_interactive=True
    )
    assert enabled is False
    assert consent_mod.consent_state(settings) == ""  # decision NOT recorded


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------


def test_collector_filters_and_redacts() -> None:
    c = DiagnosticCollector(source="test")
    red = redact_message("failed with ghp_AAAABBBBCCCCDDDDEEEEFFFFGGGGHHHH")
    assert "ghp_AAAA[REDACTED]" in red and "CCCC" not in red
    c.add("WARNING", "mod", "rate limited near=x")
    c.add("INFO", "mod", "this must be dropped")
    c.add("ERROR", "mod", "boom token=sk-1234567890abcdefghij")
    buf = c.drain(10)
    assert len(buf) == 2
    assert buf[0]["level"] == "WARNING"
    assert "sk-1234567890" not in buf[1]["message"]
    assert "[REDACTED]" in buf[1]["message"]


def test_collector_bounded_buffer() -> None:
    c = DiagnosticCollector(source="test", max_buffer=5)
    for i in range(10):
        c.add("ERROR", "mod", f"m{i}")
    assert len(c) == 5
    recs = c.drain(10)
    assert recs[0]["message"] == "m5"  # oldest dropped


def test_log_handler_never_raises() -> None:
    c = DiagnosticCollector(source="test")
    handler = DiagnosticLogHandler(c)
    logger = logging.getLogger("diag.test")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        logger.warning("from the logging pipeline token=%s", "nsc_secrettoken12345678")
        recs = c.drain(10)
        assert len(recs) == 1
        assert "nsc_secrettoken" not in recs[0]["message"]
    finally:
        logger.removeHandler(handler)


# ---------------------------------------------------------------------------
# Shipper
# ---------------------------------------------------------------------------


class _MockIngest(ThreadingHTTPServer):
    received: ClassVar[list] = []
    fail_count: ClassVar[int] = 0

    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _MockIngestHandler)
        self.url = f"http://127.0.0.1:{self.server_port}"


class _MockIngestHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length)) if length else {}
        if _MockIngest.fail_count > 0:
            _MockIngest.fail_count -= 1
            self.send_response(503)
            self.end_headers()
            return
        _MockIngest.received.append(body)
        out = json.dumps({"received": len(body.get("records", [])), "id": "x"}).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(out)))
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args: object) -> None:
        return None


def _shipper(tmp_path: Path, settings, url: str) -> DiagnosticsShipper:
    config = PlatformLinkConfig(base_url=url, connect_timeout=2.0, read_timeout=2.0)
    from nexus_scalp.platform_link.client import PlatformClient
    from nexus_scalp.platform_link.store import PlatformLinkStore
    from nexus_scalp.settings.secret_store import SecureSecretStore

    store = PlatformLinkStore(
        settings=settings, secrets=SecureSecretStore(root=tmp_path)
    )
    store.installation_id()
    client = PlatformClient(config)
    return DiagnosticsShipper(
        source="test", queue_dir=tmp_path, client=client, settings=settings, store=store
    )


def test_shipper_disabled_is_noop(tmp_path: Path, settings) -> None:
    server = _MockIngest()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        shipper = _shipper(tmp_path, settings, server.url)
        shipper.collector.add("ERROR", "mod", "should not ship")
        assert shipper.flush_once() == 0
        assert _MockIngest.received == []
    finally:
        server.shutdown()


def test_shipper_delivers_and_persists_queue_on_failure(tmp_path: Path, settings) -> None:
    consent_mod.set_consent(True, settings)
    server = _MockIngest()
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        _MockIngest.fail_count = 1
        shipper = _shipper(tmp_path, settings, server.url)
        shipper.collector.add("ERROR", "mod", "first attempt fails")
        assert shipper.flush_once() == 0
        # Record persisted in the durable queue.
        assert shipper.queue_path.exists()

        _MockIngest.fail_count = 0
        sent = shipper.flush_once()
        assert sent >= 1
        assert len(_MockIngest.received) == 1
        payload = _MockIngest.received[0]
        assert payload["installation_id"]
        assert payload["source"] == "test"
        assert not shipper.queue_path.exists() or not shipper.queue_path.read_text().strip()
    finally:
        server.shutdown()


# ---------------------------------------------------------------------------
# Token status
# ---------------------------------------------------------------------------


def test_token_status_masked_or_none(tmp_path: Path, settings) -> None:
    from nexus_scalp.platform_link.store import PlatformLinkStore
    from nexus_scalp.settings.secret_store import SecureSecretStore

    store = PlatformLinkStore(settings=settings, secrets=SecureSecretStore(root=tmp_path))
    svc = PlatformLinkService(
        config=PlatformLinkConfig(base_url="http://127.0.0.1:1"),
        store=store,
    )
    assert svc.token_status() is None
    store.save_client_token("nsc_abcdefghijklmnopqrstuvwxyz012345")
    store.save_token_meta("2026-12-05T00:00:00+00:00")
    masked, expires = svc.token_status()
    assert masked.startswith("nsc_") and "…" in masked and masked.endswith("2345")
    assert expires.startswith("2026-12-05")


# ---------------------------------------------------------------------------
# Resilient await
# ---------------------------------------------------------------------------


def test_await_verification_survives_transient_errors(
    tmp_path: Path, settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from nexus_scalp.platform_link.client import PlatformUnavailableError
    from nexus_scalp.platform_link.store import PlatformLinkStore
    from nexus_scalp.settings.secret_store import SecureSecretStore

    store = PlatformLinkStore(settings=settings, secrets=SecureSecretStore(root=tmp_path))
    store.save_desktop_session("sess-1", "secret-1")

    class FlakyClient:
        def __init__(self) -> None:
            self.calls = 0

        def start_desktop_session(self, installation_id):  # pragma: no cover
            raise AssertionError

        def poll_desktop_session(self, session_id, session_secret):
            self.calls += 1
            if self.calls <= 2:
                raise PlatformUnavailableError("flaky")
            return {"status": "complete", "client_token": "nsc_ok1234567890", "expires_in_days": 30}

        def client_me(self, token):
            return {
                "verification_state": "VerifiedStarred",
                "entitlement_state": "VerifiedStarred",
                "github_login": "octocat",
                "repository": "Opselon/NexusTradingForexBot",
            }

    svc = PlatformLinkService(
        config=PlatformLinkConfig(base_url="http://127.0.0.1:1", poll_interval=0.01),
        store=store,
        client=FlakyClient(),  # type: ignore[arg-type]
    )
    ticks: list[LinkStatus] = []
    final = svc.await_verification(timeout=10.0, on_tick=ticks.append)
    assert final.state is VerificationState.STARRED_VERIFIED
    # The two transient failures kept the loop polling (they surfaced as
    # "still waiting" states, never as a terminal NOT_STARRED/NOT_CONNECTED).
    assert all(
        t.state
        in (
            VerificationState.AUTHENTICATION_PENDING,
            VerificationState.VERIFICATION_UNAVAILABLE,
            VerificationState.STARRED_VERIFIED,
        )
        for t in ticks
    )


def test_link_status_connected_property() -> None:
    assert LinkStatus(state=VerificationState.NOT_CONNECTED).state.connected is False
    assert LinkStatus(state=VerificationState.VERIFICATION_PENDING).state.connected is True
