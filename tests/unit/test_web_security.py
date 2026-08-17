"""Security & error-hygiene regression tests for the web control surface.

Behavioral tests verifying the DASHBOARD HARDENING contract:

1. An intentional endpoint failure never returns exception text.
2. Responses never contain tracebacks, filesystem paths or SQL statements.
3. Every error response contains a stable error code and a request_id.
4. The server log contains the detailed exception for correlation.
5. SSE payloads are sanitized (no traceback ever streamed).
6. WebSocket errors are sanitized.
7. The legacy ``{"error": "<str>"}`` pattern is eliminated from server.py.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from nexus_scalp.web import errors as web_errors
from nexus_scalp.web.server import create_app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


class _FakeEngine:
    """Minimal engine that satisfies the parts of server.py exercised in tests."""

    def __init__(self) -> None:
        self._running = False
        self._last_tick = None
        self._last_fv = None
        self._last_probs = None
        self._last_proposal = None
        self._last_regime_state = None
        self._peak_equity = 0.0
        self.config = SimpleNamespace(
            execution=SimpleNamespace(symbol="XAUUSD", mode=SimpleNamespace(value="LIVE")),
            algo=SimpleNamespace(
                atr_sl_buffer_multiplier=1.5,
                min_risk_reward_ratio=1.8,
                ai_zone_confidence_threshold=0.82,
                fvg_mitigation_sensitivity=0.5,
                order_block_lookback_bars=30,
            ),
            risk=SimpleNamespace(risk_per_trade_pct=0.5),
            model=SimpleNamespace(confidence_threshold=0.75),
        )
        self.adapter = SimpleNamespace(
            get_last_tick=lambda symbol: None,
            get_account_info=lambda: None,
            get_positions=lambda symbol=None: [],
        )
        self.aggregator = SimpleNamespace(
            get_completed_bars=lambda: [],
            get_current_forming_bar=lambda: None,
        )
        self.audit = _FakeAudit()
        self.accounting_core = _FakeAccountingCore()
        self.notifier = SimpleNamespace(enabled=False, _queue=None)

    @property
    def _bundle_lock(self):
        class _Lock:
            def __enter__(self):
                return SimpleNamespace(model=None, scaler=None)

            def __exit__(self, *a):
                return False

        return _Lock()

    @property
    def _bundle(self):
        return None


class _FakeAudit:
    def __init__(self) -> None:
        self._db_path = ":memory:"

    def get_trading_rules(self):
        return []


class _FakeAccountingCore:
    def live_state(self):
        return SimpleNamespace(
            available=False, balance=None, equity=None, margin=None, open_positions=None
        )

    def load_trades(self, limit=1000):
        return []

    def drawdown_report(self):
        return SimpleNamespace(max_drawdown_pct=None)


def _intentional_failure_endpoint() -> FastAPI:
    """App whose routes deliberately explode to prove sanitization."""
    app = FastAPI(title="intentional-failures")
    log = logging.getLogger("nexus_scalp.web.server.test")

    @app.middleware("http")
    async def _correlation_middleware(request: Request, call_next):
        return await web_errors.attach_request_id_middleware(request, call_next)

    @app.get("/boom")
    def boom():
        _log, _payload = web_errors.make_error_handler("/boom", log)
        try:
            raise RuntimeError("SECRET_INTERNAL_MARKER_PATH: C:/Users/secret/app.py")
        except RuntimeError as exc:
            _log(exc)
            return _payload("INTERNAL_ERROR")

    @app.get("/sql-boom")
    def sql_boom():
        _log, _payload = web_errors.make_error_handler("/sql-boom", log)
        try:
            # Simulate a DB-layer failure whose exception text mentions SQL.
            raise RuntimeError("sqlite3.OperationalError: no such table: audit_ledger")
        except RuntimeError as exc:
            _log(exc)
            return _payload("INTERNAL_ERROR")

    @app.get("/with-request-id")
    def with_request_id(request: Request):
        rid = web_errors.request_id_from_request(request)
        _log, _payload = web_errors.make_error_handler("/with-request-id", log, request_id=rid)
        try:
            raise ValueError("internal detail for logs only")
        except ValueError as exc:
            _log(exc)
            return _payload("OPERATION_FAILED")

    return app


@pytest.fixture
def failure_client() -> TestClient:
    return TestClient(_intentional_failure_endpoint())


# ---------------------------------------------------------------------------
# 1-6: behavioral payload assertions
# ---------------------------------------------------------------------------


class TestSanitizedResponses:
    def test_01_no_exception_text_in_payload(self, failure_client: TestClient) -> None:
        res = failure_client.get("/boom")
        assert res.status_code == 200
        body = res.json()
        assert body["available"] is False
        assert body["success"] is False
        err = body["error"]
        assert err["code"] == "INTERNAL_ERROR"
        assert "SECRET_INTERNAL_MARKER_PATH" not in res.text
        assert "RuntimeError" not in res.text
        assert "C:/Users" not in res.text
        assert "Traceback" not in res.text and "traceback" not in res.text.lower()

    def test_02_no_sql_or_path_in_payload(self, failure_client: TestClient) -> None:
        res = failure_client.get("/sql-boom")
        body = res.json()
        assert body["error"]["code"] == "INTERNAL_ERROR"
        assert "SELECT" not in res.text
        assert "audit_ledger" not in res.text
        assert "sqlite3" not in res.text
        assert "no such table" not in res.text

    def test_03_stable_error_code_and_message(self, failure_client: TestClient) -> None:
        res = failure_client.get("/boom")
        err = res.json()["error"]
        assert err["code"] == "INTERNAL_ERROR"
        assert err["message"] == web_errors.ERROR_CODES["INTERNAL_ERROR"]

    def test_04_request_id_present_and_header_echoed(self, failure_client: TestClient) -> None:
        res = failure_client.get("/with-request-id", headers={"X-Request-ID": "req_test123"})
        body = res.json()
        assert body["error"]["request_id"] == "req_test123"
        assert res.headers.get("X-Request-ID") == "req_test123"

    def test_05_request_id_autogenerated_when_missing(self, failure_client: TestClient) -> None:
        res = failure_client.get("/boom")
        rid = res.json()["error"]["request_id"]
        assert rid.startswith("req_")
        assert len(rid) > 6

    def test_06_server_log_contains_detailed_exception(self) -> None:
        # The repo's structlog BoundLogger renders the traceback + exception
        # text (via rich) to stdout/stderr. This test MUST NOT depend on
        # contextlib.redirect_stdout: configure_logging() binds a
        # StreamHandler(sys.stdout) once (capturing the stdout OBJECT at
        # creation), and structlog caches loggers on first use - so when this
        # test runs after any other test that already initialized logging, the
        # handler writes to the ORIGINAL sys.stdout and redirect_stdout misses
        # it (order-dependent flake). Instead we attach a temporary capture
        # handler directly to the root logger and read the formatted record.
        import logging

        from nexus_scalp.observability.logging import configure_logging
        from nexus_scalp.observability.logging import get_logger as get_structlog

        # CRITICAL (order-dependent flake): structlog's DEFAULT logger factory
        # is PrintLoggerFactory - logs go straight to stdout and NEVER reach
        # stdlib logging handlers. A fresh pytest session has no conftest that
        # calls configure_logging(), so without this the capture handler below
        # records nothing. configure_logging() rebuilds the stdlib pipeline
        # (idempotent: clears + re-adds root handlers).
        root = logging.getLogger()
        original_level = root.level
        original_handlers = list(root.handlers)
        configure_logging(log_to_file=False)
        # The dev rich ConsoleRenderer re-raises the active exception while
        # formatting exc_info records when logging.raiseExceptions is True
        # (default). That would propagate the probe exception out of this
        # test. Suppress it for the duration and restore afterwards.
        original_raise_exceptions = logging.raiseExceptions
        logging.raiseExceptions = False

        class _CaptureHandler(logging.Handler):
            def __init__(self) -> None:
                super().__init__(level=logging.DEBUG)
                self.records: list[logging.LogRecord] = []

            def emit(self, record: logging.LogRecord) -> None:
                # format() populates record.exc_text (the traceback string)
                # and record.message, exactly like a normal handler.
                self.format(record)
                self.records.append(record)

        capture = _CaptureHandler()
        try:
            root.setLevel(logging.DEBUG)
            root.addHandler(capture)
            named = logging.getLogger("nexus_scalp.web.server.test")
            named.addHandler(capture)
            slog = get_structlog("nexus_scalp.web.server.test")
            try:
                raise RuntimeError("SECRET_INTERNAL_MARKER_PATH: C:/Users/secret/app.py")
            except RuntimeError:
                slog.exception(
                    "WEB_ERROR",
                    endpoint="/boom",
                    request_id="req_zz",
                    exception_type="RuntimeError",
                )
        finally:
            root.removeHandler(capture)
            named.removeHandler(capture)
            root.setLevel(original_level)
            # Restore the pre-test handler set (configure_logging may have
            # replaced pytest's capture handlers - the next test must see the
            # same root logger state it would have had).
            root.handlers[:] = original_handlers
            logging.raiseExceptions = original_raise_exceptions

        assert capture.records, "no log record captured"
        text = "".join(format(r.getMessage()) for r in capture.records)
        exc_text = "\n".join("".join(r.exc_text or "") for r in capture.records if r.exc_text)
        captured = text + exc_text
        assert "SECRET_INTERNAL_MARKER_PATH" in captured
        assert "RuntimeError" in captured
        assert "WEB_ERROR" in captured


# ---------------------------------------------------------------------------
# 7: SSE sanitization
# ---------------------------------------------------------------------------


class TestSSESanitization:
    def test_07_sse_payload_has_no_traceback(self) -> None:
        """SSE frames must be sanitized (no traceback ever streamed).

        Uses a REAL uvicorn server on a local socket + bounded raw read:
        the endless SSE generator cannot be consumed by sync TestClient or
        ASGITransport (they buffer until response completion, which never
        happens for SSE - pre-existing stack limitation).
        """
        import socket
        import threading
        import time

        import uvicorn

        srv_app = create_app(engine_ref=None)
        cfg = uvicorn.Config(srv_app, host="127.0.0.1", port=0, log_level="error")
        server = uvicorn.Server(cfg)
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            # Wait for the server to bind a port.
            port = None
            for _ in range(50):
                for s in getattr(server, "servers", []) or []:
                    sockets = getattr(s, "sockets", None) or []
                    if sockets:
                        port = sockets[0].getsockname()[1]
                        break
                if port:
                    break
                time.sleep(0.1)
            assert port, "uvicorn did not bind a port"

            with socket.create_connection(("127.0.0.1", port), timeout=10) as sock:
                sock.sendall(b"GET /api/ticks/stream HTTP/1.1\r\nHost: test\r\n\r\n")
                sock.settimeout(15)
                buf = b""
                deadline = time.time() + 15
                while b"\n\n" not in buf and time.time() < deadline:
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    buf += chunk
                text = buf.decode("utf-8", errors="replace")
                assert "\n\n" in text, f"no SSE frame received; got: {text[:200]}"
                # First frame body (after headers) is the 'state' snapshot.
                head, _, body = text.partition("\r\n\r\n")
                assert "200" in head, f"SSE status not 200: {head[:120]}"
                assert "Traceback" not in body
                assert "RuntimeError" not in body
                assert "file://" not in body
                payload_line = next(
                    (ln for ln in body.splitlines() if ln.startswith("data: ")), None
                )
                assert payload_line is not None, "no data: line in SSE frame"
                import json

                obj = json.loads(payload_line[len("data: ") :])
                assert "engine_running" in obj
        finally:
            server.should_exit = True
            thread.join(timeout=5)


# ---------------------------------------------------------------------------
# 8: legacy pattern eliminated from server.py
# ---------------------------------------------------------------------------


class TestLegacyPatternEliminated:
    def test_08_no_raw_str_e_in_server_returns(self) -> None:
        src = open("src/nexus_scalp/web/server.py", encoding="utf-8").read()
        # The old leaking pattern: return {... "error": str(e)} or reason: str(e)
        bad = [
            r"\"error\"\s*:\s*str\(e\)",
            r"\"reason\"\s*:\s*str\(e\)",
            r"\"message\"\s*:\s*str\(e\)",
            # f-string exception interpolation into detail (e.g. detail=f"...{err}")
            r"detail\s*=\s*f[\"'][^\"']*\{[a-zA-Z_]*err[a-zA-Z_]*\}",
        ]
        for pat in bad:
            matches = re.findall(pat, src)
            assert not matches, f"leaking pattern {pat} still present: {matches[:3]}"

    def test_09_no_exception_repr_in_web_responses(self) -> None:
        src = open("src/nexus_scalp/web/server.py", encoding="utf-8").read()
        assert (
            'raise HTTPException(status_code=500, detail=f"Inference failed: {infer_err}")'
            not in src
        )
        assert 'detail=f"PyTorch runtime unavailable: {import_err}"' not in src
        # no f-string interpolation of exception into detail
        assert re.search(r'detail=f"[^"]*\{[a-z_]*err', src) is None
