"""The report renderer must load through the real authenticated web app."""

from fastapi.testclient import TestClient

from nexus_scalp.web.server import create_app


def test_report_script_public_but_research_api_stays_protected(monkeypatch):
    monkeypatch.delenv("NSE_WEB_AUTH_DISABLE", raising=False)
    monkeypatch.setenv("NSE_WEB_AUTH_TOKEN", "backtest-fixture-token")
    client = TestClient(create_app(engine_ref=None))
    response = client.get("/backtest_report_ui.js")
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]
    assert "NSE_EMPIRICAL_REPLAY" in response.text
    assert client.get("/api/research/summary").status_code == 401
