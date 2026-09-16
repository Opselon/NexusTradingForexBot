"""BUG-300: AI triage must always deliver a summary — with reachability gating.

Contract pins for src/nexus_scalp/observability/ci_ai_triage.py:

* reachability gate FIRST: the completion call is never attempted unless a
  {AI_HOST}/models probe passed (TCP connect timer + response budget).
* every degradation path (unconfigured / refused / timeout / chat failure)
  falls back to the deterministic rules analysis and reports a stable
  provenance string — never silence, never an exception into CI.
* the configured API key literal can never ride out in a prompt: the
  outbound context is sanitized with the key scrubbed.
* probe results are cached (one timer cost per process, per host).
* notify_triage writes ci-results/run-info/ai-analysis.json and works with
  Telegram unconfigured (result still returned; never raises).
"""

from __future__ import annotations

import json
import socket
from pathlib import Path

import pytest

from nexus_scalp.observability import ci_ai_triage as triage


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for name in (
        "AI_HOST",
        "AI_KEY",
        "AI_MODEL",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_CHAT_ID",
        "USER_ID",
        "GITHUB_TOKEN",
    ):
        monkeypatch.delenv(name, raising=False)
    triage._PROBE_CACHE.clear()
    yield
    triage._PROBE_CACHE.clear()


def _configure(monkeypatch, host: str = "http://ai.invalid:1/v1"):
    monkeypatch.setenv("AI_HOST", host)
    monkeypatch.setenv("AI_KEY", "sk-testkey-0123456789abcdef")


DRIFT_CTX = {"checks": {"dependency_drift": "failed"}, "note": "requirements.lock drift"}


# ---------------------------------------------------------------------------
# gate order + fallback provenance
# ---------------------------------------------------------------------------


def test_unconfigured_falls_back_to_rules_with_provenance(monkeypatch):
    art = triage.analyze("ci-failure", DRIFT_CTX)
    assert art.provenance == triage.PROV_UNCONFIGURED
    assert "dependency lock drift" in art.text
    assert "check_dependency_drift" in art.text


def test_probe_runs_before_chat_and_chat_skipped_when_down(monkeypatch):
    _configure(monkeypatch)
    calls = {"probe": 0, "chat": 0}

    def fake_probe(ep, *, force=False):
        calls["probe"] += 1
        return {
            "status": "TCP_REFUSED",
            "connect_sec": 0.01,
            "total_sec": None,
            "cached": False,
            "error": "refused",
        }

    def fake_chat(ep, s, u, **kw):
        calls["chat"] += 1
        return "should not run", ""

    monkeypatch.setattr(triage, "probe_reachable", fake_probe)
    monkeypatch.setattr(triage, "chat_completion", fake_chat)
    art = triage.analyze("ci-failure", DRIFT_CTX)
    assert calls == {"probe": 1, "chat": 0}
    assert art.provenance == triage.PROV_DOWN
    assert art.text  # rules fallback carried the message


def test_slow_probe_still_gets_one_completion_attempt(monkeypatch):
    _configure(monkeypatch)

    def fake_probe(ep, *, force=False):
        return {
            "status": "SLOW_OK",
            "connect_sec": 2.4,
            "total_sec": 3.1,
            "cached": False,
            "error": "",
        }

    monkeypatch.setattr(triage, "probe_reachable", fake_probe)
    monkeypatch.setattr(
        triage, "chat_completion", lambda ep, s, u, **kw: ("Verdict: flake\nConfidence: low", "")
    )
    art = triage.analyze("ci-failure", {"any": "evidence"})
    assert art.provenance == triage.AI
    assert art.probe["status"] == "SLOW_OK"


def test_chat_failure_falls_back(monkeypatch):
    _configure(monkeypatch)
    monkeypatch.setattr(
        triage,
        "probe_reachable",
        lambda ep, *, force=False: {
            "status": "OK",
            "connect_sec": 0.05,
            "total_sec": 0.4,
            "cached": False,
            "error": "",
        },
    )
    monkeypatch.setattr(triage, "chat_completion", lambda ep, s, u, **kw: (None, "HTTP 503"))
    art = triage.analyze("release-failure", DRIFT_CTX)
    assert art.provenance == triage.PROV_CHAT_FAILED
    assert "drift" in art.text


