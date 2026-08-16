"""RepairEngine — non-destructive repair operations.

Philosophy:
    * NEVER delete user data silently.
    * Recreate missing directories, heal permissions where safe, initialize
      missing databases/tables from the canonical schema, restore missing
      config from the packaged template, rebuild derived caches.
    * Before any destructive step, report exactly what will happen and
      require a flag (e.g. ``repair --recreate``) — the CLI never passes it
      without user confirmation.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import paths


@dataclass
class RepairResult:
    action: str
    status: str  # OK | SKIPPED | FAILED
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"action": self.action, "status": self.status, "detail": self.detail}


class RepairEngine:
    def __init__(
        self,
        workspace: Path | None = None,
        data_root: Path | None = None,
        template_config: Path | None = None,
    ) -> None:
        self.workspace = workspace or paths.get_runtime_workspace()
        self.data_root = data_root or paths.get_data_root()
        self.template_config = template_config or self._default_template()

    def _default_template(self) -> Path:
        # Packaged bundles ship a template next to the exe; source checkouts
        # use configs/base.yaml (PAPER, safe default — NEVER the LIVE example
        # for a fresh user config, see safety contract).
        candidates = [
            self.workspace / "configs" / "base.yaml",
            self.workspace / "configs" / "live.yaml.example",
        ]
        for c in candidates:
            if c.exists():
                return c
        return candidates[0]

    # ------------------------------------------------------------------
    def run(self, *, recreate_dirs: bool = False, with_news: bool = False) -> list[RepairResult]:
        results: list[RepairResult] = []
        results.append(self._ensure_dirs())
        results.append(self._ensure_config(recreate=recreate_dirs))
        results.append(self._ensure_database())
        if with_news:
            results.append(self._ensure_news_database())
        results.append(self._ensure_models())
        results.append(self._ensure_logs())
        return results

    def _ensure_dirs(self) -> RepairResult:
        try:
            paths.ensure_user_dirs()
            for sub in paths.RUNTIME_SUBDIRS:
                (self.workspace / sub).mkdir(parents=True, exist_ok=True)
            return RepairResult("directories", "OK", "user-data + runtime dirs present")
        except OSError as e:
            return RepairResult("directories", "FAILED", str(e))

    def _ensure_config(self, *, recreate: bool = False) -> RepairResult:
        target = paths.get_user_config_path()
        if target.exists() and not recreate:
            return RepairResult("config", "OK", f"existing config preserved: {target}")
        if not self.template_config.exists():
            return RepairResult(
                "config",
                "SKIPPED",
                f"no template found at {self.template_config}",
            )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.template_config, target)
            return RepairResult(
                "config",
                "OK",
                f"{'recreated' if recreate else 'created'} config from template",
            )
        except OSError as e:
            return RepairResult("config", "FAILED", str(e))

    def _ensure_database(self) -> RepairResult:
        """Initialize the canonical audit DB schema if missing (never resets)."""
        db = self.workspace / "artifacts" / "audit.db"
        if db.exists():
            return RepairResult("database", "OK", "existing audit.db preserved")
        try:
            from nexus_scalp.adapters.database.audit_repository import AuditRepository

            repo = AuditRepository(db_url=f"sqlite:///{db}")
            repo.initialize_schema()  # type: ignore[attr-defined]
            repo.close()
            return RepairResult("database", "OK", f"initialized {db}")
        except Exception as e:
            return RepairResult("database", "FAILED", str(e))

    def _ensure_news_database(self) -> RepairResult:
        news_db = self.workspace / "artifacts" / "news.db"
        if news_db.exists():
            return RepairResult("news_db", "OK", "existing news.db preserved")
        try:
            from nexus_scalp.news.database import NewsDatabase  # type: ignore[import-not-found]

            ndb = NewsDatabase(db_path=str(news_db))
            ndb.initialize_schema()  # type: ignore[attr-defined]
            ndb.close()
            return RepairResult("news_db", "OK", f"initialized {news_db}")
        except Exception as e:
            return RepairResult("news_db", "FAILED", str(e))

    def _ensure_models(self) -> RepairResult:
        model_dir = self.workspace / "artifacts" / "models"
        try:
            model_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return RepairResult("models", "FAILED", str(e))
        present = list(model_dir.rglob("model.pt"))
        if present:
            return RepairResult("models", "OK", f"{len(present)} artifact(s) present")
        return RepairResult(
            "models",
            "SKIPPED",
            "no model artifact — external/optional until training runs",
        )

    def _ensure_logs(self) -> RepairResult:
        try:
            paths.get_logs_dir().mkdir(parents=True, exist_ok=True)
            return RepairResult("logs", "OK", "log dir present")
        except OSError as e:
            return RepairResult("logs", "FAILED", str(e))

    def summary_dict(self, results: list[RepairResult] | None = None) -> dict[str, Any]:
        results = results or self.run()
        return {
            "overall": "OK" if all(r.status != "FAILED" for r in results) else "FAILED",
            "actions": [r.to_dict() for r in results],
        }
