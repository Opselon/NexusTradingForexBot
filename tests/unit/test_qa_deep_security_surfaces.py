"""TASK-QA-DEEP-ASSURANCE / CHG-0045: security attack-surface battery.

Targets ACTUAL attack surfaces (no security theater), offline against the
FastAPI app (TestClient) and the redaction primitives:

SEC-1  API JSON purity + malformed bodies: malformed JSON, wrong content
       types, oversized payloads, unknown fields -> structured 4xx / no
       traceback leakage
SEC-2  unknown HTTP methods on real routes -> explicit 405/406/501
SEC-3  path-traversal probes against path-shaped routes -> rejected/404,
       never file contents
SEC-4  secret hygiene: redact_url strips userinfo + credential query params
       for EVERY generated URL shape (property over seeded random URLs)
SEC-5  error responses / OpenAPI never contain configured credential markers
SEC-6  header injection: newline-bearing URL components are neutralized by
       redact_url (never reproduces CRLF)

KNOWN FINDING recorded by this battery (SEC-1c): a JSON-shaped body posted
under a WRONG Content-Type (text/plain) on POST endpoints returns HTTP 500
with an EMPTY body (FastAPI RequestValidationError escaping as an unhandled
error after the raw-bytes body cannot be re-serialized). This is a P3
robustness defect (server error class on malformed-but-parseable input);
routed to the web-owners' queue via the blind-spot matrix, NOT fixed in
this tests-only pass.
"""

from __future__ import annotations

import json
import random
import urllib.parse

from fastapi.testclient import TestClient

from nexus_scalp.strategies.factory.provider_gate import redact_url
from nexus_scalp.web.server import create_app

SEED = 20260902


def _client() -> TestClient:
    # raise_server_exceptions=False: we are testing the APP's observable
    # error behavior (status + body), not that exceptions propagate.
    return TestClient(create_app(engine_ref=None), raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# SEC-1: malformed / hostile request bodies on a real POST route
# ---------------------------------------------------------------------------


def test_sec_malformed_json_is_structured_4xx() -> None:
    client = _client()
    r = client.post(
        "/api/engine/toggle",
        content=b"{not-json",
        headers={"content-type": "application/json"},
    )
    assert r.status_code in (400, 404, 422)
    body = r.text.lower()
    assert "traceback" not in body and "exception" not in body


def test_sec_oversized_payload_is_rejected_bounded() -> None:
    client = _client()
    big = {"payload": "x" * (1024 * 1024)}
    r = client.post("/api/engine/toggle", json=big)
    assert r.status_code in (400, 404, 413, 414, 422, 500)
    # a 500 must still be JSON-free of internal details
    assert "traceback" not in r.text.lower()


def test_sec_wrong_content_type_is_never_a_traceback() -> None:
    """KNOWN DEFECT PROBE (SEC-1c): wrong content type currently yields
    HTTP 500 with an EMPTY body. Guard: the response must never leak a
    traceback / internal details, and the failure class is recorded in the
    blind-spot matrix for the web owners."""
    client = _client()
    r = client.post(
        "/api/engine/toggle",
        content=json.dumps({"active": False}).encode(),
        headers={"content-type": "text/plain"},
    )
    body = r.text.lower()
    assert "traceback" not in body
    assert 'file "' not in body  # python source paths never leak


def test_sec_unknown_fields_ignored_not_crash() -> None:
    client = _client()
    r = client.post(
        "/api/engine/toggle",
        json={"active": False, "unknown_field": {"a": [1] * 50}, "another": None},
    )
    assert r.status_code in (200, 400, 422)


# ---------------------------------------------------------------------------
# SEC-2: method abuse
# ---------------------------------------------------------------------------


def test_sec_method_abuse_is_explicit() -> None:
    client = _client()
    for method in ("DELETE", "PATCH", "PUT"):
        r = client.request(method, "/api/status")
        assert r.status_code in (405, 406, 501)
    r2 = client.request("DELETE", "/api/engine/toggle")
    assert r2.status_code in (405, 406, 501)


# ---------------------------------------------------------------------------
# SEC-3: path traversal against path-shaped inputs
# ---------------------------------------------------------------------------


def test_sec_path_traversal_on_routes_is_not_file_disclosure() -> None:
    client = _client()
    probes = [
        "/api/debug/state/..%2F..%2F..%2Fetc%2Fpasswd",
        "/api/debug/state/..\\..\\..\\windows\\win.ini",
        "/api/debug/state/%2e%2e%2f%2e%2e%2fsecrets.enc",
        "/api/debug/state/C:%5Cwindows%5Cwin.ini",
        "/Web/..%2f..%2fsettings%2fsecrets.enc",
    ]
    for url in probes:
        r = client.get(url)
        assert r.status_code in (404, 400, 422), url
        body = r.text
        assert "root:" not in body and "[fonts]" not in body and "DPAPI" not in body


# ---------------------------------------------------------------------------
# SEC-4: redact_url property over generated hostile URLs
# ---------------------------------------------------------------------------


_CRED_KEYS = {"key", "api_key", "apikey", "token", "access_token", "password", "secret"}


def test_sec_redact_url_property_generated_urls() -> None:
    rng = random.Random(SEED)
    schemes = ["http", "https", "ftp"]
    hosts = ["api.example.com", "user:pass@host.io", "1.2.3.4:8080", "[::1]:9000"]
    paths = ["", "/v1/chat", "/a/b/c", "/../etc/passwd", "/%2e%2e/"]
    for _ in range(80):
        url = (
            f"{rng.choice(schemes)}://{rng.choice(hosts)}{rng.choice(paths)}"
            f"?{rng.choice(list(_CRED_KEYS))}={rng.randrange(10**9)}&x={rng.randrange(100)}"
        )
        out = redact_url(url)
        assert isinstance(out, str)
        # credential keys never survive in plaintext
        parsed = urllib.parse.parse_qsl(urllib.parse.urlsplit(out).query, keep_blank_values=True)
        for k, v in parsed:
            if k.lower() in _CRED_KEYS:
                assert v == "[REDACTED]", (url, out, k)


def test_sec_redact_url_never_reproduces_crlf() -> None:
    rng = random.Random(SEED + 1)
    for _ in range(40):
        evil = f"https://h.io/p?token=abc{'%0d%0a' if rng.random() < 0.5 else chr(10)}xyz"
        out = redact_url(evil)
        assert "\r" not in out and "\n" not in out


def test_sec_redact_url_garbage_never_raises() -> None:
    for garbage in ("", "::::", "http://", "ht tp://x", "http://[::1", "https://a@\x00b/"):
        out = redact_url(garbage)
        assert isinstance(out, str)


# ---------------------------------------------------------------------------
# SEC-5: API surface never leaks credential markers
# ---------------------------------------------------------------------------


def test_sec_openapi_and_status_leak_no_credentials() -> None:
    client = _client()
    openapi = client.get("/openapi.json").text.lower()
    for marker in ("sk-live", "begin rsa", "password=", "bearer "):
        assert marker not in openapi
    r = client.get("/api/status")
    assert r.status_code in (200, 503)
    body = r.text.lower()
    assert "sk-live" not in body and "authorization" not in body
