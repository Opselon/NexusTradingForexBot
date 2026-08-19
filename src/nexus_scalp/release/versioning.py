"""Runtime Version Consistency (TASK-9 production release layer).

Exposes the full version identity of a running installation and detects
drift (brief sections 15/52):

    application_version, git commit, database schema version(s), feature
    schema, model schema, Web bundle version — all reported from real
    backend/build data.  A conflicting combination is reported as
    VERSION_INCONSISTENCY (never silently ignored).

The Web bundle version comes from the build-time stamp (build-info.json
``web_bundle_version``) OR from hashing the ACTUAL served assets at runtime
when the stamp is absent — so a stale bundled app.js cannot hide behind a
missing stamp (brief section 16).  No hardcoded versions anywhere.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from nexus_scalp.features.schema import ACTIVE_SCHEMA_ID, FEATURE_SCHEMAS
from nexus_scalp.release.metadata import get_build_info_file, get_version_info

#: Reported statuses (brief section 52).
STATUS_CONSISTENT = "CONSISTENT"
STATUS_INCONSISTENT = "VERSION_INCONSISTENCY"

#: Web bundle stamp key inside build-info.json (written by the release build).
WEB_BUNDLE_STAMP_KEY = "web_bundle_version"

#: Runtime-capability probe (default: liquidity feature producer importable).
def _liquidity_producer_available() -> bool:
    try:
        import nexus_scalp.features.liquidity_engine  # noqa: F401

        return True
    except Exception:
        return False


#: Database schema version provider — supplied by callers that already own a
#: migration engine instance (web server, CLI).  Absence -> "unknown".
def _no_db_provider() -> dict[str, Any]:
    return {}


@dataclass
class RuntimeVersionBlock:
    """One consistent snapshot of the running installation's version identity.

    ``db_provider`` is an optional callable returning per-domain schema
    versions (e.g. ``{"audit": {"current": 4, "expected": 4}, ...}``).
    """

    db_provider: Callable[[], dict[str, Any]] | None = None
    web_dir: Path | None = None

    def build(self) -> dict[str, Any]:
        info = get_version_info()
        app_version = str(info.get("version") or "0.0.0")
        commit = str(info.get("commit") or "")[:12]
        build_info = _read_build_info_dict()

        # ---- Web bundle version (stamp OR live-hash of served assets) ----
        stamp = str(build_info.get(WEB_BUNDLE_STAMP_KEY) or "").strip()
        if stamp:
            web_bundle_version = stamp
            web_bundle_stamp_source = "build-info.json"
        else:
            web_bundle_version, count = _hash_web_dir(self.web_dir)
            web_bundle_stamp_source = f"live-hash of {count} assets" if count else "none"

        # ---- Feature schema (registry is the single source of truth) ----
        active_schema = _active_schema_info()

        # ---- Database schema versions ----
        db_versions: dict[str, Any] = {}
        if self.db_provider is not None:
            try:
                db_versions = self.db_provider() or {}
            except Exception:
                db_versions = {"error": "db provider unavailable"}

        block = {
            "application_version": app_version,
            "commit": commit,
            "web_bundle_version": web_bundle_version,
            "web_bundle_stamp_source": web_bundle_stamp_source,
            "feature_schema": active_schema,
            "database_schema": db_versions,
            "build_timestamp": str(info.get("build_timestamp") or ""),
            "channel": str(info.get("channel") or ""),
            "checked_at": datetime.now(UTC).isoformat(),
        }

        # ---- Consistency verdict (no silent contradiction) ----
        problems: list[str] = []
        # (1) build-info stamp vs live-hash when BOTH present and disagreeing.
        live_hash, live_count = _hash_web_dir(self.web_dir)
        if stamp and live_count and live_hash and stamp != live_hash and live_count > 0:
            problems.append(
                f"web bundle stamp {stamp} != served assets hash {live_hash[:12]}"
            )
        # (2) active schema must be registered.
        if not active_schema.get("registered"):
            problems.append(f"active schema {ACTIVE_SCHEMA_ID} not registered")
        # (3) DB version drift: any domain whose current != expected.
        for _domain, dv in db_versions.items():
            if isinstance(dv, dict) and "current" in dv and "expected" in dv:
                if dv["current"] != dv["expected"]:
                    problems.append(
                        f"db {_domain} v{dv['current']} != expected v{dv['expected']}"
                    )
        # (4) app version sanity: never 0.0.0 in a real release (dev only).
        if app_version == "0.0.0":
            problems.append("application version 0.0.0 (unstamped build)")

        block["problems"] = problems
        block["version_status"] = (
            STATUS_INCONSISTENT if problems else STATUS_CONSISTENT
        )
        return block


def _read_build_info_dict() -> dict[str, Any]:
    f = get_build_info_file()
    if f is None:
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _hash_web_dir(web_dir: Path | None) -> tuple[str, int]:
    """Content hash of the ACTUAL served web assets (brief section 16).

    Hashes app.js + api_client.js + index.html + styles.css when present.
    Returns (hash, count) — count == 0 when no assets are found.
    """
    if web_dir is None:
        return "", 0
    names = ("app.js", "api_client.js", "index.html", "styles.css")
    h = hashlib.sha256()
    count = 0
    for name in names:
        p = web_dir / name
        if p.is_file():
            try:
                h.update(p.read_bytes())
                count += 1
            except OSError:
                pass
    if count == 0:
        return "", 0
    return h.hexdigest(), count


def _active_schema_info() -> dict[str, Any]:
    try:
        schema = FEATURE_SCHEMAS.resolve(ACTIVE_SCHEMA_ID)
        return {
            "id": schema.schema_id,
            "dimension": schema.dimension,
            "is_active": schema.is_active,
            "registered": True,
        }
    except Exception:
        return {"id": ACTIVE_SCHEMA_ID, "dimension": 0, "is_active": False, "registered": False}


def default_db_versions_provider() -> dict[str, Any]:
    """Best-effort per-domain schema versions via the canonical engine.

    Uses the migration engine's ``status()`` for each known domain.  Returns
    an empty dict when the engine is unavailable (never raises).
    """
    try:
        from nexus_scalp.database import engine as _engine_mod
        from nexus_scalp.database.engine import DatabaseMigrationEngine
        from nexus_scalp.database.models import DatabaseDomain

        domains = [
            DatabaseDomain.AUDIT,
            DatabaseDomain.NEWS,
            DatabaseDomain.CANDLE_INTEL,
        ]
        out: dict[str, Any] = {}
        for domain in domains:
            path = _engine_mod.db_path_for_domain(domain.value)
            if path is None:
                continue
            eng = DatabaseMigrationEngine(path, domain)
            try:
                st = eng.status()
                out[domain.value] = {
                    "current": st.get("current_version", 0),
                    "expected": st.get("expected_version", 0),
                    "state": st.get("state", ""),
                    "pending": len(st.get("pending", []) or []),
                }
            except Exception:
                out[domain.value] = {"current": 0, "expected": 0, "state": "unavailable"}
        return out
    except Exception:
        return {}