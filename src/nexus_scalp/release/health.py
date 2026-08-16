"""HealthEngine — reusable health / doctor checks for the Nexus release.

Categories checked (each returns PASS / WARNING / FAIL with reason+suggestion):

    SYSTEM, RUNTIME, CONFIGURATION, DATABASE, MODEL, FEATURE_SCHEMA,
    PYTHON/RUNTIME, NETWORK, MT5, GPU, DISK, MEMORY, LOGGING, WORKERS,
    NEWS, EXPERIENCE, RESEARCH, TRAINING, SHADOW, ACCOUNTING.

The engine is intentionally dependency-light and failure-isolated: every check
is wrapped so one broken subsystem (e.g. a corrupt DB file) reports FAIL and
never crashes the whole doctor/health run.
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import environment as envmod
from . import paths
from .metadata import get_version_info

# Categories known to the diagnostics/health contract.
ALL_CATEGORIES = [
    "SYSTEM",
    "RUNTIME",
    "CONFIGURATION",
    "DATABASE",
    "MODEL",
    "FEATURE_SCHEMA",
    "GPU",
    "MT5",
    "NETWORK",
    "DISK",
    "MEMORY",
    "LOGGING",
    "WORKERS",
    "NEWS",
    "EXPERIENCE",
    "RESEARCH",
    "TRAINING",
    "SHADOW",
    "ACCOUNTING",
]

# Subsystems that count toward the READY verdict.
CRITICAL_CATEGORIES = {
    "SYSTEM",
    "RUNTIME",
    "CONFIGURATION",
    "DATABASE",
    "MODEL",
    "FEATURE_SCHEMA",
}

CheckFn = Callable[..., tuple[str, str, str]]  # (verdict, reason, suggestion)


@dataclass
class HealthEntry:
    category: str
    verdict: str  # PASS | WARNING | FAIL
    reason: str = ""
    suggestion: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "category": self.category,
            "verdict": self.verdict,
            "reason": self.reason,
            "suggestion": self.suggestion,
        }


def _db_health(db_path: Path) -> tuple[str, str]:
    """Basic SQLite integrity probe. Returns (verdict, reason)."""
    if not db_path.exists():
        return "FAIL", f"database file missing: {db_path}"
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        try:
            integrity = con.execute("PRAGMA integrity_check").fetchone()
            if integrity and integrity[0] != "ok":
                return "FAIL", f"integrity_check -> {integrity[0]}"
            tables = [
                r[0]
                for r in con.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
                )
            ]
            return "PASS", f"{len(tables)} tables, integrity ok"
        finally:
            con.close()
    except sqlite3.Error as e:
        return "FAIL", f"cannot open database: {e}"


def _db_tables(db_path: Path) -> set[str]:
    if not db_path.exists():
        return set()
    try:
        con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=2)
        try:
            return {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            con.close()
    except sqlite3.Error:
        return set()


class HealthEngine:
    """Runs all subsystem checks; verdicts are PASS/WARNING/FAIL."""

    def __init__(
        self,
        config_path: Path | None = None,
        workspace: Path | None = None,
        db_path: Path | None = None,
        news_db_path: Path | None = None,
        model_dir: Path | None = None,
    ) -> None:
        self.config_path = config_path or paths.get_user_config_path()
        self.workspace = workspace or paths.get_runtime_workspace()
        self.db_path = db_path or (self.workspace / "artifacts" / "audit.db")
        self.news_db_path = news_db_path or (self.workspace / "artifacts" / "news.db")
        self.model_dir = model_dir or (self.workspace / "artifacts" / "models")
        self._config: Any | None = None
        self._env: envmod.EnvironmentInfo | None = None

    # ------------------------------------------------------------------
    # Config loading (shared, failure-isolated)
    # ------------------------------------------------------------------
    def _load_config(self) -> Any | None:
        if self._config is not None:
            return self._config
        try:
            from nexus_scalp.configuration.config import AppConfig

            if not self.config_path.exists():
                self._config = False
                return None
            self._config = AppConfig.load_from_yaml(self.config_path)
            return self._config
        except Exception as e:
            self._config = False
            self._config_error = str(e)  # type: ignore[attr-defined]
            return None

    # ------------------------------------------------------------------
    # Individual checks
    # ------------------------------------------------------------------
    def check_system(self) -> HealthEntry:
        env = self.env()
        if env.architecture_supported:
            return HealthEntry(
                "SYSTEM",
                "PASS",
                f"{env.os_name} {env.os_version} / {env.architecture}",
            )
        return HealthEntry(
            "SYSTEM",
            "FAIL",
            f"Unsupported architecture: {env.architecture}",
            "Run on a Windows x64 machine (ARM64 unsupported by dependency stack).",
        )

    def check_runtime(self) -> HealthEntry:
        v = get_version_info()
        py = f"Python {sys.version.split()[0]}"
        return HealthEntry(
            "RUNTIME",
            "PASS",
            f"version {v['version']} · {v['channel']} · {py} · commit {v['commit'] or 'n/a'}",
        )

    def check_configuration(self) -> HealthEntry:
        cfg = self._load_config()
        if cfg is None:
            err = getattr(self, "_config_error", "unknown")
            return HealthEntry(
                "CONFIGURATION",
                "FAIL",
                f"config '{self.config_path}' failed to load: {err}",
                "Run `nexus repair` to restore from template, or fix the YAML.",
            )
        if cfg is False:
            return HealthEntry(
                "CONFIGURATION",
                "FAIL",
                f"config '{self.config_path}' missing",
                "Run `nexus setup` or `nexus repair`.",
            )
        mode = getattr(getattr(cfg, "execution", None), "mode", None)
        mode_txt = str(getattr(mode, "value", mode))
        return HealthEntry(
            "CONFIGURATION",
            "PASS",
            f"mode={mode_txt} symbol={cfg.execution.symbol} schema={cfg.model.feature_schema_version}",
        )

    def check_database(self) -> HealthEntry:
        verdict, reason = _db_health(self.db_path)
        entry = HealthEntry("DATABASE", verdict, f"audit.db: {reason}")
        if verdict != "PASS":
            entry.suggestion = (
                "Run `nexus repair --database` (non-destructive) — or restore from backup."
            )
        else:
            # Phase tables presence check (informative, not gate-keeping).
            tables = _db_tables(self.db_path)
            missing = sorted(
                {
                    "strategy_registry",
                    "training_runs",
                    "shadow_runs",
                    "position_lifecycle_events",
                }
                - tables
            )
            if missing:
                entry.reason += f" · phase tables missing: {', '.join(missing)}"
                entry.verdict = "WARNING"
                entry.suggestion = "Run `nexus repair --database` to create missing phase tables."
        return entry

    def check_model(self) -> HealthEntry:
        configured = None
        cfg = self._load_config()
        if cfg is not None and cfg is not False:
            configured = getattr(getattr(cfg, "model", None), "model_artifact_path", None)
        candidate = None
        if configured:
            p = Path(configured)
            candidate = p if p.is_absolute() else self.workspace / p
        if candidate is None or not candidate.exists():
            # Fall back to any artifact under the model dir.
            matches = sorted(self.model_dir.rglob("model.pt")) if self.model_dir.exists() else []
            if matches:
                candidate = matches[0]
        if candidate is None or not candidate.exists():
            return HealthEntry(
                "MODEL",
                "FAIL",
                "no model artifact found (bundled or downloaded)",
                "Run `nexus setup`/`nexus repair --model` to initialize from the release bundle.",
            )
        try:
            import torch  # type: ignore[import-not-found]

            with contextlib.suppress(Exception):
                sd = torch.load(candidate, map_location="cpu", weights_only=True)
                if isinstance(sd, dict):
                    tensors = sd.get("state_dict", sd)
                    n = len(tensors) if isinstance(tensors, dict) else len(sd)
                    return HealthEntry("MODEL", "PASS", f"{candidate} ({n} tensors)")
        except Exception:
            pass
        return HealthEntry(
            "MODEL",
            "WARNING",
            f"artifact exists but could not be introspected: {candidate}",
            "Run `nexus doctor --verbose` for a full trace.",
        )

    def check_feature_schema(self) -> HealthEntry:
        try:
            from nexus_scalp.features.schema import ACTIVE_SCHEMA_ID, FEATURE_SCHEMAS

            schema = FEATURE_SCHEMAS.resolve(ACTIVE_SCHEMA_ID)
            return HealthEntry(
                "FEATURE_SCHEMA",
                "PASS",
                f"{schema.schema_id} / {schema.dimension}D (active contract)",
            )
        except Exception as e:
            return HealthEntry(
                "FEATURE_SCHEMA",
                "FAIL",
                f"schema registry failed: {e}",
                "Reinstall the application bundle.",
            )

    def check_gpu(self) -> HealthEntry:
        env = self.env()
        if env.cuda_available:
            return HealthEntry(
                "GPU", "PASS", f"{env.gpu_name or 'CUDA GPU'} CUDA {env.cuda_version or '?'}"
            )
        if env.gpu_name and "nvidia" in env.gpu_name.lower():
            return HealthEntry(
                "GPU",
                "WARNING",
                f"NVIDIA {env.gpu_name} without CUDA (driver {env.nvidia_driver or '?'})",
                "Update the NVIDIA driver or keep CPU mode.",
            )
        return HealthEntry(
            "GPU", "PASS", f"{env.gpu_name or 'no discrete GPU'} — CPU mode (safe default)"
        )

    def check_mt5(self) -> HealthEntry:
        env = self.env()
        if env.mt5_available:
            return HealthEntry(
                "MT5",
                "PASS",
                "terminal detected"
                if env.mt5_available and env.os_name.lower() == "windows"
                else "native module available",
            )
        return HealthEntry(
            "MT5",
            "WARNING",
            "MetaTrader 5 not detected",
            "Required only for LIVE execution; PAPER/SHADOW work without it.",
        )

    def check_network(self) -> HealthEntry:
        env = self.env()
        if env.network_reachable is True:
            return HealthEntry("NETWORK", "PASS", "outbound HTTPS ok")
        if env.network_reachable is False:
            return HealthEntry(
                "NETWORK",
                "WARNING",
                "no outbound connectivity",
                "News feeds and updates need internet; local features work.",
            )
        return HealthEntry(
            "NETWORK", "UNKNOWN" if False else "WARNING", "connectivity undetermined"
        )

    def check_disk(self) -> HealthEntry:
        env = self.env()
        if not env.free_disk_mb:
            return HealthEntry("DISK", "WARNING", "free disk undetermined")
        if env.free_disk_mb < envmod.MIN_FREE_DISK_MB:
            return HealthEntry(
                "DISK",
                "FAIL",
                f"{env.free_disk_mb} MB free",
                f"Free at least {envmod.MIN_FREE_DISK_MB} MB.",
            )
        if env.free_disk_mb < envmod.RECOMMENDED_FREE_DISK_MB:
            return HealthEntry(
                "DISK",
                "WARNING",
                f"{env.free_disk_mb} MB free",
                f"Recommended >= {envmod.RECOMMENDED_FREE_DISK_MB} MB.",
            )
        return HealthEntry("DISK", "PASS", f"{env.free_disk_mb} MB free")

    def check_memory(self) -> HealthEntry:
        env = self.env()
        if not env.ram_mb:
            return HealthEntry("MEMORY", "WARNING", "RAM undetermined")
        if env.ram_mb < envmod.MIN_RAM_MB:
            return HealthEntry(
                "MEMORY",
                "FAIL",
                f"{env.ram_mb} MB RAM",
                f"Minimum {envmod.MIN_RAM_MB} MB required.",
            )
        if env.ram_mb < envmod.RECOMMENDED_RAM_MB:
            return HealthEntry(
                "MEMORY",
                "WARNING",
                f"{env.ram_mb} MB RAM",
                f"Recommended >= {envmod.RECOMMENDED_RAM_MB} MB.",
            )
        return HealthEntry("MEMORY", "PASS", f"{env.ram_mb} MB RAM")

    def check_logging(self) -> HealthEntry:
        logs = paths.get_logs_dir()
        recent = sorted(logs.glob("*.log")) if logs.exists() else []
        if recent:
            latest = recent[-1]
            try:
                size = latest.stat().st_size
            except OSError:
                size = 0
            return HealthEntry("LOGGING", "PASS", f"{latest.name} ({size} bytes)")
        return HealthEntry(
            "LOGGING",
            "WARNING",
            "no log files yet",
            "Logs appear after the first engine start.",
        )

    def check_workers(self) -> HealthEntry:
        # Worker lifecycle is persisted; a missing checkpoint is not a defect.
        tables = _db_tables(self.db_path)
        known = {
            "intelligence_worker_state",
            "research_worker_state",
        }
        known & tables
        missing = known - tables
        if not missing:
            return HealthEntry("WORKERS", "PASS", "worker checkpoint tables present")
        return HealthEntry(
            "WORKERS",
            "WARNING",
            f"worker checkpoint tables missing: {', '.join(sorted(missing))}",
            "Created automatically on first engine start.",
        )

    def _check_phase(
        self, category: str, tables: set[str], needs: set[str], label: str
    ) -> HealthEntry:
        missing = needs - tables
        if not missing:
            return HealthEntry(category, "PASS", f"{label} tables present")
        return HealthEntry(
            category,
            "WARNING",
            f"{label} tables missing: {', '.join(sorted(missing))}",
            "Created on first engine start; not a blocker.",
        )

    def check_news(self) -> HealthEntry:
        tables = _db_tables(self.news_db_path) if self.news_db_path.exists() else set()
        if not tables:
            return HealthEntry(
                "NEWS",
                "WARNING",
                "news DB not initialized (feature disabled)",
                "Enable `news:` in config and run `nexus repair --news`.",
            )
        return self._check_phase("NEWS", tables, {"articles", "events"}, "News")

    def check_experience(self) -> HealthEntry:
        tables = _db_tables(self.db_path)
        needs = {"audit_experiences", "audit_experience_outcomes", "experience_model_registry"}
        return self._check_phase("EXPERIENCE", tables, needs, "Experience")

    def check_research(self) -> HealthEntry:
        tables = _db_tables(self.db_path)
        needs = {"strategy_registry", "research_runs"}
        return self._check_phase("RESEARCH", tables, needs, "Research")

    def check_training(self) -> HealthEntry:
        tables = _db_tables(self.db_path)
        needs = {"training_runs", "model_comparisons"}
        return self._check_phase("TRAINING", tables, needs, "Training")

    def check_shadow(self) -> HealthEntry:
        tables = _db_tables(self.db_path)
        needs = {"shadow_runs", "shadow_decisions"}
        return self._check_phase("SHADOW", tables, needs, "Shadow")

    def check_accounting(self) -> HealthEntry:
        tables = _db_tables(self.db_path)
        needs = {"audit_ledger", "audit_account_snapshots"}
        return self._check_phase("ACCOUNTING", tables, needs, "Accounting")

    # ------------------------------------------------------------------
    # Orchestration
    # ------------------------------------------------------------------
    def env(self) -> envmod.EnvironmentInfo:
        if self._env is None:
            self._env = envmod.detect_environment()
        return self._env

    def run_all(self) -> list[HealthEntry]:
        checks: list[tuple[str, Callable[[], HealthEntry]]] = [
            ("SYSTEM", self.check_system),
            ("RUNTIME", self.check_runtime),
            ("CONFIGURATION", self.check_configuration),
            ("DATABASE", self.check_database),
            ("MODEL", self.check_model),
            ("FEATURE_SCHEMA", self.check_feature_schema),
            ("GPU", self.check_gpu),
            ("MT5", self.check_mt5),
            ("NETWORK", self.check_network),
            ("DISK", self.check_disk),
            ("MEMORY", self.check_memory),
            ("LOGGING", self.check_logging),
            ("WORKERS", self.check_workers),
            ("NEWS", self.check_news),
            ("EXPERIENCE", self.check_experience),
            ("RESEARCH", self.check_research),
            ("TRAINING", self.check_training),
            ("SHADOW", self.check_shadow),
            ("ACCOUNTING", self.check_accounting),
        ]
        entries: list[HealthEntry] = []
        for _category, fn in checks:
            try:
                entries.append(fn())
            except Exception as e:  # failure isolation
                entries.append(
                    HealthEntry(
                        _category, "FAIL", f"check raised: {e}", "Run `nexus doctor --verbose`."
                    )
                )
        return entries

    def overall(self, entries: list[HealthEntry] | None = None) -> tuple[str, list[HealthEntry]]:
        entries = entries or self.run_all()
        fails = [e for e in entries if e.verdict == "FAIL"]
        critical_fails = [e for e in fails if e.category in CRITICAL_CATEGORIES]
        if critical_fails:
            verdict = "NOT READY"
        elif fails:
            verdict = "DEGRADED"
        else:
            verdict = "READY"
        return verdict, entries

    def summary_dict(self) -> dict[str, Any]:
        verdict, entries = self.overall()
        return {
            "overall": verdict,
            "checks": [e.to_dict() for e in entries],
            "environment": envmod.format_hardware_block(self.env()),
            "version": get_version_info(),
        }


def async_health(check_fns: list[CheckFn]) -> None:
    """Placeholder kept for API symmetry; checks are synchronous by design."""

    async def _runner() -> None:
        await asyncio.sleep(0)

    try:
        asyncio.run(_runner())
    except RuntimeError:
        pass
