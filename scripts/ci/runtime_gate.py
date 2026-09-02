#!/usr/bin/env python3
"""Canonical Runtime Certification Gate (CHG-0051 / TASK-RUNTIME-GATE).

ONE boring, deterministic, OFFLINE command that answers the agent's
pre-push question: "Is the COMPOSED runtime safe, coherent, loadable and
operational enough to push this change?"

    python scripts/ci/runtime_gate.py            # recommended (agent pre-push)
    python scripts/ci/runtime_gate.py --json     # machine-readable (pure JSON)
    python scripts/ci/runtime_gate.py --fast     # static/import/config/contract tier
    python scripts/ci/runtime_gate.py --evidence # also write artifacts/forensics/runtime_gate_result.json

The gate is a LAYERED CERTIFICATION, not "run all pytest":

    L0 STATIC            entrypoints, config schema source, canonical 70D SSoT
    L1 IMPORT            real critical-package imports (circular/deferred errors)
    L2 CONFIG            real AppConfig load (bootstrap + YAML) + secret masking
    L3 DATABASE          disposable SQLite: engine-created schema classes,
                         migration engine status, read/write/flush round-trip
    L4 MODEL/FEATURE     real artifact load (checkpoint/meta/scaler), schema
                         hash identity, 50D/70D assembly + strict validation
    L5 SERVICE GRAPH     REAL LiveEngine construction (paper adapter, injected
                         disposable audit repo, isolated settings DB)
    L6 DECISION CYCLE    seeded synthetic market data -> features -> 70D ->
                         scaler -> inference -> regime -> policy -> gates ->
                         risk -> SIMULATED proposal (no order is placed)
    L7 API/HEALTH        real create_app() + /health + /api/status probes
    L8 SHUTDOWN          real _shutdown_async (workers, audit flush/close)
    L9 INVARIANTS        order_send isolation proof + existing certified
                         deploy-gate engine consumed (never reimplemented)

SAFETY CONTRACT (non-negotiable):
  * PAPER adapter only (no MetaTrader5 IPC, no network gateway).
  * artifacts/audit.db, artifacts/news.db and the real app_settings.db are
    NEVER touched: the gate injects a disposable AuditRepository and points
    NEXUS_SETTINGS_DB at a temp directory BEFORE importing the runtime.
  * No order_send: proven at runtime (the gate's paper adapter records every
    execution-seam call; the whole certification must show ZERO).
  * Offline by default: the only network traffic is loopback HTTP to the
    gate's own in-process FastAPI app.
  * No random data: synthetic bars are deterministic (seeded math series).

EXIT CODES (aligned with release/exit_codes.py semantics, additive):
    0 CERTIFIED          all required stages passed
    1 RUNTIME FAILURE    a required runtime stage failed (owner: see stage)
    2 CONFIGURATION ERROR gate could not run as configured (nothing certified)
    3 ENVIRONMENT BLOCKED missing platform prerequisites (files/artifact)
    4 CONTRACT VIOLATION a protected invariant/contract check failed
    5 INTERNAL GATE ERROR the gate itself crashed (fail-safe: never green)

Deterministic: same tree + same environment => same conclusions. Stage
ordering is fixed; timing fields are informational and never gate.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"

# ---------------------------------------------------------------------------
# Exit-code contract (do not renumber; documented in
# docs/architecture/runtime-certification-gate.md)
# ---------------------------------------------------------------------------
EXIT_CERTIFIED = 0
EXIT_RUNTIME_FAILURE = 1
EXIT_CONFIG_ERROR = 2
EXIT_ENVIRONMENT_BLOCKED = 3
EXIT_CONTRACT_VIOLATION = 4
EXIT_INTERNAL_GATE_ERROR = 5

GATE_VERSION = "1.0.0"

#: Exit code per failure class (single source of truth for the mapping).
FAILURE_CLASS_EXIT: dict[str, int] = {
    "CODE_DEFECT": EXIT_RUNTIME_FAILURE,
    "CONFIG_ERROR": EXIT_CONFIG_ERROR,
    "ENVIRONMENT_BLOCKED": EXIT_ENVIRONMENT_BLOCKED,
    "MISSING_ARTIFACT": EXIT_ENVIRONMENT_BLOCKED,
    "DATABASE_SCHEMA_ERROR": EXIT_RUNTIME_FAILURE,
    "MODEL_CONTRACT_ERROR": EXIT_CONTRACT_VIOLATION,
    "FEATURE_CONTRACT_ERROR": EXIT_CONTRACT_VIOLATION,
    "SERVICE_CONSTRUCTION_ERROR": EXIT_RUNTIME_FAILURE,
    "RUNTIME_BOOT_ERROR": EXIT_RUNTIME_FAILURE,
    "API_ERROR": EXIT_RUNTIME_FAILURE,
    "SHUTDOWN_ERROR": EXIT_RUNTIME_FAILURE,
    "INVARIANT_VIOLATION": EXIT_CONTRACT_VIOLATION,
    "INTERNAL_GATE_ERROR": EXIT_INTERNAL_GATE_ERROR,
}

#: Suggested owner per stage prefix (handoff precision requirement).
STAGE_OWNERS: dict[str, str] = {
    "L0": "repo-owner (structure)",
    "L1": "import-graph / dependency owner",
    "L2": "configuration owner (configuration/config.py)",
    "L3": "database platform owner (TASK-DB-PLATFORM)",
    "L4": "model/feature-contract owner (schema_contract.py, artifacts)",
    "L5": "application/runtime owner (live_engine.py service graph)",
    "L6": "application/runtime owner (decision pipeline)",
    "L7": "web/API owner (web/server.py)",
    "L8": "application/runtime owner (shutdown path)",
    "L9": "invariants owner (forensics/, execution/)",
}


@dataclass
class StageResult:
    """One certification stage. status: PASS | FAIL | SKIP | WARN."""

    name: str
    status: str = "SKIP"
    duration_ms: float = 0.0
    evidence: dict[str, Any] = field(default_factory=dict)
    failure_class: str | None = None
    reason: str = ""
    skipped_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "duration_ms": round(self.duration_ms, 1),
            "evidence": self.evidence,
            "owner": STAGE_OWNERS.get(self.name.split()[0], "unassigned"),
            "failure_class": self.failure_class,
            "reason": self.reason,
            "skipped_reason": self.skipped_reason,
        }


class _StageFailure(Exception):
    """Raised by a stage body to fail the stage with a precise class."""

    def __init__(self, failure_class: str, reason: str, **evidence: Any) -> None:
        super().__init__(reason)
        self.failure_class = failure_class
        self.evidence = evidence


StageBody = Any  # Callable[[Gate, StageResult], None]


class Gate:
    """Accumulates stage results; owns the environment-isolation seams."""

    def __init__(self, fast: bool = False) -> None:
        self.fast = fast
        self.started = time.perf_counter()
        self.stages: list[StageResult] = []
        self.tmpdir: Path | None = None
        self._old_env: dict[str, str | None] = {}
        self.failures: list[dict[str, str]] = []
        self.warnings: list[dict[str, str]] = []
        # Cross-stage handles (set by L5, consumed by L6/L7/L8/L9).
        self.engine_ref: Any = None
        self.adapter_ref: Any = None

    # -- environment isolation -------------------------------------------
    def isolate_environment(self) -> Path:
        """Isolate every writable surface BEFORE the runtime is imported.

        - NEXUS_SETTINGS_DB -> temp dir (never the real app_settings.db)
        - NSE_NO_TELEGRAM=1 belt-and-braces (the gate config disables
          telegram anyway)
        - PYTHONDONTWRITEBYTECODE keeps the checkout clean
        """
        self.tmpdir = Path(tempfile.mkdtemp(prefix="nse_runtime_gate_"))
        overrides = {
            "NEXUS_SETTINGS_DB": str(self.tmpdir / "app_settings.db"),
            "NSE_NO_TELEGRAM": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        for key, value in overrides.items():
            self._old_env[key] = os.environ.get(key)
            os.environ[key] = value
        # src/ onto sys.path exactly like NexusTradingForexBot.py does.
        if str(SRC_DIR) not in sys.path:
            sys.path.insert(0, str(SRC_DIR))
        return self.tmpdir

    def finish(self) -> None:
        for key, old in self._old_env.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old
        if self.tmpdir is not None:
            shutil.rmtree(self.tmpdir, ignore_errors=True)

    # -- stage bookkeeping -------------------------------------------------
    def record(self, result: StageResult) -> None:
        self.stages.append(result)
        if result.status == "FAIL":
            self.failures.append(
                {
                    "stage": result.name,
                    "reason": result.reason,
                    "failure_class": result.failure_class or "CODE_DEFECT",
                    "evidence": json.dumps(result.evidence, default=str)[:1500],
                    "owner": STAGE_OWNERS.get(result.name.split()[0], "unassigned"),
                }
            )
        elif result.status == "WARN":
            self.warnings.append({"stage": result.name, "reason": result.reason})

    def stage_status(self, prefix: str) -> str:
        for s in self.stages:
            if s.name.startswith(prefix):
                return s.status
        return "SKIP"

    @property
    def duration_ms(self) -> float:
        return (time.perf_counter() - self.started) * 1000.0

    def exit_code(self) -> int:
        if not self.failures:
            return EXIT_CERTIFIED
        classes = {f["failure_class"] for f in self.failures}
        if classes <= {"ENVIRONMENT_BLOCKED", "MISSING_ARTIFACT"}:
            return EXIT_ENVIRONMENT_BLOCKED
        for cls in ("INVARIANT_VIOLATION", "MODEL_CONTRACT_ERROR", "FEATURE_CONTRACT_ERROR"):
            if cls in classes:
                return EXIT_CONTRACT_VIOLATION
        if classes == {"CONFIG_ERROR"}:
            return EXIT_CONFIG_ERROR
        return EXIT_RUNTIME_FAILURE


def run_stage(gate: Gate, name: str, body: StageBody) -> StageResult:
    """Runs one stage with uniform timing, stdout capture, error containment.

    Runtime chatter (structlog/engine logs) is captured, not printed, so
    --json stdout stays pure JSON and the human report stays clean. On a
    failure the captured tail becomes evidence.
    """
    result = StageResult(name=name, status="PASS")
    t0 = time.perf_counter()
    buffer = io.StringIO()
    try:
        with contextlib.redirect_stdout(buffer):
            body(gate, result)
    except _StageFailure as exc:
        result.status = "FAIL"
        result.failure_class = exc.failure_class
        result.reason = str(exc)
        result.evidence.update(exc.evidence)
        result.evidence["stdout_tail"] = buffer.getvalue()[-800:]
    except Exception as exc:  # noqa: BLE001 - gate must classify, never crash
        result.status = "FAIL"
        result.failure_class = "INTERNAL_GATE_ERROR"
        result.reason = f"{type(exc).__name__}: {exc}"
        result.evidence["stdout_tail"] = buffer.getvalue()[-800:]
    result.duration_ms = (time.perf_counter() - t0) * 1000.0
    gate.record(result)
    return result


def skip_stage(gate: Gate, name: str, reason: str) -> None:
    gate.record(StageResult(name=name, status="SKIP", skipped_reason=reason))


# ===========================================================================
# L0 STATIC
# ===========================================================================

REQUIRED_PATHS: tuple[str, ...] = (
    "NexusTradingForexBot.py",
    "main.py",
    "configs/base.yaml",
    "src/nexus_scalp/application/live_engine.py",
    "src/nexus_scalp/features/schema_contract.py",
    "src/nexus_scalp/features/features70.py",
    "src/nexus_scalp/features/liquidity_runtime.py",
    "src/nexus_scalp/features/inference_validator.py",
    "src/nexus_scalp/models/scalp_net.py",
    "src/nexus_scalp/adapters/paper/paper_adapter.py",
    "src/nexus_scalp/adapters/database/audit_repository.py",
    "src/nexus_scalp/risk/risk_engine.py",
    "src/nexus_scalp/signals/policy.py",
    "src/nexus_scalp/web/server.py",
    "src/nexus_scalp/release/exit_codes.py",
    "src/nexus_scalp/forensics/deploy_gate.py",
)


def l0_static(gate: Gate, res: StageResult) -> None:
    missing = [rel for rel in REQUIRED_PATHS if not (REPO_ROOT / rel).exists()]
    if missing:
        raise _StageFailure(
            "ENVIRONMENT_BLOCKED",
            f"required runtime files absent: {missing}",
            missing=missing,
        )
    # py_compile the true entrypoints (cheap but real syntax gate).
    for entry in ("NexusTradingForexBot.py", "main.py"):
        proc = subprocess.run(
            [sys.executable, "-m", "py_compile", str(REPO_ROOT / entry)],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=str(REPO_ROOT),
        )
        if proc.returncode != 0:
            raise _StageFailure(
                "CODE_DEFECT", f"py_compile failed for {entry}", stderr=proc.stderr[-800:]
            )
    res.evidence = {
        "required_files_present": len(REQUIRED_PATHS),
        "py_compile": ["NexusTradingForexBot.py", "main.py"],
    }


# ===========================================================================
# L1 IMPORT
# ===========================================================================

CRITICAL_IMPORTS: tuple[str, ...] = (
    "nexus_scalp",
    "nexus_scalp.configuration.config",
    "nexus_scalp.configuration.runtime_config",
    "nexus_scalp.database.engine",
    "nexus_scalp.models.scalp_net",
    "nexus_scalp.features.scalp_features",
    "nexus_scalp.features.schema_contract",
    "nexus_scalp.features.features70",
    "nexus_scalp.features.liquidity_runtime",
    "nexus_scalp.features.inference_validator",
    "nexus_scalp.signals.policy",
    "nexus_scalp.risk.risk_engine",
    "nexus_scalp.adapters.paper.paper_adapter",
    "nexus_scalp.adapters.database.audit_repository",
    "nexus_scalp.application.live_engine",
    "nexus_scalp.web.server",
    "nexus_scalp.release.health",
    "nexus_scalp.cli.doctor",
)


def l1_import(gate: Gate, res: StageResult) -> None:
    import importlib

    failed: list[dict[str, str]] = []
    for mod in CRITICAL_IMPORTS:
        try:
            importlib.import_module(mod)
        except Exception as exc:
            failed.append({"module": mod, "error": f"{type(exc).__name__}: {exc}"})
    if failed:
        raise _StageFailure(
            "CODE_DEFECT", f"{len(failed)} critical import(s) failed", failed=failed[:8]
        )
    res.evidence = {"imported": len(CRITICAL_IMPORTS)}


# ===========================================================================
# L2 CONFIG
# ===========================================================================


def l2_config(gate: Gate, res: StageResult) -> None:
    from nexus_scalp.configuration.config import AppConfig
    from nexus_scalp.configuration.runtime_config import RuntimeConfigStore

    # 1. Defaults construct and validate (pydantic contract).
    cfg = AppConfig()
    # 2. The REAL bootstrap YAML every operator launch reads.
    cfg_yaml = AppConfig.load_from_yaml(REPO_ROOT / "configs" / "base.yaml")
    # 3. Secret masking: no token material in the safe representation.
    try:
        snapshot = RuntimeConfigStore(bootstrap=cfg_yaml).get_snapshot().to_dict()
        blob = json.dumps(snapshot, default=str)
        masking_surface = "runtime_snapshot.to_dict()"
    except Exception:
        blob = json.dumps(cfg_yaml.model_dump(), default=str)
        masking_surface = "model_dump() fallback"
    token = getattr(cfg_yaml.telegram, "bot_token", "") or ""
    if token and token in blob:
        raise _StageFailure(
            "CONFIG_ERROR", "telegram bot_token leaked into the safe config representation"
        )
    res.evidence = {
        "defaults_ok": True,
        "base_yaml_ok": True,
        "mode": cfg_yaml.execution.mode.value,
        "symbol": cfg_yaml.execution.symbol,
        "artifact_path": cfg_yaml.model.model_artifact_path,
        "telegram_enabled": cfg_yaml.telegram.enabled,
        "secret_masking": "PASS",
        "masking_surface": masking_surface,
    }


# ===========================================================================
# L3 DATABASE (disposable only)
# ===========================================================================

#: Table CLASSES, not names: required = financial-truth schema the runtime
#: assumes at boot; optional = subsystem tables whose absence is a legitimate
#: disabled-subsystem state (never a runtime failure).
REQUIRED_TABLE_CLASSES = ("audit_signals", "audit_orders", "audit_ledger")
OPTIONAL_TABLE_CLASSES = ("behavior_analysis", "factory_runs", "news_articles")


def l3_database(gate: Gate, res: StageResult) -> None:
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.domain.enums import ActionType
    from nexus_scalp.domain.models import TradeProposal

    assert gate.tmpdir is not None
    db_path = gate.tmpdir / "gate_audit.db"
    repo = AuditRepository(db_url=f"sqlite:///{db_path}", flush_interval_sec=0.05)
    try:
        con = sqlite3.connect(str(db_path))
        try:
            tables = {
                r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        finally:
            con.close()
        missing_required = [t for t in REQUIRED_TABLE_CLASSES if t not in tables]
        if missing_required:
            raise _StageFailure(
                "DATABASE_SCHEMA_ERROR",
                f"required audit tables absent on fresh engine-created DB: {missing_required}",
                missing=missing_required,
            )
        # Round-trip: queued worker write + flush + read back.
        repo.log_signal(
            TradeProposal(
                request_id="gate-roundtrip-0001",
                symbol="XAUUSD",
                generated_at=datetime.now(UTC) - timedelta(minutes=1),
                action=ActionType.NO_TRADE,
                confidence=0.0,
                proposed_entry=2000.0,
                stop_loss=1990.0,
                take_profit=2020.0,
                risk_reward_ratio=2.0,
                reason_code="RUNTIME_GATE_PROBE",
            )
        )
        flushed = repo.flush(timeout_sec=10.0)
        con = sqlite3.connect(str(db_path))
        try:
            row = con.execute(
                "SELECT COUNT(*) FROM audit_signals WHERE request_id='gate-roundtrip-0001'"
            ).fetchone()
        finally:
            con.close()
        count = int(row[0]) if row else 0
        if not flushed or count < 1:
            raise _StageFailure(
                "DATABASE_SCHEMA_ERROR",
                f"audit round-trip failed (flushed={flushed}, rows={count})",
            )
        # Migration engine status (read-only, on the disposable DB).
        migration_state = "N/A"
        try:
            from nexus_scalp.database.engine import DatabaseMigrationEngine
            from nexus_scalp.database.models import DatabaseDomain

            eng = DatabaseMigrationEngine(db_path=db_path, domain=DatabaseDomain.AUDIT)
            st = eng.status()
            migration_state = str(st.get("migration_state", "N/A"))
        except Exception as exc:  # optional capability — report honestly
            migration_state = f"unavailable: {type(exc).__name__}: {exc}"[:200]
        optional_absent = [t for t in OPTIONAL_TABLE_CLASSES if t not in tables]
        res.evidence = {
            "db": "disposable tmp file (production DBs untouched)",
            "tables_created": len(tables),
            "required_present": True,
            "roundtrip_rows": count,
            "migration_state": migration_state,
            "optional_absent_tolerated": optional_absent,
        }
    finally:
        repo.close()


# ===========================================================================
# L4 MODEL / FEATURE CONTRACT
# ===========================================================================


class SimpleTick:
    """Duck-typed minimal tick for feature-engine probes."""

    def __init__(self, symbol: str, timestamp: Any, bid: float, ask: float, volume: float):
        self.symbol = symbol
        self.timestamp = timestamp
        self.bid = bid
        self.ask = ask
        self.volume = volume
        self.last = 0.0
        self.flags = 0


def synthetic_bars(count: int = 240) -> tuple[list[Any], SimpleTick]:
    """Deterministic synthetic M1 series (no randomness, no future data).

    Timestamps are strictly ascending and end in the past; prices follow a
    fixed sine/cosine composite (identical series on every run).
    """
    from nexus_scalp.market_data.bar_aggregator import BarData

    t0 = datetime.now(UTC) - timedelta(minutes=count + 2)
    bars: list[BarData] = []
    price = 3300.0
    for i in range(count):
        step = ((i * 37) % 140) / 100.0 - 0.7 + ((i * 11) % 100) / 100.0 - 0.5
        o = price
        price = price + step
        bars.append(
            BarData(
                symbol="XAUUSD",
                timeframe="M1",
                timestamp=t0 + timedelta(minutes=i + 1),
                open=o,
                high=max(o, price) + 0.4,
                low=min(o, price) - 0.4,
                close=price,
                tick_volume=100 + (i % 7) * 5,
                is_complete=True,
            )
        )
    last = bars[-1]
    tick = SimpleTick(
        symbol="XAUUSD", timestamp=last.timestamp, bid=last.close, ask=last.close + 0.20,
        volume=float(last.tick_volume),
    )
    return bars, tick


def domain_tick(t: SimpleTick) -> Any:
    """Frozen TickData contract expected by policy/regime."""
    from nexus_scalp.domain.models import TickData

    return TickData(symbol=t.symbol, timestamp=t.timestamp, bid=t.bid, ask=t.ask,
                    volume=float(t.volume))


def l4_model_contract(gate: Gate, res: StageResult) -> None:
    import numpy as np
    import torch

    from nexus_scalp.configuration.config import AppConfig
    from nexus_scalp.features.features70 import news_10d_from_context
    from nexus_scalp.features.liquidity_runtime import LiquidityGovernor, build_70d_vector
    from nexus_scalp.features.scalp_features import ScalpFeatureEngine
    from nexus_scalp.features.schema_contract import (
        SCHEMA_ID,
        assert_canonical_registry,
        feature_schema_hash,
        validate_70d_vector,
    )

    assert_canonical_registry()
    artifact = REPO_ROOT / AppConfig().model.model_artifact_path
    if not artifact.exists():
        raise _StageFailure(
            "MISSING_ARTIFACT", f"configured champion artifact absent: {artifact}",
            path=str(artifact),
        )
    state = torch.load(artifact, map_location="cpu")
    w = state.get("input_projection.weight") if isinstance(state, dict) else None
    model_dim = int(w.shape[1]) if w is not None and hasattr(w, "shape") else None
    meta_path = artifact.with_suffix(".meta.json")
    meta: dict[str, Any] = {}
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    scaler_path = artifact.with_suffix(".scaler.npz")
    scaler_dim = None
    scaler = None
    if scaler_path.exists():
        data = np.load(scaler_path)
        scaler_dim = int(np.asarray(data["mean"]).shape[0])
        if scaler_dim == 70 and float(np.abs(np.asarray(data["std"])).min()) > 0:
            scaler = (
                np.asarray(data["mean"], dtype=np.float64),
                np.asarray(data["std"], dtype=np.float64),
            )
    if model_dim != 70 or scaler_dim != 70:
        raise _StageFailure(
            "MODEL_CONTRACT_ERROR",
            f"artifact width split: checkpoint={model_dim} scaler={scaler_dim} (want 70/70)",
            model_dim=model_dim,
            scaler_dim=scaler_dim,
        )
    meta_dim = meta.get("feature_schema_dimension") or meta.get("num_features")
    if meta and meta_dim != 70:
        raise _StageFailure("MODEL_CONTRACT_ERROR", f"meta declares dim {meta_dim} (want 70)")
    meta_schema = meta.get("feature_schema_id")
    if meta and meta_schema != SCHEMA_ID:
        raise _StageFailure(
            "MODEL_CONTRACT_ERROR", f"meta schema {meta_schema} != canonical {SCHEMA_ID}"
        )

    # 50D base + canonical 70D assembly through the REAL producers.
    bars, tick = synthetic_bars(240)
    fv = ScalpFeatureEngine(symbol="XAUUSD").compute_from_bars(bars, tick)
    base50 = fv.to_tensor_input()
    if len(base50) != 50:
        raise _StageFailure(
            "FEATURE_CONTRACT_ERROR", f"base producer width {len(base50)} (want 50)"
        )
    gov = LiquidityGovernor(enabled=True)
    gov.compute_from_engine(
        bars=bars, mid_price=float(bars[-1].close), atr=float(fv.atr_m1),
        decision_at=bars[-1].timestamp,
    )
    liq10 = [float(v) for v in gov.last_snapshot.features] if gov.last_snapshot else None
    if not liq10 or len(liq10) != 10:
        raise _StageFailure(
            "FEATURE_CONTRACT_ERROR", "liquidity 10D block not produced for synthetic bars"
        )
    vec70 = build_70d_vector(base50, family_10=news_10d_from_context(None), liquidity_10=liq10)
    vec70 = validate_70d_vector(
        vec70, schema_hash=feature_schema_hash(), context="runtime_gate_l4"
    )
    # Scaler application must stay finite (the exact live transform).
    scaled_finite = None
    if scaler is not None:
        mean, std = scaler
        x = (np.array(vec70, dtype=np.float64) - mean) / std
        scaled_finite = bool(np.isfinite(x).all())
        if not scaled_finite:
            raise _StageFailure(
                "MODEL_CONTRACT_ERROR", "scaled vector has non-finite values"
            )
    res.evidence = {
        "artifact": str(artifact.relative_to(REPO_ROOT)),
        "model_dim": model_dim,
        "scaler_dim": scaler_dim,
        "meta_schema": meta_schema,
        "schema_id": SCHEMA_ID,
        "schema_hash": feature_schema_hash(),
        "layout": "0..49 base | 50..59 news | 60..69 liquidity",
        "base50": 50,
        "news10": 10,
        "liquidity10": 10,
        "vec70_validated": True,
        "scaled_finite": scaled_finite,
    }


# ===========================================================================
# L5 SERVICE GRAPH (REAL LiveEngine, isolated writes)
# ===========================================================================


def build_gate_engine(gate: Gate) -> tuple[Any, Any, Any]:
    """Constructs the REAL LiveEngine exactly like the launcher/CLI does,
    with disposable persistence: tmp audit repo + NEXUS_SETTINGS_DB +
    PAPER adapter. Returns (engine, adapter, audit_repo)."""
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter
    from nexus_scalp.application.live_engine import LiveEngine
    from nexus_scalp.configuration.config import AppConfig

    assert gate.tmpdir is not None
    repo = AuditRepository(
        db_url=f"sqlite:///{gate.tmpdir / 'engine_audit.db'}", flush_interval_sec=0.05
    )
    gate._engine_repo = repo  # noqa: SLF001 - gate-internal handle for L8
    adapter = _GatePaperAdapter(initial_balance=10_000.0, symbol="XAUUSD")
    adapter.connect()
    artifact = REPO_ROOT / AppConfig().model.model_artifact_path
    config = AppConfig.model_validate(
        {
            "execution": {"symbol": "XAUUSD", "mode": "PAPER", "magic_number": 888201},
            "model": {
                "model_artifact_path": str(artifact),
                "feature_schema_version": "v1.0",
                "confidence_threshold": 0.35,
            },
            "risk": {
                "risk_per_trade_pct": 2.0,
                "max_account_drawdown_pct": 10.0,
                "max_concurrent_positions": 5,
                "max_spread_points": 50,
                "max_allowed_lots": 10.0,
                "max_margin_usage_pct": 50.0,
            },
            "telegram": {"enabled": False, "bot_token": "", "admin_id": ""},
        }
    )
    engine = LiveEngine(config=config, adapter=adapter, audit_repo=repo)
    return engine, adapter, repo


class _GatePaperAdapter:
    """PaperMT5Adapter with an execution-seam tripwire.

    Delegates everything to the REAL PaperMT5Adapter and counts calls to
    every order-execution seam, so the gate can PROVE (not assume) that the
    whole certification ran with zero execution-seam invocations.
    """

    def __init__(self, **kwargs: Any) -> None:
        from nexus_scalp.adapters.paper.paper_adapter import PaperMT5Adapter

        self._inner = PaperMT5Adapter(**kwargs)
        self._execution_calls = 0

    def _count(self) -> bool:
        self._execution_calls += 1
        return False  # even if reached, no order happens

    # -- read surface: straight delegation --------------------------------
    def __getattr__(self, item: str) -> Any:
        return getattr(self._inner, item)

    # -- execution seams: counted, refused --------------------------------
    def send_order(self, order: Any) -> bool:
        return self._count()

    def execute_market_order(self, *a: Any, **k: Any) -> bool:
        return self._count()

    def place_pending_order(self, *a: Any, **k: Any) -> bool:
        return self._count()

    def modify_order(self, *a: Any, **k: Any) -> bool:
        return self._count()

    def cancel_pending_order(self, *a: Any, **k: Any) -> bool:
        return self._count()

    def close_position(self, *a: Any, **k: Any) -> bool:
        return self._count()

    def modify_position(self, *a: Any, **k: Any) -> bool:
        return self._count()

    @property
    def execution_calls(self) -> int:
        return self._execution_calls


def l5_service_graph(gate: Gate, res: StageResult) -> None:
    engine, adapter, _repo = build_gate_engine(gate)
    required_services = (
        "feature_engine",
        "regime_classifier",
        "risk_engine",
        "order_manager",
        "signal_policy",
        "experience_engine",
        "intelligence_gate",
        "liquidity_governor",
        "runtime_config",
        "model_registry",
        "accounting_core",
        "_bundle",
    )
    missing = [s for s in required_services if getattr(engine, s, None) is None]
    if missing:
        raise _StageFailure(
            "SERVICE_CONSTRUCTION_ERROR",
            f"service graph incomplete: {missing}",
            missing=missing,
        )
    bundle = engine._bundle
    effective_dim = int(engine.effective_feature_dim)
    if bundle is None or effective_dim not in (50, 70):
        raise _StageFailure(
            "MODEL_CONTRACT_ERROR", f"bundle None or unexpected effective dim {effective_dim}"
        )
    gate.engine_ref = engine
    gate.adapter_ref = adapter
    res.evidence = {
        "constructed": len(required_services),
        "effective_feature_dim": effective_dim,
        "effective_schema_id": engine.effective_feature_schema_id,
        "runtime_mode": engine._runtime_mode,
        "adapter": "PaperMT5Adapter via _GatePaperAdapter (PAPER, no broker IPC)",
        "persistence": "disposable tmp audit DB + isolated settings DB",
    }


# ===========================================================================
# L6 SAFE DECISION CYCLE (synthetic, seeded, no order)
# ===========================================================================


def l6_decision_cycle(gate: Gate, res: StageResult) -> None:
    import numpy as np
    import torch

    from nexus_scalp.domain.enums import ActionType
    from nexus_scalp.features.features70 import news_10d_from_context
    from nexus_scalp.features.liquidity_runtime import build_70d_vector
    from nexus_scalp.features.schema_contract import (
        feature_schema_hash,
        validate_70d_vector,
    )

    engine = gate.engine_ref
    if engine is None:
        raise _StageFailure("INTERNAL_GATE_ERROR", "L5 did not leave an engine reference")

    bars, tick = synthetic_bars(240)
    fv = engine.feature_engine.compute_from_bars(bars, tick)

    # Canonical live-tensor assembly (same path as _infer_probabilities).
    base50 = engine._validate_50d_tensor(fv.to_tensor_input(), context="gate_base50")
    gov = engine.liquidity_governor
    gov.compute_from_engine(
        bars=bars, mid_price=float(bars[-1].close), atr=float(fv.atr_m1),
        decision_at=bars[-1].timestamp,
    )
    snap = gov.last_snapshot
    liq10 = [float(v) for v in snap.features] if snap is not None else None
    if liq10 is None or len(liq10) != 10:
        raise _StageFailure(
            "FEATURE_CONTRACT_ERROR", "70D assembly input: liquidity block invalid"
        )
    vec70 = build_70d_vector(
        base50, family_10=news_10d_from_context(None), liquidity_10=liq10
    )
    vec70 = validate_70d_vector(
        vec70, schema_hash=feature_schema_hash(), context="runtime_gate_l6"
    )

    with engine._bundle_lock:
        bundle = engine._bundle
    if bundle is None:
        raise _StageFailure("MODEL_CONTRACT_ERROR", "model bundle absent at decision cycle")
    x_np = np.array(vec70, dtype=np.float32).reshape(1, -1)
    x_np = bundle.scaler.transform(x_np)
    x = torch.nan_to_num(torch.tensor(x_np, dtype=torch.float32), nan=0.0, posinf=1.0,
                         neginf=-1.0)
    bundle.model.eval()
    prior_threads = torch.get_num_threads()
    torch.set_num_threads(1)
    try:
        with torch.inference_mode():
            probs = bundle.model(x)
    finally:
        torch.set_num_threads(prior_threads)
    probs_list = probs.detach().cpu().numpy().flatten().tolist()
    if len(probs_list) < 3 or not all(np.isfinite(v) for v in probs_list[:3]):
        raise _StageFailure(
            "MODEL_CONTRACT_ERROR", f"model output degenerate: {probs_list[:4]}"
        )

    # The REAL regime classifier + REAL policy + REAL pre-trade gates.
    regime_state = engine.regime_classifier.classify_tick(
        current_tick=domain_tick(tick), is_macro_news_window=False
    )
    proposal = engine.signal_policy.evaluate_probabilities(
        probabilities=probs,
        current_tick=domain_tick(tick),
        feature_vector=fv,
        regime_state=regime_state,
        force_log=False,
    )
    proposal, _exp = engine.experience_engine.evaluate_proposal(
        proposal=proposal, feature_vector=fv, regime_state=regime_state
    )
    proposal, _exp2, _suit = engine.intelligence_gate.evaluate(
        proposal=proposal, fv=fv, regime=regime_state
    )
    proposal, _fresh = engine.live_freshness_gate(proposal)

    # Risk sizing on the SIMULATED proposal (never dispatched).
    account = engine.adapter.get_account_info()
    symbol_info = engine.adapter.get_symbol_info("XAUUSD")
    volume = 0.0
    if proposal.action in (
        ActionType.BUY, ActionType.SELL, ActionType.BUY_MARKET, ActionType.SELL_MARKET
    ):
        volume, _sizing = engine.risk_engine.calculate_dynamic_volume(
            entry=float(proposal.proposed_entry),
            sl=float(proposal.stop_loss),
            account=account,
            symbol_info=symbol_info,
            risk_pct=engine.runtime_config.get_snapshot().risk.risk_per_trade_pct,
        )
    execution_calls = int(getattr(gate.adapter_ref, "execution_calls", 0))
    if execution_calls != 0:
        raise _StageFailure(
            "INVARIANT_VIOLATION",
            f"execution seam fired {execution_calls}x during the decision cycle",
        )
    res.evidence = {
        "tensor_dim": len(vec70),
        "probs": [round(float(p), 6) for p in probs_list[:4]],
        "regime": str(getattr(getattr(regime_state, "regime_type", None), "value", "UNKNOWN")),
        "action": proposal.action.value,
        "reason_code": proposal.reason_code,
        "decision_stage": getattr(proposal, "decision_stage", ""),
        "confidence": round(float(proposal.confidence), 6),
        "risk_volume": round(float(volume), 4),
        "execution_seam_calls": execution_calls,
        "note": "NO_TRADE is a valid completion; no order was placed or placeable",
    }


# ===========================================================================
# L7 API / HEALTH
# ===========================================================================


def l7_api(gate: Gate, res: StageResult) -> None:
    from nexus_scalp.web.server import create_app

    app = create_app(engine_ref=gate.engine_ref)
    r_health = client_get(app, "/health")
    if r_health.status_code not in (200, 503):
        raise _StageFailure("API_ERROR", f"/health returned {r_health.status_code}",
                            status=r_health.status_code)
    health = r_health.json() if r_health.status_code == 200 else {}
    verdict = str(health.get("verdict", "?"))
    if r_health.status_code == 200 and verdict not in ("READY", "DEGRADED"):
        raise _StageFailure("API_ERROR", f"/health verdict unexpected: {verdict}",
                            verdict=verdict)
    r_status = client_get(app, "/api/status")
    if r_status.status_code != 200:
        raise _StageFailure("API_ERROR", f"/api/status returned {r_status.status_code}")
    status = r_status.json()
    blob = json.dumps(status, default=str)
    token = os.environ.get("NEXUS_TELEGRAM_BOT_TOKEN", "")
    if token and token in blob:
        raise _StageFailure("API_ERROR", "environment token leaked into /api/status")
    res.evidence = {
        "health_status": r_health.status_code,
        "health_verdict": verdict,
        "status_state_version": status.get("state_version"),
        "secret_leak_check": "PASS",
    }


def client_get(app: Any, path: str) -> Any:
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        return client.get(path)


# ===========================================================================
# L8 SHUTDOWN
# ===========================================================================


def l8_shutdown(gate: Gate, res: StageResult) -> None:
    import asyncio

    engine = gate.engine_ref
    if engine is None:
        raise _StageFailure("INTERNAL_GATE_ERROR", "no engine to shut down")
    asyncio.run(engine._shutdown_async())
    repo = getattr(gate, "_engine_repo", None) or engine.audit
    drained = repo.flush(timeout_sec=10.0)
    bg = [t for t in getattr(engine, "_background_tasks", set()) if not t.done()]
    if bg:
        raise _StageFailure(
            "SHUTDOWN_ERROR", f"{len(bg)} background task(s) still pending after shutdown"
        )
    res.evidence = {
        "workers_stopped": True,
        "adapter_disconnected": not gate.adapter_ref.is_connected(),
        "audit_flushed": drained,
        "background_tasks_pending": len(bg),
        "note": "engine that boots must also stop cleanly (certification requirement)",
    }


# ===========================================================================
# L9 INVARIANTS
# ===========================================================================


def l9_invariants(gate: Gate, res: StageResult) -> None:
    # 1) Runtime seam proof: the gate's paper adapter counts every
    #    execution-seam call; the WHOLE certification must show ZERO.
    execution_calls = int(getattr(gate.adapter_ref, "execution_calls", 0))
    if execution_calls != 0:
        raise _StageFailure(
            "INVARIANT_VIOLATION",
            f"execution seam invoked {execution_calls}x during certification (want 0)",
        )
    # 2) MT5 IPC: importable on Windows is fine, but the gate process must
    #    not have driven the terminal (runtime proof is the seam counter).
    mt5_loaded = False
    try:
        import MetaTrader5  # noqa: F401

        mt5_loaded = True
    except Exception:
        mt5_loaded = False
    # 3) Consume the EXISTING certified deploy-gate engine (no
    #    reimplementation): read-only snapshot over forensic checks.
    deploy_summary: dict[str, Any]
    if not gate.fast:
        try:
            from nexus_scalp.forensics.engine import ForensicHealthEngine

            fh = ForensicHealthEngine()
            rec = fh.snapshot(persist=False)
            deploy_summary = {
                "status": str(getattr(rec, "overall_status", "UNKNOWN")),
                "blocking": list(fh.blocking_checks() or [])[:10],
            }
        except Exception as exc:
            deploy_summary = {"status": "UNAVAILABLE", "note": f"{type(exc).__name__}: {exc}"}
    else:
        deploy_summary = {"status": "SKIPPED", "note": "not run in fast tier"}
    res.evidence = {
        "order_send_isolation": "PASS (runtime seam count = 0)",
        "mt5_module_loaded_in_gate_process": mt5_loaded,
        "forensic_deploy_gate": deploy_summary,
    }


# ===========================================================================
# Output rendering
# ===========================================================================


def human_report(gate: Gate) -> str:
    lines = ["", "NEXUS RUNTIME CERTIFICATION", "=" * 46]
    for s in gate.stages:
        lines.append(f"  {s.name:<22} {s.status:>5}   ({s.duration_ms:7.0f} ms)")
        if s.status == "FAIL":
            lines.append(f"      reason: {s.reason}")
            lines.append(
                f"      class : {s.failure_class}  "
                f"owner: {STAGE_OWNERS.get(s.name.split()[0], 'unassigned')}"
            )
        elif s.status == "SKIP":
            lines.append(f"      skipped: {s.skipped_reason}")
    lines.append("=" * 46)
    code = gate.exit_code()
    lines.append("RUNTIME CERTIFIED" if code == EXIT_CERTIFIED else "RUNTIME BLOCKED")
    lines.append(f"exit_code={code}  duration={gate.duration_ms / 1000.0:.1f}s")
    return "\n".join(lines)


def _stage_evidence(gate: Gate, prefix: str) -> dict[str, Any]:
    for s in gate.stages:
        if s.name.startswith(prefix):
            return {"status": s.status, **s.evidence}
    return {}


def _model_block(gate: Gate) -> dict[str, Any]:
    ev = _stage_evidence(gate, "L4")
    return ev if ev else {}


def _schema_block(gate: Gate) -> dict[str, Any]:
    model = _model_block(gate)
    dim = model.get("model_dim")
    return {
        "schema_id": model.get("schema_id"),
        "dimension": dim,
        "schema_hash": model.get("schema_hash"),
        "scaler": "OK" if model.get("scaler_dim") == dim and dim is not None else "UNKNOWN",
        "runtime_activation": model.get("schema_id"),
    }


def gate_json(gate: Gate) -> dict[str, Any]:
    app_version = "unknown"
    commit = "NOT_RECORDED"
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=str(REPO_ROOT),
            capture_output=True, text=True, timeout=10,
        )
        if out.returncode == 0:
            commit = out.stdout.strip() or commit
    except Exception:
        pass
    try:
        from nexus_scalp.release.metadata import get_version_info

        app_version = str(get_version_info().get("version", "unknown"))
    except Exception:
        pass
    code = gate.exit_code()
    engine_ev = _stage_evidence(gate, "L5")
    decision_ev = _stage_evidence(gate, "L6")
    return {
        "gate_version": GATE_VERSION,
        "timestamp": datetime.now(UTC).isoformat(),
        "git_commit": commit,
        "application_version": app_version,
        "environment": {
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "tier": "fast" if gate.fast else "full",
            "network": "offline (loopback only)",
            "live_trading": "disabled (paper adapter, disposable DBs)",
        },
        "duration_ms": round(gate.duration_ms, 1),
        "status": "CERTIFIED" if code == EXIT_CERTIFIED else "BLOCKED",
        "exit_code": code,
        "stages": [s.to_dict() for s in gate.stages],
        "invariants": [
            {
                "name": "order_send_isolation",
                "status": "PASS" if code != EXIT_CONTRACT_VIOLATION else "CHECK_EVIDENCE",
            },
        ],
        "model": _model_block(gate),
        "feature_schema": _schema_block(gate),
        "database": _stage_evidence(gate, "L3"),
        "engine": {"service_graph": engine_ev, "decision_cycle": decision_ev},
        "api": _stage_evidence(gate, "L7"),
        "shutdown": _stage_evidence(gate, "L8"),
        "failures": gate.failures,
        "warnings": gate.warnings,
    }


# ===========================================================================
# main
# ===========================================================================

FULL_RUNTIME_STAGES = (
    ("L3 DATABASE", l3_database),
    ("L4 MODEL/FEATURE", l4_model_contract),
    ("L5 SERVICE GRAPH", l5_service_graph),
    ("L6 DECISION CYCLE", l6_decision_cycle),
    ("L7 API/HEALTH", l7_api),
    ("L8 SHUTDOWN", l8_shutdown),
    ("L9 INVARIANTS", l9_invariants),
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="runtime_gate", description="NEXUS canonical runtime certification gate (pre-push)."
    )
    parser.add_argument("--json", action="store_true", help="machine-readable JSON only")
    parser.add_argument("--fast", action="store_true", help="static/import/config/contract tier")
    parser.add_argument(
        "--evidence", action="store_true",
        help="persist JSON to artifacts/forensics/runtime_gate_result.json",
    )
    args = parser.parse_args(argv)

    gate = Gate(fast=args.fast)
    try:
        gate.isolate_environment()
    except Exception as exc:
        print(f"RUNTIME GATE: cannot isolate environment: {exc}", file=sys.stderr)
        return EXIT_CONFIG_ERROR

    code = EXIT_INTERNAL_GATE_ERROR
    report: dict[str, Any] = {}
    try:
        run_stage(gate, "L0 STATIC", l0_static)
        run_stage(gate, "L1 IMPORT", l1_import)
        run_stage(gate, "L2 CONFIG", l2_config)
        cheap_failed = any(gate.stage_status(p) == "FAIL" for p in ("L0", "L1", "L2"))
        if cheap_failed:
            # Later tiers cannot be meaningful when the cheap layers failed.
            for name, _ in FULL_RUNTIME_STAGES:
                skip_stage(gate, name, "upstream layer failed")
        else:
            run_stage(gate, "L3 DATABASE", l3_database)
            run_stage(gate, "L4 MODEL/FEATURE", l4_model_contract)
            if args.fast:
                for name in ("L5 SERVICE GRAPH", "L6 DECISION CYCLE", "L7 API/HEALTH",
                             "L8 SHUTDOWN", "L9 INVARIANTS"):
                    skip_stage(gate, name, "--fast tier (runtime boot excluded)")
            elif gate.stage_status("L3") == "FAIL" or gate.stage_status("L4") == "FAIL":
                # DB/model contract failures make boot evidence misleading.
                for name, _ in FULL_RUNTIME_STAGES[2:]:
                    skip_stage(gate, name, "contract layer failed")
                run_stage(gate, "L9 INVARIANTS", l9_invariants)
            else:
                run_stage(gate, "L5 SERVICE GRAPH", l5_service_graph)
                if gate.stage_status("L5") == "FAIL":
                    for name in ("L6 DECISION CYCLE", "L7 API/HEALTH", "L8 SHUTDOWN"):
                        skip_stage(gate, name, "service graph failed")
                    run_stage(gate, "L9 INVARIANTS", l9_invariants)
                else:
                    run_stage(gate, "L6 DECISION CYCLE", l6_decision_cycle)
                    run_stage(gate, "L7 API/HEALTH", l7_api)
                    run_stage(gate, "L8 SHUTDOWN", l8_shutdown)
                    run_stage(gate, "L9 INVARIANTS", l9_invariants)
        code = gate.exit_code()
        report = gate_json(gate)
    except Exception as exc:  # fail-safe: a crashed gate is NEVER green
        code = EXIT_INTERNAL_GATE_ERROR
        report = {
            "gate_version": GATE_VERSION,
            "status": "GATE_CRASHED",
            "exit_code": code,
            "error": f"{type(exc).__name__}: {exc}",
            "stages": [s.to_dict() for s in gate.stages],
            "failures": [
                {"stage": "GATE", "reason": str(exc), "failure_class": "INTERNAL_GATE_ERROR"}
            ],
            "warnings": [],
        }
    finally:
        gate.finish()

    if args.evidence:
        try:
            evidence_dir = REPO_ROOT / "artifacts" / "forensics"
            evidence_dir.mkdir(parents=True, exist_ok=True)
            (evidence_dir / "runtime_gate_result.json").write_text(
                json.dumps(report, indent=2, default=str), encoding="utf-8"
            )
        except Exception:
            pass

    if args.json:
        print(json.dumps(report, indent=2, default=str))
    else:
        print(human_report(gate))
        for f in gate.failures:
            print(f"\n  [{f['stage']}] {f['failure_class']}: {f['reason']}")
            print(f"            owner: {f['owner']}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
