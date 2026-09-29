#!/usr/bin/env python3
"""Two-minute process-level runtime soak for each database provider.

Boots the real LiveEngine + FastAPI application graph against the provider
selected by CI, keeps both loops alive for a fixed wall-clock window, probes
the live HTTP surface, and performs a bounded graceful shutdown.

This is deliberately separate from scripts/ci/runtime_gate.py:
runtime_gate certifies the service graph deterministically; this lane exercises
a real long-running process with a real SQLite file or throwaway PostgreSQL.

Safety:
  * PAPER mode only; no broker order execution.
  * Telegram/news are disabled so external services do not turn this into an
    internet-availability test.
  * PostgreSQL is a throwaway CI service, never production nexusdb.
  * SQLite uses the app isolation seams under RUNNER_TEMP.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import socket
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
MODEL_ARTIFACT = (
    REPO_ROOT
    / "artifacts"
    / "models"
    / "scalp"
    / "XAUUSD"
    / "70d_liquidity"
    / "model.pt"
)


def _set_env(name: str, value: str) -> None:
    os.environ[name] = value


def configure_provider(provider: str) -> None:
    """Install CI-only isolation/provider settings before app construction."""
    root = Path(os.environ.get("RUNNER_TEMP", str(REPO_ROOT / ".ci-runtime"))).resolve()
    root.mkdir(parents=True, exist_ok=True)

    _set_env("NEXUS_SETTINGS_DB", str(root / "app_settings.db"))
    _set_env("NEXUS_AUDIT_DB", str(root / "audit.db"))
    _set_env("NEXUS_DATA_ROOT", str(root / "data"))
    _set_env("NSE_NO_TELEGRAM", "1")
    _set_env("NSE_TELEGRAM__ENABLED", "false")
    _set_env("NSE_NEWS__ENABLED", "false")
    _set_env("NSE_WEB_AUTH_DISABLE", "1")
    _set_env("NSE_WEB_HOST", "127.0.0.1")
    _set_env("NSE_WEB_PORT", os.environ.get("NSE_RUNTIME_SOAK_PORT", "18080"))
    _set_env("NSE_EXECUTION__MODE", "PAPER")
    _set_env("NSE_EXECUTION__SYMBOL", "XAUUSD")

    if provider == "postgres":
        password = os.environ.get("NSE_PG_TEST_PASSWORD", "")
        if not password:
            raise RuntimeError("NSE_PG_TEST_PASSWORD is required for the PostgreSQL soak")

        _set_env("NSE_DATABASE__PROVIDER", "postgresql")
        _set_env("NSE_DATABASE__PG_HOST", os.environ.get("NSE_DATABASE__PG_HOST", "127.0.0.1"))
        _set_env("NSE_DATABASE__PG_PORT", os.environ.get("NSE_DATABASE__PG_PORT", "5432"))
        _set_env(
            "NSE_DATABASE__PG_DATABASE",
            os.environ.get("NSE_DATABASE__PG_DATABASE", "nse_audit"),
        )
        _set_env("NSE_DATABASE__PG_USER", os.environ.get("NSE_DATABASE__PG_USER", "nse_user"))
        _set_env("NSE_DATABASE__PG_SSLMODE", "disable")

        # Production config keeps the password only in SecureSecretStore.
        from nexus_scalp.database.config import PG_PASSWORD_SECRET_KEY
        from nexus_scalp.settings.secret_store import SecureSecretStore

        SecureSecretStore().set_secret(PG_PASSWORD_SECRET_KEY, password)
    elif provider == "sqlite":
        _set_env("NSE_DATABASE__PROVIDER", "sqlite")
        for name in (
            "NSE_DATABASE__PG_HOST",
            "NSE_DATABASE__PG_PORT",
            "NSE_DATABASE__PG_DATABASE",
            "NSE_DATABASE__PG_USER",
            "NSE_DATABASE__PG_SSLMODE",
        ):
            os.environ.pop(name, None)
    else:
        raise ValueError(f"unsupported provider: {provider}")


def database_probe(provider: str) -> dict[str, Any]:
    """Prove provider resolution plus a real SELECT 1 before engine boot."""
    from nexus_scalp.database.config import build_postgres_url, load_database_config

    cfg = load_database_config("audit")
    resolved = cfg.provider.value
    if provider == "postgres" and not cfg.is_postgresql:
        raise RuntimeError(f"provider resolution mismatch: expected postgresql, got {resolved}")
    if provider == "sqlite" and not cfg.is_sqlite:
        raise RuntimeError(f"provider resolution mismatch: expected sqlite, got {resolved}")

    if cfg.is_postgresql:
        import psycopg

        with psycopg.connect(build_postgres_url(cfg), connect_timeout=5) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                value = cursor.fetchone()
        if not value or value[0] != 1:
            raise RuntimeError(f"PostgreSQL SELECT 1 returned {value!r}")
        return {
            "provider": resolved,
            "database": cfg.database,
            "host": cfg.host,
            "port": cfg.port,
        }

    path = cfg.sqlite_connect_path
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=5) as connection:
        value = connection.execute("SELECT 1").fetchone()
    if not value or value[0] != 1:
        raise RuntimeError(f"SQLite SELECT 1 returned {value!r}")
    return {"provider": resolved, "path": path}


def _wait_for_port(host: str, port: int, timeout: float) -> None:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return
        except OSError as exc:
            last_error = exc
            time.sleep(0.5)
    raise RuntimeError(
        f"web server did not bind {host}:{port} within {timeout:.0f}s: {last_error}"
    )


def http_probe(url: str, timeout: float = 5.0) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read(64 * 1024)
            return {
                "status": int(getattr(response, "status", 200) or 200),
                "bytes": len(body),
                "duration_ms": round((time.perf_counter() - started) * 1000, 2),
            }
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"HTTP {exc.code} from {url}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"HTTP probe failed for {url}: {exc}") from exc


def _task_failure(task: asyncio.Task[Any], name: str) -> str | None:
    if not task.done():
        return None
    if task.cancelled():
        return f"{name} task cancelled"
    exc = task.exception()
    return f"{name} task exited early: {type(exc).__name__}: {exc}" if exc else f"{name} task exited early"


async def run_soak(provider: str, duration_sec: int, host: str, port: int) -> dict[str, Any]:
    """Boot the real app graph, soak it, probe it, then drain it."""
    import uvicorn
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.adapters.paper.paper_data import build_paper_adapter
    from nexus_scalp.application.live_engine import LiveEngine
    from nexus_scalp.configuration.config import AppConfig
    from nexus_scalp.database.config import build_postgres_url, load_database_config
    from nexus_scalp.domain.enums import ActionType, ExecutionMode
    from nexus_scalp.domain.models import TradeProposal
    from nexus_scalp.web.server import create_app

    if not MODEL_ARTIFACT.exists():
        raise RuntimeError(f"required model bundle missing: {MODEL_ARTIFACT}")

    base = AppConfig.load_from_yaml(REPO_ROOT / "configs" / "live.yaml")
    raw = base.model_dump()
    raw["execution"]["mode"] = ExecutionMode.PAPER.value
    raw["execution"]["symbol"] = "XAUUSD"
    raw["model"]["model_artifact_path"] = str(MODEL_ARTIFACT)
    if raw.get("news") is not None:
        raw["news"]["enabled"] = False
    if raw.get("candle_intel") is not None:
        raw["candle_intel"]["enabled"] = False
    raw["telegram"] = {
        **raw.get("telegram", {}),
        "enabled": False,
        "bot_token": "",
        "admin_id": "",
    }
    config = AppConfig.model_validate(raw)

    db_evidence = database_probe(provider)
    db_cfg = load_database_config("audit")
    if provider == "sqlite":
        audit_path = Path(os.environ["NEXUS_AUDIT_DB"]).resolve()
        audit_repo = AuditRepository(
            db_url=f"sqlite:///{audit_path}",
            flush_interval_sec=0.1,
        )
    else:
        audit_repo = AuditRepository(config=db_cfg, flush_interval_sec=0.1)

    adapter = build_paper_adapter(
        symbol=config.execution.symbol,
        paper_data=getattr(config, "paper_data", None),
    )
    engine = LiveEngine(
        config=config,
        adapter=adapter,
        audit_repo=audit_repo,
        mode_override=ExecutionMode.PAPER,
    )
    engine._preflight_or_raise()

    app = create_app(engine_ref=engine)
    server = uvicorn.Server(
        uvicorn.Config(
            app=app,
            host=host,
            port=port,
            log_level="warning",
            ws_max_size=16 * 1024 * 1024,
        )
    )

    server_task = asyncio.create_task(server.serve(), name="nse-web")
    engine_task = asyncio.create_task(engine.run_loop(), name="nse-engine")

    probes: list[dict[str, Any]] = []
    started = time.monotonic()
    failure: str | None = None
    health_url = f"http://{host}:{port}/health"
    status_url = f"http://{host}:{port}/api/status"

    try:
        await asyncio.to_thread(_wait_for_port, host, port, 30)
        first_health = http_probe(health_url)
        first_status = http_probe(status_url)
        if first_health["status"] != 200 or first_status["status"] != 200:
            raise RuntimeError(
                f"initial probe failure: health={first_health} status={first_status}"
            )

        deadline = time.monotonic() + duration_sec
        while time.monotonic() < deadline:
            for label, url in (("health", health_url), ("status", status_url)):
                result = http_probe(url)
                result["endpoint"] = label
                result["elapsed_sec"] = round(time.monotonic() - started, 2)
                probes.append(result)
                if result["status"] != 200:
                    raise RuntimeError(f"{label} returned HTTP {result['status']}")

            for task, name in ((server_task, "web"), (engine_task, "engine")):
                early = _task_failure(task, name)
                if early:
                    raise RuntimeError(early)

            remaining = deadline - time.monotonic()
            await asyncio.sleep(min(5.0, max(0.1, remaining)))
    except Exception as exc:
        failure = f"{type(exc).__name__}: {exc}"
    finally:
        # Always attempt a bounded graceful drain. A hung teardown is a failure,
        # not a reason to leave CI with a living process.
        server.should_exit = True
        try:
            await asyncio.wait_for(engine.stop(), timeout=5)
        except Exception:
            pass
        try:
            await asyncio.wait_for(
                asyncio.gather(server_task, engine_task, return_exceptions=True),
                timeout=30,
            )
        except Exception:
            pass
        try:
            await asyncio.wait_for(engine._shutdown_async(), timeout=30)
        except Exception:
            pass

    elapsed = time.monotonic() - started
    persistence_evidence: dict[str, Any] = {}
    if not failure:
        # Force one harmless NO_TRADE audit write so the matrix proves the
        # running engine can persist through the selected provider.
        try:
            engine.audit.log_signal(
                TradeProposal(
                    request_id=f"ci-runtime-soak-{provider}",
                    symbol="XAUUSD",
                    generated_at=datetime.now(UTC),
                    action=ActionType.NO_TRADE,
                    confidence=0.0,
                    proposed_entry=2000.0,
                    stop_loss=1990.0,
                    take_profit=2020.0,
                    risk_reward_ratio=2.0,
                    reason_code="CI_RUNTIME_SOAK",
                )
            )
            if not engine.audit.flush(timeout_sec=10.0):
                raise RuntimeError("audit flush returned false")
            if provider == "postgres":
                import psycopg

                with psycopg.connect(build_postgres_url(db_cfg), connect_timeout=5) as connection:
                    with connection.cursor() as cursor:
                        cursor.execute(
                            "SELECT COUNT(*) FROM audit_signals WHERE request_id = %s",
                            (f"ci-runtime-soak-{provider}",),
                        )
                        count = int(cursor.fetchone()[0])
            else:
                with sqlite3.connect(
                    os.environ["NEXUS_AUDIT_DB"], timeout=5
                ) as connection:
                    row = connection.execute(
                        "SELECT COUNT(*) FROM audit_signals WHERE request_id = ?",
                        (f"ci-runtime-soak-{provider}",),
                    ).fetchone()
                    count = int(row[0]) if row else 0
            if count != 1:
                raise RuntimeError(f"expected one persisted soak probe row, got {count}")
            persistence_evidence = {"audit_probe_rows": count}
        except Exception as exc:
            failure = f"persistence verification failed: {type(exc).__name__}: {exc}"

    if not failure:
        failure = _task_failure(server_task, "web") or _task_failure(engine_task, "engine")
    if not failure and (not server_task.done() or not engine_task.done()):
        failure = "runtime tasks did not terminate during graceful shutdown"
    if not failure and not engine.shutdown_completed:
        failure = "LiveEngine shutdown did not reach CLOSED state"
    if not failure and provider == "postgres" and Path(os.environ["NEXUS_AUDIT_DB"]).exists():
        failure = "unexpected SQLite audit DB was created during PostgreSQL runtime soak"

    report = {
        "provider": provider,
        "requested_duration_sec": duration_sec,
        "actual_duration_sec": round(elapsed, 2),
        "db": db_evidence,
        "probe_count": len(probes),
        "last_probes": probes[-10:],
        "persistence": persistence_evidence,
        "shutdown": {
            "web_task_done": server_task.done(),
            "engine_task_done": engine_task.done(),
            "engine_shutdown_completed": engine.shutdown_completed,
        },
    }
    if failure:
        report["failure"] = failure
        raise RuntimeError(json.dumps(report, indent=2, sort_keys=True))
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--provider", choices=("sqlite", "postgres"), required=True)
    parser.add_argument("--duration", type=int, default=120)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--port",
        type=int,
        default=int(os.environ.get("NSE_RUNTIME_SOAK_PORT", "18080")),
    )
    args = parser.parse_args()

    if args.duration < 120:
        parser.error("--duration must be at least 120 seconds")

    if str(REPO_ROOT / "src") not in sys.path:
        sys.path.insert(0, str(REPO_ROOT / "src"))

    configure_provider(args.provider)
    evidence_dir = Path(
        os.environ.get("RUNNER_TEMP", str(REPO_ROOT / ".ci-runtime"))
    ).resolve() / f"nse-runtime-{args.provider}"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_path = evidence_dir / "provider_runtime_soak.json"

    started = time.perf_counter()
    try:
        report = asyncio.run(run_soak(args.provider, args.duration, args.host, args.port))
    except Exception as exc:
        report = {
            "provider": args.provider,
            "status": "FAIL",
            "error": str(exc),
            "elapsed_sec": round(time.perf_counter() - started, 2),
        }
        evidence_path.write_text(
            json.dumps(report, indent=2, sort_keys=True) + "\\n",
            encoding="utf-8",
        )
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1

    report["status"] = "PASS"
    evidence_path.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
