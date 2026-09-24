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
    # Operator-facing remedy for a FAILED/SKIPPED step (empty when nothing is
    # actionable). Carried alongside the status so the doctor/repair renderer
    # can surface "here is the exact command" instead of a bare FAILED.
    suggestion: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "action": self.action,
            "status": self.status,
            "detail": self.detail,
            "suggestion": self.suggestion,
        }


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
        # for a fresh user config, see safety contract). In PyInstaller
        # onedir the configs land under _internal/configs/.
        candidates = [
            self.workspace / "configs" / "base.yaml",
            self.workspace / "_internal" / "configs" / "base.yaml",
            self.workspace / "configs" / "live.yaml.example",
            self.workspace / "_internal" / "configs" / "live.yaml.example",
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
        # BUG-146: EVERY canonical persistence domain is provisioned by setup
        # and repair (never only audit). with_news keeps its legacy meaning
        # (news is included in the default pass now; the flag remains for
        # callers that pass False explicitly to skip it).
        if with_news:
            results.append(self._ensure_news_database())
        results.append(self._ensure_candle_intel_database())
        results.append(self._ensure_strategies_database())
        results.append(self._ensure_settings_database())
        results.append(self._ensure_models())
        results.append(self._ensure_logs())
        return results

    # ------------------------------------------------------------------
    # Public, side-effect-free seams for callers that need ONE repair action
    # instead of the whole run() (doctor --fix for a single category, the
    # setup wizard, or an integrator wiring a sibling lane's step into the
    # repair sequence). Each returns a RepairResult and never raises.
    def repair_configuration(self, *, recreate: bool = False) -> RepairResult:
        """Create the user config from the packaged template if it is missing.

        Idempotent and non-destructive: an existing config is validated, never
        rewritten (``recreate=True`` requires the operator's explicit
        ``--recreate-config`` confirmation). This is the single entry point that
        makes ``%LOCALAPPDATA%/NexusScalpEngine/config/nexus.yaml`` exist, so
        ``check_configuration`` can move from FAIL/NOT_INITIALIZED to PASS
        through the repair path the operator is already told to run
        (``nexus doctor --fix`` / ``nexus repair``), instead of the engine
        silently booting from the ``configs/base.yaml`` fallback.
        """
        return self._ensure_config(recreate=recreate)

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
            # An existing user config is ALWAYS preserved (safety contract):
            # doctor --fix / repair must NEVER overwrite an operator's config.
            # But "present" is not "loadable" — validate it honestly so an
            # invalid config reports FAILED with a fix suggestion instead of a
            # silent OK while the engine secretly falls back to configs/base.yaml
            # (engine_boot.py:382-388). See _validate_existing_config.
            return self._validate_existing_config(target)
        if not self.template_config.exists():
            return RepairResult(
                "config",
                "SKIPPED",
                f"no template found at {self.template_config}",
            )
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.template_config, target)
            res = RepairResult(
                "config",
                "OK",
                f"{'recreated' if recreate else 'created'} config from template",
            )
        except OSError as e:
            return RepairResult("config", "FAILED", str(e))
        # Freshly written from the packaged template: the copy is byte-identical
        # to configs/base.yaml, which the loader accepts. Prove it rather than
        # assume it, so a bootstrap that handed the operator an unloadable config
        # could never report OK (the loader contract is the same one
        # check_configuration uses).
        validated = self._validate_existing_config(target)
        if validated.status != "OK":
            return validated
        return res

    def _validate_existing_config(self, target: Path) -> RepairResult:
        """Honest validation of a present user config (non-destructive).

        Never rewrites a valid config and never deletes an invalid one — the
        remedy for an invalid config is the operator's explicit
        ``--recreate-config``. The repair result just reports the truth:

        * OK      — loads through ``AppConfig.load_from_yaml`` (the same loader
          the engine and ``check_configuration`` use, so repair and health can
          never disagree about the same file).
        * FAILED  — the file exists but cannot be parsed/validated. Carries a
          fix suggestion naming the exact command. This is the divergence the
          health probe exposed: the engine boots fine via the
          ``configs/base.yaml`` fallback while doctor reports a silent OK.

        Any unexpected error while validating is reported FAILED, never raised
        — a repair step must never abort the whole run (failure isolation,
        same contract as every other ``_ensure_*`` step).
        """
        try:
            from nexus_scalp.configuration.config import AppConfig

            AppConfig.load_from_yaml(target)
        except Exception as e:
            return RepairResult(
                "config",
                "FAILED",
                f"existing config at {target} is invalid ({type(e).__name__}: {e}) "
                "— NOT overwritten; the engine is running from its fallback config",
                "Back up the file, then run `nexus repair --recreate-config` "
                "(or fix the YAML by hand) to restore a valid configuration.",
            )
        return RepairResult("config", "OK", f"existing config preserved: {target}")

    def _ensure_database(self) -> RepairResult:
        """Initialize the canonical audit DB schema if missing (never resets)."""
        db = self.workspace / "artifacts" / "audit.db"
        if db.exists():
            return RepairResult("database", "OK", "existing audit.db preserved")
        try:
            from nexus_scalp.adapters.database.audit_repository import AuditRepository

            repo = AuditRepository(db_url=f"sqlite:///{db}")
            # BUG-146: AuditRepository creates its schema in __init__
            # (_setup_storage); it has no initialize_schema() method. Calling
            # the nonexistent method aborted the whole setup wizard.
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
            # NewsDatabase also initializes its schema in __init__.
            ndb.close()
            return RepairResult("news_db", "OK", f"initialized {news_db}")
        except Exception as e:
            return RepairResult("news_db", "FAILED", str(e))

    def _ensure_candle_intel_database(self) -> RepairResult:
        """BUG-146: pre-create the candle intelligence DB (candle_intel.db).

        Anchors the store explicitly to THIS workspace's artifacts dir — the
        store's own CWD-anchoring would put the DB in the process CWD's
        artifacts (wrong for a frozen EXE launched from elsewhere).
        """
        db = self.workspace / "artifacts" / "candle_intel.db"
        if db.exists():
            return RepairResult("candle_intel_db", "OK", "existing candle_intel.db preserved")
        try:
            from nexus_scalp.candle_intelligence.config import CandleIntelligenceConfig
            from nexus_scalp.candle_intelligence.store import CandleIntelStore

            cfg = CandleIntelligenceConfig(db_path=str(db))
            store = CandleIntelStore(cfg)
            store.close()
            return RepairResult("candle_intel_db", "OK", f"initialized {db}")
        except Exception as e:
            return RepairResult("candle_intel_db", "FAILED", str(e))

    def _ensure_strategies_database(self) -> RepairResult:
        """BUG-146: pre-create the strategy research DB (strategies.db).

        Anchored explicitly to THIS workspace's artifacts dir.

        Release acceptance 2026-09-02 (BUG-217): the frozen CLI bundle
        excludes numpy by design, and `nexus_scalp.strategies.research_store`
        transitively imports it (strategies/__init__ -> base -> research.
        backtest -> metrics). A FAILED strategies-db step aborted the whole
        setup wizard on the packaged artifact. The research DB is an
        OPTIONAL research-tier component: when its import chain is
        unavailable the step reports SKIPPED (with the honest reason)
        instead of failing setup.
        """
        db = self.workspace / "artifacts" / "strategies.db"
        if db.exists():
            return RepairResult("strategies_db", "OK", "existing strategies.db preserved")
        try:
            from nexus_scalp.database.config import DatabaseConfig
            from nexus_scalp.strategies.research_store import StrategyResearchStore

            store = StrategyResearchStore(DatabaseConfig.for_sqlite("strategies", path=str(db)))
            store.ensure_schema()
            if hasattr(store, "close"):
                store.close()
            return RepairResult("strategies_db", "OK", f"initialized {db}")
        except ImportError as e:
            # Optional tier unavailable in this bundle (frozen CLI excludes
            # numpy) - skip honestly, never fail setup for an optional DB.
            return RepairResult(
                "strategies_db", "SKIPPED", f"research tier unavailable in this bundle: {e}"
            )
        except Exception as e:
            return RepairResult("strategies_db", "FAILED", str(e))

    def _ensure_settings_database(self) -> RepairResult:
        """BUG-146: pre-create the isolated settings DB (app_settings.db)."""
        try:
            from nexus_scalp.settings.paths import settings_db_path

            db = settings_db_path()
            if db.exists():
                return RepairResult(
                    "settings_db", "OK", f"existing app_settings.db preserved ({db})"
                )
            from nexus_scalp.settings.service import SettingsDatabase

            sdb = SettingsDatabase(db_path=db)
            sdb.close()
            return RepairResult("settings_db", "OK", f"initialized {db}")
        except Exception as e:
            return RepairResult("settings_db", "FAILED", str(e))

    def _ensure_models(self) -> RepairResult:
        model_dir = self.workspace / "artifacts" / "models"
        try:
            model_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            return RepairResult("models", "FAILED", str(e))
        present = list(model_dir.rglob("model.pt"))
        if present:
            return RepairResult("models", "OK", f"{len(present)} artifact(s) present")
        # BUG-296 (Z-B1): provisioning the starter is the EXPLICIT
        # `nexus repair --model` / first-run coordinator path — plain repair
        # keeps the legacy SKIPPED semantics (never silently writes model
        # bytes the operator did not ask for).
        return RepairResult(
            "models",
            "SKIPPED",
            "no model artifact — run `nexus repair --model` (starter), "
            "`nexus model-setup` (official download / train your own), "
            "or `nexus train-once`",
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
