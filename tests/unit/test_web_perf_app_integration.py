"""The performance middleware must be part of the real app, in both modes.

The unit tests prove the middleware's behaviour; this proves the wiring. A
one-line regression here (someone reordering ``create_app``) would silently
remove ETag/gzip from production while every other test stayed green.
"""

from __future__ import annotations

import pytest

from nexus_scalp.web.performance import PerfConfig, PerfMiddleware


@pytest.fixture()
def app_factory(monkeypatch, tmp_path):
    monkeypatch.setenv("NSE_WEB_AUTH_TOKEN", "test-token-not-a-secret")
    monkeypatch.setenv("NEXUS_ARTIFACTS_DIR", str(tmp_path))
    from nexus_scalp.web.server import create_app

    return create_app


def _middleware_classes(app) -> list:
    return [entry.cls for entry in app.user_middleware]


def test_the_perf_middleware_is_installed_by_default(app_factory):
    app = app_factory(None)
    assert PerfMiddleware in _middleware_classes(app)


def test_the_perf_middleware_can_be_switched_off(app_factory, monkeypatch):
    monkeypatch.setenv("NEXUS_WEB_PERF", "0")
    app = app_factory(None)
    # add_middleware is still called with the config; the config is what
    # disables it, so the class stays in the stack but must be inert.
    configs = [
        entry.kwargs.get("config") for entry in app.user_middleware if entry.cls is PerfMiddleware
    ]
    assert all(isinstance(config, PerfConfig) and not config.enabled for config in configs)


def test_health_response_carries_an_etag_and_revalidates(app_factory):
    """A 200 through the REAL middleware stack is cached and revalidated.

    The probe route is mounted on the production app after ``create_app``, so
    the response still travels through the installed middleware stack — the
    wiring under test. It deliberately does NOT assert ``/health`` == 200: that
    endpoint reports the MACHINE's verdict (missing market data, no Telegram
    token, unversioned local DB) and a 503 is not a cacheable status, so using
    it made this etag contract fail for environmental reasons instead of a
    middleware regression.
    """
    from fastapi.testclient import TestClient

    app = app_factory(None)

    @app.get("/__perf_probe")
    def _probe() -> dict[str, bool]:  # pragma: no cover - trivially true
        return {"ok": True}

    client = TestClient(app)
    client.headers.update({"Authorization": "Bearer test-token-not-a-secret"})
    first = client.get("/__perf_probe")
    assert first.status_code == 200
    etag = first.headers.get("etag")
    assert etag and etag.startswith('"')

    second = client.get("/__perf_probe", headers={"If-None-Match": etag})
    assert second.status_code == 304
    assert second.content == b""
