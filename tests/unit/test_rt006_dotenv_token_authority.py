"""RT-006 (2026-09-24, observed live): the operator's repo-root .env token is
authoritative and must never be silently discarded.

Reproduced on the live machine: NSE_WEB_AUTH_TOKEN was set in the gitignored
repo-root .env (the documented operator surface — docker-compose and the
launcher banner both read it from there), but the file was never loaded into
os.environ. _resolve_token consulted only os.environ, so it resolved the
secret-STORE value instead, and auth_boot.publish() then OVERWROTE the .env
token with the store value. The operator's copied "?token=<.env value>" was
rejected with 401 on every transport and no bootstrap Set-Cookie was ever
issued, because the browser never held the value the middleware enforced.

Contract pinned here:
* the .env token resolves BELOW a real process env override (containers/CI)
  and ABOVE the secret store;
* current_web_auth_token() honours the same precedence (publish exports the
  OPERATOR value, not the store value);
* publish() NEVER overwrites an operator-set .env token key — only the port
  key is always publishable;
* a machine with no .env token and no env override still boots (the store /
  generator path is untouched).
"""

from __future__ import annotations

import os

import pytest

from nexus_scalp.web import auth as web_auth
from nexus_scalp.web import auth_boot

ENV_TOKEN = "rt006-env-override"
DOTENV_TOKEN = "rt006-operator-dotenv"
STORE_TOKEN = "rt006-secret-store"


@pytest.fixture()
def isolated_store(tmp_path, monkeypatch):
    from nexus_scalp.release import paths as release_paths
    from nexus_scalp.settings import secret_store as secret_store_mod

    monkeypatch.setattr(release_paths, "app_data_root", lambda: tmp_path)
    monkeypatch.setattr(secret_store_mod, "app_data_root", lambda: tmp_path)
    monkeypatch.delenv("NSE_WEB_AUTH_TOKEN", raising=False)
    monkeypatch.delenv(auth_boot.ENV_ACTUAL_PORT, raising=False)
    monkeypatch.delenv("NSE_WEB_PORT", raising=False)
    yield tmp_path


@pytest.fixture()
def dotenv_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(auth_boot, "_dotenv_path", lambda: tmp_path / ".env")
    yield tmp_path


def _write_env(path, key, value):
    p = path / ".env"
    lines = []
    if p.exists():
        lines = [
            ln for ln in p.read_text(encoding="utf-8").splitlines() if not ln.startswith(key + "=")
        ]
    lines.append(f"{key}={value}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_resolve_prefers_env_over_dotenv(isolated_store, dotenv_dir) -> None:
    _write_env(dotenv_dir, "NSE_WEB_AUTH_TOKEN", DOTENV_TOKEN)
    os.environ["NSE_WEB_AUTH_TOKEN"] = ENV_TOKEN
    try:
        resolved, source = web_auth._resolve_token()
    finally:
        del os.environ["NSE_WEB_AUTH_TOKEN"]
    if resolved != ENV_TOKEN:
        raise AssertionError("env token must win over dotenv")
    assert source == "env"


def test_resolve_reads_dotenv_above_secret_store(isolated_store, dotenv_dir) -> None:
    """RT-006 regression guard: the .env operator token wins over the store."""
    from nexus_scalp.settings.secret_store import SecureSecretStore

    _write_env(dotenv_dir, "NSE_WEB_AUTH_TOKEN", DOTENV_TOKEN)
    SecureSecretStore().set_secret(web_auth.WEB_AUTH_TOKEN_SECRET_NAME, STORE_TOKEN)
    resolved, source = web_auth._resolve_token()
    if resolved != DOTENV_TOKEN:
        raise AssertionError("dotenv token must win over store")
    assert source == "dotenv"


def test_current_web_auth_token_reads_dotenv(isolated_store, dotenv_dir) -> None:
    _write_env(dotenv_dir, "NSE_WEB_AUTH_TOKEN", DOTENV_TOKEN)
    assert web_auth.current_web_auth_token() == DOTENV_TOKEN


def test_publish_never_overwrites_operator_dotenv_token(isolated_store, dotenv_dir) -> None:
    """The live symptom: publish() clobbered the operator's .env token."""
    _write_env(dotenv_dir, "NSE_WEB_AUTH_TOKEN", DOTENV_TOKEN)
    out = auth_boot.publish(port=8099)
    assert out["token"] == DOTENV_TOKEN
    text = (dotenv_dir / ".env").read_text(encoding="utf-8")
    assert f"NSE_WEB_AUTH_TOKEN={DOTENV_TOKEN}" in text
    assert f"NSE_WEB_AUTH_TOKEN={STORE_TOKEN}" not in text
    assert "NSE_WEB_ACTUAL_PORT=8099" in text


def test_publish_still_writes_token_when_dotenv_absent(isolated_store, dotenv_dir) -> None:
    os.environ["NSE_WEB_AUTH_TOKEN"] = ENV_TOKEN
    try:
        out = auth_boot.publish(port=8100)
    finally:
        del os.environ["NSE_WEB_AUTH_TOKEN"]
    assert out["token"] == ENV_TOKEN
    text = (dotenv_dir / ".env").read_text(encoding="utf-8")
    assert f"NSE_WEB_AUTH_TOKEN={ENV_TOKEN}" in text
    assert "NSE_WEB_ACTUAL_PORT=8100" in text
