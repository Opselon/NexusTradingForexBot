"""BUG-267: web-auth bootstrap handoff (operator token → env → .env).

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
  ``NSE_WEB_AUTH_TOKEN`` + ``NSE_WEB_ACTUAL_PORT`` to the PROCESS env
  (children inherit the authoritative value) and persists both to the
  repo-root ``.env`` (gitignored; the same file the docker-compose contract
  already reads ``NSE_WEB_AUTH_TOKEN`` from). The docker ``NSE_WEB_PORT``
  mapping key is operator-owned and NEVER written by the launcher.
* The token value is printed ONLY by the 127.0.0.1-bound launcher banner via
  an explicit caller-supplied sink. This module never prints or logs it.
* Opt-out: ``NSE_WEB_AUTH_DOTENV_DISABLE=1`` skips the .env write (the env
  export still happens). Writing .env degrades nothing: a process that
  cannot read the DPAPI blob can, by definition, read the same user's files
  — and .env is exactly where docker operators already look.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.web.auth_boot")

#: Process-env keys published at server boot.
ENV_ACTIVE_TOKEN = "NSE_WEB_AUTH_TOKEN"
#: BUG-267: the port the WEB SERVER ACTUALLY bound (after auto-increment).
#: Deliberately NOT NSE_WEB_PORT: docker-compose uses that key as the HOST
#: side of its port mapping (``${NSE_WEB_PORT:-9090}:9090``) — overwriting
#: it from a local launcher boot would change the container mapping and can
#: collide (compose up failing on a busy host port). The local bind truth
#: gets its own key; NSE_WEB_PORT remains purely operator-configured.
ENV_ACTUAL_PORT = "NSE_WEB_ACTUAL_PORT"
#: Opt-out for the .env persistence (env export still applies).
DOTENV_DISABLE_ENV = "NSE_WEB_AUTH_DOTENV_DISABLE"

_DOTENV_NAME = ".env"
_ENV_FILE_HEADER = (
    "# NSE operator environment — managed by nexus (BUG-267 bootstrap handoff).\n"
    "# NSE_WEB_AUTH_TOKEN is the control-plane credential enforced by WEB-AUTH-P0.\n"
    "# Keep this file private (gitignored). Both web consoles bootstrap cookie\n"
    "# auth automatically (BUG-267); this token is the operator copy for\n"
    "# header/query auth (?token=...), scripts and docker-compose.\n"
    "# NSE_WEB_ACTUAL_PORT is the port the web server ACTUALLY bound at last\n"
    "# boot (after auto-increment past occupied ports). NSE_WEB_PORT stays\n"
    "# the operator/compose mapping key — never written by the launcher.\n"
    "#   legacy console:  http://127.0.0.1:$NSE_WEB_ACTUAL_PORT/\n"
    "#   React  console:  http://127.0.0.1:$NSE_WEB_ACTUAL_PORT/alt/\n"
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


def publish(
    port: int | None = None, *, print_token_to: Callable[[str], None] | None = None
) -> dict[str, Any]:
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
        os.environ[ENV_ACTUAL_PORT] = str(port)
        env[ENV_ACTUAL_PORT] = str(port)
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


def read_env_file() -> dict[str, str]:
    """Parse the repo-root .env (KEY=VALUE lines; comments/blanks ignored).

    Best-effort: unreadable/absent file resolves to {} — callers fall back
    to their own defaults. Used by first-party tooling (doctor's web probe,
    the launcher's port choice) so the values publish() persisted at boot
    are visible WITHOUT importing the secret into unrelated process env.
    """
    path = _dotenv_path()
    if path is None:
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return {}
    out: dict[str, str] = {}
    for raw_line in raw.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key.strip()] = value.strip().strip('"').strip("'")
    return out


def resolved_web_port(default: int = 8080) -> int:
    """The port first-party tooling should probe/publish.

    Precedence: NSE_WEB_ACTUAL_PORT env > .env NSE_WEB_ACTUAL_PORT (written
    by publish() at the last real boot — the port the web server ACTUALLY
    bound after auto-increment) > NSE_WEB_PORT (operator-configured, the
    docker-compose mapping key) > ``default``. This is what makes
    `nexus doctor` and the launcher stop contradicting the running engine
    when 8080 was occupied and the server drifted to 8081+: the FIRST boot
    records the actual port, every later tool reads it. A malformed value
    at any level falls through to the next (never crashes a CLI probe).
    """
    file_env = read_env_file()
    candidates = (
        os.environ.get(ENV_ACTUAL_PORT, ""),
        file_env.get(ENV_ACTUAL_PORT, ""),
        os.environ.get("NSE_WEB_PORT", ""),
        file_env.get("NSE_WEB_PORT", ""),
    )
    for raw in candidates:
        try:
            port = int(raw.strip())
        except ValueError:
            continue
        if 1 <= port <= 65535:
            return port
    return default
