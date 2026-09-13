"""BUG-267: auth_boot contract — boot handoff WITHOUT token generation.

Pins:
* publish() resolves without minting (no env, no store -> token None, no
  secrets written) and exports env + .env atomically when a token exists
* update_env_file replaces keys in place, never duplicates, keeps comments
* resolved_web_port precedence actual-port env > actual-port .env >
  NSE_WEB_PORT (operator/compose key) > default — and NEVER writes
  NSE_WEB_PORT (the docker-compose host-mapping key stays operator-owned)
* the generated-token trap: install_web_auth records the in-process token
  and publish exports THAT value (what the middleware enforces == what the
  operator's .env says — no second token ever minted by tooling)
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from nexus_scalp.web import auth_boot


@pytest.fixture()
def isolated_store(tmp_path, monkeypatch):
    """Secret store redirected at its resolver (test_web_auth precedent) —
    a generated-token branch in any code path lands in tmp, never DPAPI."""
    from nexus_scalp.release import paths as release_paths
    from nexus_scalp.settings import secret_store as secret_store_mod

    monkeypatch.setattr(release_paths, "app_data_root", lambda: tmp_path)
    monkeypatch.setattr(secret_store_mod, "app_data_root", lambda: tmp_path)
    monkeypatch.delenv("NSE_WEB_AUTH_TOKEN", raising=False)
    monkeypatch.delenv(auth_boot.ENV_ACTUAL_PORT, raising=False)
    monkeypatch.delenv("NSE_WEB_PORT", raising=False)
    yield tmp_path


def test_publish_exports_env_token_and_port(tmp_path, monkeypatch, isolated_store) -> None:
    monkeypatch.setenv("NSE_WEB_AUTH_TOKEN", "boot-token-abc")
    monkeypatch.setattr(auth_boot, "_dotenv_path", lambda: tmp_path / ".env")
    out = auth_boot.publish(port=8099)
    assert out["token"] == "boot-token-abc"
    assert out["dotenv"] is True
    assert os.environ[auth_boot.ENV_ACTUAL_PORT] == "8099"
    # the compose mapping key is NEVER written by boot handoff
    assert "NSE_WEB_PORT" not in os.environ
    text = (tmp_path / ".env").read_text(encoding="utf-8")
    assert "NSE_WEB_AUTH_TOKEN=boot-token-abc" in text
    assert "NSE_WEB_ACTUAL_PORT=8099" in text
    assert "NSE_WEB_PORT=" not in text


def test_publish_without_token_never_generates(monkeypatch, isolated_store) -> None:
    """The trap: an unreadable/absent store must NOT mint a second token
    (generation is the middleware's job). publish must report None."""
    monkeypatch.delenv("NSE_WEB_AUTH_TOKEN", raising=False)
    monkeypatch.setattr(auth_boot, "_dotenv_path", lambda: None)
    from nexus_scalp.web import auth as web_auth

    monkeypatch.setattr(web_auth, "_LIVE_WEB_AUTH_TOKEN", {"token": None})
    out = auth_boot.publish(port=None)
    assert out["token"] is None
    assert out["dotenv"] is False


def test_publish_exports_generated_middleware_token(monkeypatch, isolated_store) -> None:
    """Order-tolerance: when create_app generated+persisted the token at
    boot, publish exports the SAME value via web_auth_token_in_process."""
    from nexus_scalp.web import auth as web_auth

    # env scrubbed + store EMPTY -> current_web_auth_token() is None; the
    # middleware resolved one lazily (generated) — publish must relay it,
    # not mint its own.
    monkeypatch.setattr(web_auth, "_LIVE_WEB_AUTH_TOKEN", {"token": "gen-token-xyz"})
    monkeypatch.setattr(auth_boot, "_dotenv_path", lambda: None)
    out = auth_boot.publish(port=8123)
    assert out["token"] == "gen-token-xyz"


def test_update_env_file_replaces_in_place_and_keeps_foreign(tmp_path) -> None:
    p = tmp_path / ".env"
    p.write_text(
        "# operator notes\nFOO=bar\nNSE_WEB_ACTUAL_PORT=8080\n# trailing comment\n",
        encoding="utf-8",
    )
    auth_boot.update_env_file(p, {"NSE_WEB_ACTUAL_PORT": "8081", "NEW_KEY": "v"})
    text = p.read_text(encoding="utf-8")
    assert text.count("NSE_WEB_ACTUAL_PORT=") == 1
    assert "NSE_WEB_ACTUAL_PORT=8081" in text
    assert "FOO=bar" in text and "NEW_KEY=v" in text and "# operator notes" in text


def test_resolved_web_port_precedence(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv(auth_boot.ENV_ACTUAL_PORT, raising=False)
    monkeypatch.delenv("NSE_WEB_PORT", raising=False)
    env_file = tmp_path / ".env"
    monkeypatch.setattr(auth_boot, "_dotenv_path", lambda: env_file)
    assert auth_boot.resolved_web_port() == 8080  # nothing recorded
    env_file.write_text("NSE_WEB_ACTUAL_PORT=8081\n", encoding="utf-8")
    assert auth_boot.resolved_web_port() == 8081  # .env actual port
    env_file.write_text("NSE_WEB_ACTUAL_PORT=8081\nNSE_WEB_PORT=9090\n", encoding="utf-8")
    assert auth_boot.resolved_web_port() == 8081  # actual beats compose key
    monkeypatch.setenv(auth_boot.ENV_ACTUAL_PORT, "8099")
    assert auth_boot.resolved_web_port() == 8099  # process env wins
    monkeypatch.setenv(auth_boot.ENV_ACTUAL_PORT, "not-a-port")
    assert auth_boot.resolved_web_port() == 8081  # garbage falls to .env
    env_file.write_text("NSE_WEB_ACTUAL_PORT=99999999\nNSE_WEB_PORT=9090\n", encoding="utf-8")
    monkeypatch.delenv(auth_boot.ENV_ACTUAL_PORT)
    assert auth_boot.resolved_web_port(default=8080) == 9090  # out-of-range actual -> compose key
    env_file.write_text("", encoding="utf-8")
    assert auth_boot.resolved_web_port(default=8080) == 8080
