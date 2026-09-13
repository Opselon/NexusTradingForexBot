"""BUG-266: web-auth bootstrap handoff (operator token → env → .env).

Why this exists
---------------
WEB-AUTH-P0 resolves the control-plane token from ``NSE_WEB_AUTH_TOKEN``
(env) > ``SecureSecretStore("web_auth_token")`` (DPAPI) > generate+persist.
The third branch is a boot trap: ``SecureSecretStore.get_secret`` RAISES
``SecretStoreError`` when the stored blob exists but cannot be decrypted
(different Windows user/service account, relocated profile, broken ACL), and
``_resolve_token`` swallows that into a NEW random token minted every start.
The operator's remembered token then authenticates nothing, and the only
copy of the new value lives in that process's memory — the banner never
showed it and logs are a forbidden channel (the redactor scrubs credential
keys). Result: both consoles (legacy ``/`` and React ``/alt/``) dead-end in
``{"error":{"code":"UNAUTHORIZED","message":"missing or invalid web auth
token"}}`` with no documented recovery — this is what the operator hit.

Contract (fail-closed preserved)
--------------------------------
* ``resolve_token_at_boot`` NEVER generates: env > secret store > token the
  middleware already resolved for this process. None when nothing resolves
  (the caller decides; generation stays owned by ``_resolve_token``).
* ``publish`` runs at SERVER BOOT ONLY (launcher / ``nexus start`` web
  co-boot — never CLI subcommands like ``doctor``/``status``): exports
  ``NSE_WEB_AUTH_TOKEN`` + ``NSE_WEB_PORT`` to the PROCESS env (children
  inherit the authoritative value) and persists both to the repo-root
  ``.env`` (gitignored; the same file the docker-compose contract already
  reads ``NSE_WEB_AUTH_TOKEN`` from).
* The token value is printed ONLY by the 127.0.0.1-bound launcher banner via
  an explicit caller-supplied sink. This module never prints or logs it.
* Opt-out: ``NSE_WEB_AUTH_DOTENV_DISABLE=1`` skips the .env write (the env
  export still happens). Writing .env degrades nothing: a process that
  cannot read the DPAPI blob can, by definition, read the same user's files
  — and .env is exactly where docker operators already look.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.web.auth_boot")

#: Process-env keys published at server boot.
ENV_ACTIVE_TOKEN = "NSE_WEB_AUTH_TOKEN"
ENV_WEB_PORT = "NSE_WEB_PORT"
#: Opt-out for the .env persistence (env export still applies).
DOTENV_DISABLE_ENV = "NSE_WEB_AUTH_DOTENV_DISABLE"

_DOTENV_NAME = ".env"
_ENV_FILE_HEADER = (
    "# NSE operator environment — managed by nexus (BUG-266 bootstrap handoff).\n"
    "# NSE_WEB_AUTH_TOKEN is the control-plane credential enforced by WEB-AUTH-P0.\n"
    "# Keep this file private (gitignored). Both web consoles bootstrap cookie\n"
    "# auth automatically (BUG-266); this token is the operator copy for\n"
    "# header/query auth (?token=...), scripts and docker-compose.\n"
    "#   legacy console:  http://127.0.0.1:$NSE_WEB_PORT/\n"
    "#   React  console:  http://127.0.0.1:$NSE_WEB_PORT/alt/\n"
)


def _dotenv_path() -> Path | None:
    """Repo-root .env candidate, or None in layouts where writing it makes
    no sense (packaged/frozen trees live under _internal/; the repo root is
    identifiable by pyproject.toml)."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "pyproject.toml").is_file():
            return parent / _DOTENV_NAME
    return None


def update_env_file(path: Path, updates: dict[str, str]) -> None:
    """Set KEY=VALUE lines in an env file, preserving unknown lines/comments.

    Existing keys are replaced IN PLACE (never duplicated); missing keys are
    appended. An unreadable file is replaced (with a loud log line) — it is
    a generated operator file, not user data. Atomic write (tmp+replace).
    """
    try:
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
    except OSError as exc:
        logger.warning("[AUTH-BOOT] .env unreadable, rewriting", path=str(path), error=str(exc))
        existing = ""
    lines = existing.splitlines()
    remaining = dict(updates)
    out: list[str] = []
    for line in lines:
        key = line.partition("=")[0].strip()
        if key in remaining:
            out.append(f"{key}={remaining.pop(key)}")
        else:
            out.append(line)
    if not any(not line.startswith("#") and "=" in line for line in lines):
        out.insert(0, _ENV_FILE_HEADER.rstrip("\n"))
    for key, value in remaining.items():
        out.append(f"{key}={value}")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def publish(port: int | None = None, *, print_token_to: Callable[[str], None] | None = None) -> dict[str, Any]:
    """Server-boot token/port handoff. Returns {"token", "port", "dotenv"}.

    ``print_token_to``: caller-supplied sink for the launcher's LOCAL banner
    only; never set by non-web callers. This function never prints or logs
    the token itself.
    """
    from nexus_scalp.web.auth import current_web_auth_token, web_auth_token_in_process

    # Resolve WITHOUT generating. If nothing exists yet, the auth
    # middleware's own _resolve_token (create_app) owns generation +
    # persistence; publish must never mint a competing value. Order-
    # tolerant: safe before OR after create_app.
    token = current_web_auth_token() or web_auth_token_in_process()
    env: dict[str, str] = {}
    if token:
        os.environ[ENV_ACTIVE_TOKEN] = token
        env[ENV_ACTIVE_TOKEN] = token
    else:
        logger.warning(
            "[AUTH-BOOT] no web auth token resolvable at publish time; the "
            "auth middleware owns generation+persistence for this process"
        )
    if port is not None:
        os.environ[ENV_WEB_PORT] = str(port)
        env[ENV_WEB_PORT] = str(port)
    dotenv_written = False
    if env and not os.environ.get(DOTENV_DISABLE_ENV, "").strip():
        path = _dotenv_path()
        if path is not None:
            try:
                update_env_file(path, env)
                dotenv_written = True
            except OSError as exc:
                logger.warning("[AUTH-BOOT] .env write failed", path=str(path), error=str(exc))
    if token and print_token_to is not None:
        print_token_to(token)
    return {"token": token, "port": port, "dotenv": dotenv_written}