def test_rules_empty_is_still_reported(monkeypatch):
    art = triage.analyze("push-summary", {"nothing": "recognizable"})
    assert art.provenance == triage.PROV_RULES_EMPTY
    assert art.text == ""
    # the to_result contract: provenance present even without text
    res = art.to_result(False)
    assert res["provenance"] == triage.PROV_RULES_EMPTY and res["ok"] is False


# ---------------------------------------------------------------------------
# secret hygiene on the outbound prompt
# ---------------------------------------------------------------------------


def test_api_key_never_leaves_in_prompt(monkeypatch):
    _configure(monkeypatch)
    captured = {}

    def fake_probe(ep, *, force=False):
        return {"status": "OK", "connect_sec": 0.05, "total_sec": 0.3, "cached": False, "error": ""}

    def fake_chat(ep, system, user, **kw):
        captured["user"] = user
        return ("Verdict: ok", "")

    monkeypatch.setattr(triage, "probe_reachable", fake_probe)
    monkeypatch.setattr(triage, "chat_completion", fake_chat)
    ctx = {"log": "line with key sk-testkey-0123456789abcdef leaked into output"}
    art = triage.analyze("ci-failure", ctx)
    assert art.provenance == triage.AI
    assert "sk-testkey-0123456789abcdef" not in captured["user"]


# ---------------------------------------------------------------------------
# probe mechanics (timers + cache)
# ---------------------------------------------------------------------------


def test_tcp_timer_marks_unroutable_host_fast(monkeypatch):
    _configure(monkeypatch, "http://10.255.255.1:9/v1")  # non-routable TEST-NET
    monkeypatch.setattr(triage, "PROBE_CONNECT_SEC", 0.5)
    art = triage.analyze("ci-failure", DRIFT_CTX)
    assert art.provenance == triage.PROV_DOWN
    assert art.probe["status"] == "TCP_REFUSED"
    assert art.probe["connect_sec"] <= 1.0


def test_probe_cache_shared_within_ttl(monkeypatch):
    _configure(monkeypatch)
    calls = {"n": 0}
    real_connect = socket.create_connection

    def counting_conn(*a, **k):
        calls["n"] += 1
        return real_connect(*a, **k)

    monkeypatch.setattr(triage.socket, "create_connection", counting_conn)
    ep = triage.Endpoint.from_env()
    triage.probe_reachable(ep)
    second = triage.probe_reachable(ep)
    assert second.get("cached") is True
    assert calls["n"] == 1


# ---------------------------------------------------------------------------
# notify_triage: artifact write + never-raise (Telegram unconfigured)
# ---------------------------------------------------------------------------


def test_notify_triage_writes_artifact_offline(tmp_path, monkeypatch):
    results = tmp_path / "ci-results"
    (results / "run-info").mkdir(parents=True)
    ctx_file = tmp_path / "evidence.json"
    ctx_file.write_text(json.dumps(DRIFT_CTX), encoding="utf-8")
    _configure(monkeypatch)
    monkeypatch.setattr(
        triage,
        "probe_reachable",
        lambda ep, *, force=False: {
            "status": "TIMEOUT",
            "connect_sec": 1.0,
            "total_sec": 4.0,
            "cached": False,
            "error": "budget",
        },
    )
    out = triage.notify_triage("ci-failure", results, context_path=ctx_file, send_telegram=False)
    payload = json.loads((results / "run-info" / "ai-analysis.json").read_text(encoding="utf-8"))
    assert payload["provenance"] == triage.PROV_DOWN
    assert "drift" in payload["text"]  # rules fallback carried the message
    assert out["artifact_written"] is True


def test_sanitize_strips_generic_keyvalue_secrets():
    text = "boom password=hunter2 token=123456789:ABCdefGHIjklMNOpqrsTUVwxyz12345"
    out = triage.sanitize(text)
    assert "hunter2" not in out
    assert "ABCdefGHI" not in out


def test_format_ai_triage_escapes_model_output():
    from nexus_scalp.observability.telegram_html import CIContext, format_ai_triage

    html_text = format_ai_triage(
        CIContext(repository="r", workflow="w", run_id="1"),
        kind="ci-failure",
        analysis="<script>alert(1)</script>",
        provenance=triage.PROV_AI,
        model="coding",
        probe={"status": "OK", "connect_sec": 0.1, "total_sec": 0.9},
    )
    assert "<script>" not in html_text
    assert "&lt;script&gt;" in html_text
    assert "AI" in html_text
