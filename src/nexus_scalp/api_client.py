"""Nexus API client — lightweight typed Python client over the /api/v1 platform.

CONTRACT RULE: consumes the SAME HTTP contracts as every external client (no
business logic here). All methods return the raw parsed envelope
``{"data": ..., "meta": {...}}`` or raise :class:`NexusApiError` carrying the
standard v1 error object (code/message/details/request_id/retryable).

Usage:
    from nexus_scalp.api_client import NexusApiClient

    client = NexusApiClient("http://127.0.0.1:8080")
    print(client.system_status()["data"])
    print(client.decisions_latest()["data"])

    # raw path access:
    print(client.get("/api/v1/system/version")["data"])

USED BY: cli/api_commands.py (the ``nexus api`` group), developer scripts,
external automations (import path identical when packaged).
"""

from __future__ import annotations

from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8080"
API_PREFIX = "/api/v1"
DEFAULT_TIMEOUT = 10.0


class NexusApiError(RuntimeError):
    """Standard v1 error envelope raised as an exception."""

    def __init__(
        self,
        code: str,
        message: str,
        request_id: str,
        retryable: bool,
        status: int,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"[{code}] {message} (request_id={request_id})")
        self.code = code
        self.message = message
        self.request_id = request_id
        self.retryable = retryable
        self.status = status
        self.details = details or {}


class NexusApiClient:
    """Typed convenience wrapper over the versioned /api/v1 surface."""

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        headers: dict[str, str] | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._headers = dict(headers or {})

    # ------------------------------------------------------------------
    # core transport
    # ------------------------------------------------------------------

    def get(self, path: str, *, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """GET a v1 path (with or without the /api/v1 prefix). Returns the envelope."""
        return self._request("GET", self._url(path), params=params)

    def post(
        self,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """POST a v1 path. Returns the envelope (error mapped to NexusApiError)."""
        headers = dict(self._headers)
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        return self._request("POST", self._url(path), json_body=json_body, extra_headers=headers)

    def _url(self, path: str) -> str:
        if path.startswith("http://") or path.startswith("https://"):
            return path
        if not path.startswith("/"):
            path = "/" + path
        if not path.startswith(API_PREFIX):
            path = API_PREFIX + path
        return self._base + path

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        try:
            resp = httpx.request(
                method,
                url,
                params=params,
                json=json_body,
                timeout=self._timeout,
                headers={**self._headers, **(extra_headers or {})},
            )
        except httpx.HTTPError as exc:
            raise NexusApiError(
                "DEPENDENCY_UNAVAILABLE",
                f"cannot reach Nexus API at {self._base}",
                request_id="req_client",
                retryable=True,
                status=0,
                details={"reason": type(exc).__name__},
            ) from exc
        try:
            body = resp.json()
        except ValueError:
            body = {}
        if resp.status_code >= 400:
            err = body.get("error", {}) if isinstance(body, dict) else {}
            raise NexusApiError(
                code=str(err.get("code", "INTERNAL_ERROR")),
                message=str(err.get("message", resp.text[:200])),
                request_id=str(err.get("request_id", "req_unknown")),
                retryable=bool(err.get("retryable", resp.status_code >= 500)),
                status=resp.status_code,
                details=err.get("details") or {},
            )
        return body

    # ------------------------------------------------------------------
    # typed domain helpers (thin: same contracts, zero logic)
    # ------------------------------------------------------------------

    def system_status(self) -> dict[str, Any]:
        return self.get("/api/v1/system/status")

    def system_health(self) -> dict[str, Any]:
        return self.get("/api/v1/system/health")

    def system_version(self) -> dict[str, Any]:
        return self.get("/api/v1/system/version")

    def capabilities(self) -> dict[str, Any]:
        return self.get("/api/v1/system/capabilities")

    def runtime_mode(self) -> dict[str, Any]:
        return self.get("/api/v1/runtime/mode")

    def market_quote(self, symbol: str | None = None) -> dict[str, Any]:
        return self.get("/api/v1/market/quote", params={"symbol": symbol} if symbol else None)

    def signals_latest(self) -> dict[str, Any]:
        return self.get("/api/v1/signals/latest")

    def signals_history(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        symbol: str | None = None,
        action: str | None = None,
    ) -> dict[str, Any]:
        return self.get(
            "/api/v1/signals/history",
            params={
                "page": page,
                "page_size": page_size,
                "symbol": symbol,
                "action": action,
            },
        )

    def decisions_latest(self) -> dict[str, Any]:
        return self.get("/api/v1/decisions/latest")

    def decisions(
        self,
        *,
        page: int = 1,
        page_size: int = 50,
        symbol: str | None = None,
        action: str | None = None,
    ) -> dict[str, Any]:
        return self.get(
            "/api/v1/decisions",
            params={
                "page": page,
                "page_size": page_size,
                "symbol": symbol,
                "action": action,
            },
        )

    def decision_detail(self, decision_id: str) -> dict[str, Any]:
        return self.get(f"/api/v1/decisions/{decision_id}")

    def decision_gates(self, decision_id: str) -> dict[str, Any]:
        return self.get(f"/api/v1/decisions/{decision_id}/gates")

    def positions(self) -> dict[str, Any]:
        return self.get("/api/v1/positions")

    def risk_status(self) -> dict[str, Any]:
        return self.get("/api/v1/risk/status")

    def execution_status(self) -> dict[str, Any]:
        return self.get("/api/v1/execution/status")

    def model_status(self) -> dict[str, Any]:
        return self.get("/api/v1/model/status")

    def model_identity(self) -> dict[str, Any]:
        return self.get("/api/v1/model/identity")

    def features_contract(self) -> dict[str, Any]:
        return self.get("/api/v1/features/contract")

    def research_status(self) -> dict[str, Any]:
        return self.get("/api/v1/research/status")

    def shadow_status(self) -> dict[str, Any]:
        return self.get("/api/v1/shadow/status")

    def incidents(
        self, *, page: int = 1, page_size: int = 50, severity: str | None = None
    ) -> dict[str, Any]:
        return self.get(
            "/api/v1/incidents",
            params={
                "page": page,
                "page_size": page_size,
                "severity": severity,
            },
        )

    def database_status(self) -> dict[str, Any]:
        return self.get("/api/v1/database/status")

    def observability_metrics(self) -> dict[str, Any]:
        return self.get("/api/v1/observability/metrics")

    def run_diagnostics(self, *, idempotency_key: str | None = None) -> dict[str, Any]:
        return self.post("/api/v1/system/diagnostics/run", idempotency_key=idempotency_key)
