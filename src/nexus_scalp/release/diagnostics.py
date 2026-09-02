"""Sanitized diagnostics export.

Produces a ZIP archive under the diagnostics directory containing:

    * version / architecture / OS info
    * runtime status + dependency versions
    * model metadata + feature schema
    * worker status + recent logs
    * database metadata (schema + row counts — NEVER the data)

NEVER included: passwords, API keys, tokens, broker credentials, account
secrets, private config values, or database contents.
"""

from __future__ import annotations

import json
import re
import sqlite3
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from . import paths
from .health import HealthEngine
from .metadata import get_version_info

_SECRET_PATTERNS = [
    re.compile(
        r"(?i)(password|passwd|secret|token|api[_-]?key|apikey|private[_-]?key|bot[_-]?token)\s*[=:]\s*['\"]?[^\s'\"]+"
    ),
    re.compile(r"(?i)ey[A-Za-z0-9_\-]{20,}"),
    re.compile(r"(?i)sk-[A-Za-z0-9]{20,}"),
]


def _dependency_versions() -> dict[str, str]:
    out: dict[str, str] = {}
    for mod in (
        "torch",
        "numpy",
        "polars",
        "pydantic",
        "rich",
        "fastapi",
        "uvicorn",
        "httpx",
        "yaml",
        "structlog",
    ):
        try:
            m = __import__(mod)
            out[mod] = getattr(m, "__version__", "unknown")
        except Exception:
            out[mod] = "not installed"
    return out


def _model_metadata(workspace: Path) -> dict[str, Any]:
    model_dir = workspace / "artifacts" / "models"
    matches = sorted(model_dir.rglob("model.pt")) if model_dir.exists() else []
    if not matches:
        return {"model_artifact": None}
    artifact = matches[0]
    info: dict[str, Any] = {"model_artifact": str(artifact), "size": artifact.stat().st_size}
    try:
        import torch  # type: ignore[import-not-found]

        sd = torch.load(artifact, map_location="cpu", weights_only=True)
        if isinstance(sd, dict):
            info["tensor_count"] = len(sd.get("state_dict", {}))
    except Exception:
        info["introspection"] = "failed"
    return info


def _db_metadata(db_path: Path) -> dict[str, Any]:
    if not db_path.exists():
        return {"exists": False}
    meta: dict[str, Any] = {"exists": True, "tables": []}
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        try:
            for name, sql in con.execute(
                "SELECT name, sql FROM sqlite_master WHERE type='table' ORDER BY name"
            ):
                try:
                    count = con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
                except sqlite3.Error:
                    count = -1
                meta["tables"].append({"name": name, "rows": count, "sql": sql})
        finally:
            con.close()
    except sqlite3.Error as e:
        meta["error"] = str(e)
    return meta


def _severity_of(text: str) -> bool:
    return any(p.search(text) for p in _SECRET_PATTERNS)


def export_diagnostics(workspace: Path | None = None) -> Path:
    """Create the sanitized diagnostics archive; returns its path."""
    workspace = workspace or paths.get_runtime_workspace()
    health = HealthEngine(workspace=workspace)
    verdict, entries = health.overall()
    log_dir = paths.get_logs_dir()
    recent_logs = sorted(log_dir.glob("*.log"))[-5:] if log_dir.exists() else []

    stamp = datetime.now(UTC).strftime("%Y%m%d_%H%M%S")
    out_dir = paths.get_diagnostics_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"nexus-diagnostics-{stamp}.zip"

    payload: dict[str, Any] = {
        "version": get_version_info(),
        "environment": health.env().raw,
        "hardware": health.env().raw,
        "health": {"overall": verdict, "checks": [e.to_dict() for e in entries]},
        "dependencies": _dependency_versions(),
        "model": _model_metadata(workspace),
        "feature_schema": _feature_schema(),
        "audit_db_metadata": _db_metadata(workspace / "artifacts" / "audit.db"),
        "news_db_metadata": _db_metadata(workspace / "artifacts" / "news.db"),
        "exported_at": datetime.now(UTC).isoformat(),
    }

    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("diagnostics.json", json.dumps(payload, indent=2, default=str))
        for log in recent_logs:
            try:
                text = log.read_text(encoding="utf-8", errors="replace")
                # Redact anything secret-shaped even in logs.
                for pat in _SECRET_PATTERNS:
                    text = pat.sub("[REDACTED]", text)
                zf.writestr(f"logs/{log.name}", text)
            except OSError:
                continue
    return out


def _feature_schema() -> dict[str, Any]:
    try:
        from nexus_scalp.features.schema import ACTIVE_SCHEMA_ID, FEATURE_SCHEMAS

        s = FEATURE_SCHEMAS.resolve(ACTIVE_SCHEMA_ID)
        return {"schema_id": s.schema_id, "dimension": s.dimension, "active": s.is_active}
    except Exception as e:
        return {"error": str(e)}
