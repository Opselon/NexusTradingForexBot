#!/usr/bin/env python3
"""Fail-closed test-database isolation guard for CI lanes.

The self-hosted runners are persistent machines: a previous run (or a
local gate on the same box) may have left a persisted ``app_settings.db``
pointing at PostgreSQL, and the audit test-isolation seam plus the
persisted provider would otherwise resolve the test DB from that machine
state — i.e. CI would test against a real/shared database.

The CI jobs pin the test surface at JOB level (sqlite provider, per-run
temp paths under RUNNER_TEMP); this guard proves the pinning actually
took effect through the repo's own resolver, before any test runs:

* the ``audit`` domain resolves to SQLite with no PostgreSQL host, and
* no ``NSE_DATABASE__PG_HOST`` override is present in the environment, and
* the resolved SQLite file actually lives in an ephemeral temp directory
  (RUNNER_TEMP when set, else the system temp dir) — provider==sqlite
  alone does NOT prove isolation: with the ``NSE_DATABASE__SQLITE_PATH``
  pin missing, the resolver falls back to the repo-relative default
  (``artifacts/audit.db`` via ``default_sqlite_path``), which is sqlite
  with no host, so the old guard passed while tests shared one
  checkout-persistent database file across runs; and
* when RUNNER_TEMP is set (every GitHub runner), the three job-level DB
  pins (``NSE_DATABASE__SQLITE_PATH``, ``NEXUS_AUDIT_DB``,
  ``NEXUS_SETTINGS_DB``) are all present and inside RUNNER_TEMP, so a
  missing or misordered pin step cannot silently leave a test seam
  pointed at runner-persistent state.

Exit 0 = isolated (sqlite only, in a temp dir). Anything else exits
non-zero with a plain-language reason, so the lane fails instead of
touching a shared DB.
"""

from __future__ import annotations

import os
import sys
import tempfile


def _fail(msg: str) -> int:
    print(f"::error::test-db isolation FAILED: {msg}", file=sys.stderr)
    return 1


def _is_within(path: str, root: str) -> bool:
    """True when ``path`` resolves inside ``root`` (no ``..`` escape)."""
    try:
        real_path = os.path.realpath(path)
        real_root = os.path.realpath(root)
    except OSError:
        return False
    return os.path.commonpath([real_path, real_root]) == real_root


def _effective_sqlite_path(cfg: object) -> str:
    """The file the resolved audit config would actually open."""
    path = getattr(cfg, "sqlite_path", "") or ""
    if path:
        return path
    uri = getattr(cfg, "sqlite_uri", "") or ""
    for prefix in ("sqlite:///", "sqlite://"):
        if uri.startswith(prefix):
            return uri[len(prefix) :]
    return uri


def main() -> int:
    pg_host = os.environ.get("NSE_DATABASE__PG_HOST", "").strip()
    if pg_host:
        return _fail(
            f"NSE_DATABASE__PG_HOST={pg_host!r} is set — refusing to test "
            "against a configured PostgreSQL host"
        )

    try:
        from nexus_scalp.database.config import load_database_config
    except Exception as exc:
        return _fail(f"cannot load DB config: {exc}")

    try:
        cfg = load_database_config("audit")
    except Exception as exc:
        return _fail(f"audit config did not resolve: {exc}")

    provider = getattr(cfg, "provider", None)
    provider_value = getattr(provider, "value", provider)
    host = getattr(cfg, "host", "") or ""
    if provider_value != "sqlite" or host:
        return _fail(
            "audit domain resolved to "
            f"provider={provider_value!r} host={host!r} — expected sqlite with "
            "no host. The runner's persisted settings may point at a shared "
            "database; refusing to run tests."
        )

    # --- the resolved SQLite file must be ephemeral -----------------------
    # A sqlite provider with no host used to be the whole check, but the
    # default sqlite path is repo-relative (artifacts/audit.db) — shared
    # across runs on a persistent checkout — so the file's location is
    # part of the isolation proof, not just its provider.
    runner_temp = os.environ.get("RUNNER_TEMP", "").strip()
    sqlite_path = _effective_sqlite_path(cfg)
    if not sqlite_path:
        return _fail(
            "audit domain resolved to sqlite but with no sqlite file path — "
            "cannot prove the test DB is ephemeral"
        )
    temp_root = runner_temp or tempfile.gettempdir()
    if not _is_within(sqlite_path, temp_root):
        return _fail(
            f"audit sqlite_path={sqlite_path!r} is outside the ephemeral "
            f"temp dir {temp_root!r} — expected a per-run temp file "
            "(the NSE_DATABASE__SQLITE_PATH pin is missing or points at "
            "runner-persistent state, e.g. artifacts/audit.db)"
        )

    # --- the job-level pins must all be present and ephemeral -------------
    # Only enforced when RUNNER_TEMP is set: that is the CI shape (the pin
    # step writes all four paths under RUNNER_TEMP before this guard runs).
    # Locally (no RUNNER_TEMP) the per-test conftest fixtures own the pins
    # and this guard only proves the resolved file is a temp file.
    if runner_temp:
        for key in (
            "NSE_DATABASE__SQLITE_PATH",
            "NEXUS_AUDIT_DB",
            "NEXUS_SETTINGS_DB",
        ):
            value = os.environ.get(key, "").strip()
            if not value:
                return _fail(
                    f"{key} is unset while RUNNER_TEMP is set — the 'Pin "
                    "test DB to ephemeral sqlite paths' step did not run "
                    "or did not export it; refusing to run tests against "
                    "an unpinned (possibly persistent) database"
                )
            if not _is_within(value, runner_temp):
                return _fail(
                    f"{key}={value!r} is outside RUNNER_TEMP={runner_temp!r} "
                    "— the pin points at runner-persistent state"
                )

    print(f"test-db isolation: OK (audit provider={provider_value} sqlite_path={sqlite_path!r})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
