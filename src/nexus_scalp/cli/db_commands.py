"""
CLI DB Commands (TASK-10 §24/§25/§53/§54)
========================================
`nexus db status|plan|migrate|verify|migrations|history|repair` — all backed
by the SAME canonical migration engine as startup (no separate CLI
implementation, §25).

All commands support --json for machine-readable output (no ANSI, §54).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import typer

from nexus_scalp.database.engine import DatabaseMigrationEngine, db_path_for_domain
from nexus_scalp.database.models import DatabaseDomain
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.cli.db")

_ALL_DOMAINS = ("audit", "news", "candle_intel")


def _domain(value: str) -> DatabaseDomain:
    try:
        return DatabaseDomain(value.lower())
    except ValueError:
        raise typer.BadParameter(
            f"unknown database '{value}' — expected one of: {', '.join(_ALL_DOMAINS)}"
        ) from None


def _engine(
    database: str | None,
    workspace: Path | None,
    *,
    app_version: str = "",
    git_commit: str = "",
) -> dict[str, DatabaseMigrationEngine]:
    """Builds engines for the requested domain(s) — one per domain (§2)."""
    if database:
        dom = _domain(database)
        return {
            dom.value: DatabaseMigrationEngine(
                db_path=db_path_for_domain(dom.value, workspace),
                domain=dom,
                application_version=app_version,
                git_commit=git_commit,
            )
        }
    return {
        d: DatabaseMigrationEngine(
            db_path=db_path_for_domain(d, workspace),
            domain=DatabaseDomain(d),
            application_version=app_version,
            git_commit=git_commit,
        )
        for d in _ALL_DOMAINS
    }


def _emit(payload: dict[str, Any], json_mode: bool, *, plain_title: str = "") -> None:
    if json_mode:
        # Pure machine-readable stdout: silence structlog (stderr) noise so
        # `--json` output is parseable with zero post-processing (§54).
        try:
            import logging as _logging

            _logging.disable(_logging.CRITICAL)
        except Exception:
            pass
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    print(plain_title or "")
    for db, data in payload.items():
        if isinstance(data, dict) and "current_version" in data:
            print(
                f"  {db:14} schema {data['current_version']} / expected "
                f"{data['expected_version']}  [{data.get('migration_state', data.get('state', ''))}]"
            )


def _print_error(message: str) -> None:
    print(f"ERROR: {message}", file=sys.stderr)
    raise typer.Exit(1)


def make_portability_app() -> typer.Typer:
    """`nexus db-portability` — DATABASE PORTABILITY workflow (SQLite <-> PostgreSQL)."""
    app = typer.Typer(help="DATABASE PORTABILITY: provider status, config, migration.")

    @app.command("status")
    def portability_status(
        json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON."),
    ):
        """Active provider + per-domain health snapshot."""
        from nexus_scalp.database.health import health_snapshot, load_ui_config

        health = health_snapshot()
        ui = load_ui_config()
        payload = {
            "provider": ui["provider"],
            "supported_providers": health["supported_providers"],
            "overall": health["overall"],
            "domains": health["domains"],
        }
        _emit(payload, json_mode, plain_title="DATABASE PORTABILITY STATUS")

    @app.command("config")
    def portability_config(
        host: str = typer.Option("localhost", "--host", help="PostgreSQL host."),
        port: int = typer.Option(5432, "--port", help="PostgreSQL port."),
        database: str = typer.Option("nse_audit", "--database", help="PostgreSQL database name."),
        username: str = typer.Option("nse_user", "--username", help="PostgreSQL role."),
        ssl_mode: str = typer.Option("", "--ssl-mode", help="PostgreSQL SSL mode."),
        password: str = typer.Option(
            "", "--password", help="PostgreSQL password (stored in the OS secret store)."
        ),
        json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON."),
    ) -> None:
        """Save the PostgreSQL connection configuration + password (secret store)."""
        from nexus_scalp.settings.service import load_settings_service

        svc = load_settings_service()
        cfg = {
            "host": host,
            "port": port,
            "database": database,
            "username": username,
            "ssl_mode": ssl_mode,
        }
        if password:
            cfg["password"] = password
        svc.set_postgres_config(cfg)
        payload = {
            "success": True,
            "provider": "postgresql",
            "configured": True,
            "password_set": svc.postgres_password_set(),
        }
        _emit(payload, json_mode, plain_title="POSTGRESQL CONFIG SAVED")

    @app.command("switch")
    def portability_switch(
        provider: str = typer.Argument(..., help="sqlite | postgresql"),
        json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON."),
    ) -> None:
        """Switch the ACTIVE provider (takes effect on next start)."""
        from nexus_scalp.database.provider import DatabaseProvider
        from nexus_scalp.settings.service import load_settings_service

        parsed = DatabaseProvider.parse(provider)
        svc = load_settings_service()
        svc.set_database_provider(parsed.value)
        payload = {"success": True, "provider": parsed.value, "restart_required": True}
        _emit(payload, json_mode, plain_title=f"PROVIDER SWITCHED TO {parsed.value.upper()}")

    @app.command("test-connection")
    def portability_test(
        host: str = typer.Option("localhost", "--host"),
        port: int = typer.Option(5432, "--port"),
        database: str = typer.Option("nse_audit", "--database"),
        username: str = typer.Option("nse_user", "--username"),
        password: str = typer.Option("", "--password"),
        ssl_mode: str = typer.Option("", "--ssl-mode"),
        json_mode: bool = typer.Option(False, "--json"),
    ) -> None:
        """Test the PostgreSQL connection."""
        from nexus_scalp.database.config import DatabaseConfig
        from nexus_scalp.database.drivers import get_driver
        from nexus_scalp.settings.secret_store import SecureSecretStore

        if password:
            from nexus_scalp.database.config import PG_PASSWORD_SECRET_KEY

            SecureSecretStore().set_secret(PG_PASSWORD_SECRET_KEY, password)
        cfg = DatabaseConfig.for_postgres(
            domain="audit",
            host=host,
            port=port,
            database=database,
            username=username,
            ssl_mode=ssl_mode,
        )
        driver = get_driver(cfg)
        try:
            ok = driver.ping()
            payload = {
                "success": ok,
                "connected": ok,
                "database_version": driver.database_version() if ok else "",
            }
        finally:
            driver.close()
        _emit(payload, json_mode, plain_title="POSTGRESQL CONNECTION TEST")

    @app.command("preview")
    def portability_preview(json_mode: bool = typer.Option(False, "--json")):
        """Dry-run preview of the SQLite->PostgreSQL migration."""
        mig = _portability_migrator({})
        payload = mig.preview()
        _emit(payload, json_mode, plain_title="MIGRATION PREVIEW (DRY RUN)")

    @app.command("migrate")
    def portability_migrate(
        dry_run: bool = typer.Option(False, "--dry-run", help="Preview only, no writes."),
        confirm: bool = typer.Option(False, "--confirm", help="Confirm the real migration."),
        batch_size: int = typer.Option(2000, "--batch-size", help="Rows per batch."),
        resume: bool = typer.Option(True, "--resume/--restart", help="Resume from checkpoint."),
        json_mode: bool = typer.Option(False, "--json"),
    ) -> None:
        """Run the SQLite->PostgreSQL migration (streamed, resumable)."""
        payload = {
            "dry_run": dry_run,
            "confirm": confirm,
            "batch_size": batch_size,
            "resume": resume,
        }
        mig = _portability_migrator(payload)
        report = mig.run()
        _emit(report.to_dict(), json_mode, plain_title="MIGRATION RESULT")

    @app.command("validate")
    def portability_validate(json_mode: bool = typer.Option(False, "--json")):
        """Validate the last migration (row counts, identities, financials)."""
        mig = _portability_migrator({})
        result = mig.validate()
        _emit({"validation": result}, json_mode, plain_title="MIGRATION VALIDATION")

    @app.command("backup")
    def portability_backup(json_mode: bool = typer.Option(False, "--json")):
        """WAL-consistent backup of the SQLite audit database."""
        import time

        ts = time.strftime("%Y%m%d-%H%M%S")
        import os

        os.makedirs("artifacts/backups", exist_ok=True)
        import sqlite3

        backup_path = f"artifacts/backups/audit_backup_{ts}.db"
        src = sqlite3.connect("artifacts/audit.db", timeout=30.0)
        try:
            dst = sqlite3.connect(backup_path)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        payload = {"success": True, "backup_path": backup_path}
        _emit(payload, json_mode, plain_title="SQLITE BACKUP CREATED")

    return app


def make_portability_migrator(payload: dict[str, Any]) -> Any:
    """Build the SQLite->PostgreSQL migrator from CLI/`--json` payload (portability)."""
    from nexus_scalp.database.config import DatabaseConfig
    from nexus_scalp.database.migrate_engine import (
        MigrationOptions,
        SqliteToPostgresMigrator,
    )

    src = DatabaseConfig.for_sqlite("audit", path=str(payload.get("sqlite_path") or "") or None)
    dst = DatabaseConfig.for_postgres(
        domain="audit",
        host=str(payload.get("host") or "localhost"),
        port=int(payload.get("port") or 5432),
        database=str(payload.get("database") or "nse_audit"),
        username=str(payload.get("username") or "nse_user"),
        ssl_mode=str(payload.get("ssl_mode") or ""),
    )
    options = MigrationOptions(
        dry_run=bool(payload.get("dry_run")),
        confirm=bool(payload.get("confirm")),
        resume=bool(payload.get("resume", True)),
        batch_size=int(payload.get("batch_size") or 2000),
        validate_checksums=bool(payload.get("validate_checksums", True)),
    )
    return SqliteToPostgresMigrator(src, dst, options)


def _portability_migrator(payload: dict[str, Any]) -> Any:
    return make_portability_migrator(payload)


def make_db_app(
    workspace: Path | None = None,
    *,
    app_version: str = "",
    git_commit: str = "",
) -> typer.Typer:
    """Builds the `nexus db` sub-command group (canonical engine)."""
    app = typer.Typer(help="Database schema migration and management.")

    @app.command("status")
    def db_status(
        database: str = typer.Option(None, "--database", "-d", help="audit|news|candle_intel"),
        json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
    ) -> None:
        """Current schema version + migration state per domain."""
        engines = _engine(database, workspace, app_version=app_version, git_commit=git_commit)
        payload: dict[str, Any] = {}
        for name, eng in engines.items():
            st = eng.status()
            payload[name] = {
                "database": name,
                "current_version": st["current_version"],
                "expected_version": st["expected_version"],
                "pending_count": st["pending_count"],
                "migration_state": st["migration_state"],
                "last_migration": st.get("last_migration", {}),
                "integrity": st.get("integrity", ""),
                "tamper_detected": st.get("tamper_detected", False),
                "drift": st.get("drift", []),
                "error_code": st.get("error", ""),
            }
        _emit(payload, json_mode, plain_title="DATABASE STATUS")

    @app.command("plan")
    def db_plan(
        database: str = typer.Option(None, "--database", "-d", help="audit|news|candle_intel"),
        json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
    ) -> None:
        """Dry-run: shows the migration plan WITHOUT applying it (§25)."""
        engines = _engine(database, workspace, app_version=app_version, git_commit=git_commit)
        payload: dict[str, Any] = {}
        for name, eng in engines.items():
            plan = eng.plan()
            payload[name] = {
                "database": name,
                "current_version": plan["current_version"],
                "expected_version": plan["expected_version"],
                "pending_count": plan["pending_count"],
                "migration_state": plan["migration_state"],
                "pending": plan["pending"],
            }
        _emit(payload, json_mode, plain_title="MIGRATION PLAN (dry-run — no changes made)")

    @app.command("migrate")
    def db_migrate(
        database: str = typer.Option(None, "--database", "-d", help="audit|news|candle_intel"),
        json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
        force: bool = typer.Option(False, "--force", help="Bypass the migration lock."),
    ) -> None:
        """Applies pending safe migrations (§26)."""
        engines = _engine(database, workspace, app_version=app_version, git_commit=git_commit)
        payload: dict[str, Any] = {}
        failed = False
        for name, eng in engines.items():
            result = eng.migrate(force=force)
            payload[name] = result
            if result["state"] in (
                "DB_MIGRATION_FAILED",
                "DB_BLOCKED",
                "DB_DOWNGRADE_BLOCKED",
            ):
                failed = True
        _emit(payload, json_mode, plain_title="DATABASE MIGRATION")
        if failed:
            raise typer.Exit(1)

    @app.command("verify")
    def db_verify(
        database: str = typer.Option(None, "--database", "-d", help="audit|news|candle_intel"),
        json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
    ) -> None:
        """Post-migration verification: version + integrity + drift (§32)."""
        engines = _engine(database, workspace, app_version=app_version, git_commit=git_commit)
        payload: dict[str, Any] = {}
        ok = True
        for name, eng in engines.items():
            v = eng.verify()
            payload[name] = v
            ok = ok and bool(v["verified"])
        _emit(payload, json_mode, plain_title="DATABASE VERIFICATION")
        if not ok:
            raise typer.Exit(1)

    @app.command("migrations")
    def db_migrations(
        database: str = typer.Option(None, "--database", "-d", help="audit|news|candle_intel"),
        json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
    ) -> None:
        """Pending + applied migration catalogue (§53)."""
        engines = _engine(database, workspace, app_version=app_version, git_commit=git_commit)
        payload: dict[str, Any] = {}
        for name, eng in engines.items():
            plan = eng.plan()
            payload[name] = {
                "database": name,
                "current_version": plan["current_version"],
                "expected_version": plan["expected_version"],
                "pending": plan["pending"],
            }
        _emit(payload, json_mode, plain_title="MIGRATION CATALOGUE")

    @app.command("history")
    def db_history(
        database: str = typer.Option(None, "--database", "-d", help="audit|news|candle_intel"),
        limit: int = typer.Option(20, "--limit", help="Max history rows per domain."),
        json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
    ) -> None:
        """Applied migration history (§53)."""
        engines = _engine(database, workspace, app_version=app_version, git_commit=git_commit)
        payload: dict[str, Any] = {}
        for name, eng in engines.items():
            payload[name] = {
                "database": name,
                "history": eng.history(limit=limit),
            }
        _emit(payload, json_mode, plain_title="MIGRATION HISTORY")

    @app.command("repair")
    def db_repair(
        database: str = typer.Option(None, "--database", "-d", help="audit|news|candle_intel"),
        json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
    ) -> None:
        """Safe automatic repair: re-run the idempotent additive engine (§40)."""
        engines = _engine(database, workspace, app_version=app_version, git_commit=git_commit)
        payload: dict[str, Any] = {}
        failed = False
        for name, eng in engines.items():
            result = eng.repair()
            payload[name] = result
            if result["state"] in (
                "DB_MIGRATION_FAILED",
                "DB_BLOCKED",
                "DB_DOWNGRADE_BLOCKED",
            ):
                failed = True
        _emit(payload, json_mode, plain_title="DATABASE REPAIR")
        if failed:
            raise typer.Exit(1)

    @app.command("doctor")
    def db_doctor(
        database: str = typer.Option(None, "--database", "-d", help="audit|news|candle_intel"),
        json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON output."),
    ) -> None:
        """READ-ONLY database health diagnostics (never writes).

        Aggregates per-domain schema version, migration state, integrity,
        tamper detection and drift into a READY/DEGRADED/BLOCKED verdict.
        For repair use `nse db repair` (explicit action).
        """
        import sqlite3

        from nexus_scalp.release.paths import get_runtime_workspace

        ws = workspace or get_runtime_workspace()
        engines = _engine(database, workspace, app_version=app_version, git_commit=git_commit)
        payload: dict[str, Any] = {}
        overall = "READY"
        for name, eng in engines.items():
            st = eng.status()
            verdict = st.get("migration_state") or "READY"
            if verdict in (
                "DB_READY",
                "READY",
                "DB_MIGRATION_NOT_REQUIRED",
                "DB_MIGRATION_SUCCEEDED",
                "DB_UP_TO_DATE",
            ):
                verdict_txt = "READY"
            elif verdict in ("DB_BLOCKED", "BLOCKED", "DB_DOWNGRADE_BLOCKED"):
                verdict_txt = "BLOCKED"
            else:
                verdict_txt = "DEGRADED"
            entry: dict[str, Any] = {
                "database": name,
                "verdict": verdict_txt,
                "current_version": st.get("current_version"),
                "expected_version": st.get("expected_version"),
                "pending_count": st.get("pending_count"),
                "migration_state": st.get("migration_state"),
                "tamper_detected": st.get("tamper_detected", False),
                "drift": st.get("drift", []),
            }
            db_file = ws / "artifacts" / f"{name}.db"
            if not db_file.exists():
                db_file = ws / "artifacts" / "audit.db"
            try:
                con = sqlite3.connect(f"file:{db_file}?mode=ro", uri=True, timeout=2)
                try:
                    integ = con.execute("PRAGMA integrity_check").fetchone()
                    entry["integrity"] = integ[0] if integ else "unknown"
                    dv = con.execute("PRAGMA data_version").fetchone()
                    entry["data_version"] = dv[0] if dv else None
                finally:
                    con.close()
            except sqlite3.Error as e:
                entry["integrity"] = f"error: {e}"
                if verdict_txt == "READY":
                    verdict_txt = "DEGRADED"
                    entry["verdict"] = verdict_txt
            payload[name] = entry
            if verdict_txt == "BLOCKED":
                overall = "BLOCKED"
            elif verdict_txt == "DEGRADED" and overall != "BLOCKED":
                overall = "DEGRADED"
        payload["_overall"] = overall
        _emit(payload, json_mode, plain_title="DATABASE DOCTOR")
        if overall == "BLOCKED":
            raise typer.Exit(1)

    @app.command("create-migration")
    def db_create_migration(
        database: str = typer.Option(..., "--database", "-d", help="audit|news|candle_intel"),
        name: str = typer.Option(..., "--name", help="snake_case migration name"),
    ) -> None:
        """Generates a migration TEMPLATE (never executes it) (§52)."""
        dom = _domain(database)
        template = _migration_template(dom.value, name)
        out = Path("scratch") / f"migration_{dom.value}_{name}.py"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(template, encoding="utf-8")
        print(f"Migration template written: {out}")
        print("Review, register in nexus_scalp/database/registry.py, then test.")

    return app


def _migration_template(domain: str, name: str) -> str:
    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in name)
    return (
        f'"""\nMigration template: {domain}-{safe}\n'
        f"Generated by `nexus db create-migration` (TASK-10 §52) — review before use.\n"
        f'"""\n\n'
        f"from pathlib import Path\n"
        f"import sqlite3\n\n\n"
        f"def apply(conn: sqlite3.Connection, db_path: Path) -> None:\n"
        f'    """Apply the schema change (idempotent)."""\n'
        f"    raise NotImplementedError\n\n\n"
        f"def verify(conn: sqlite3.Connection, db_path: Path) -> bool:\n"
        f'    """Return True when the change is present."""\n'
        f"    return True\n\n\n"
        f"def rollback(conn: sqlite3.Connection, db_path: Path) -> None:\n"
        f'    """Compensation strategy (required for destructive changes)."""\n'
        f"    pass\n"
    )


# ---------------------------------------------------------------------------
# Database Hygiene (TASK-11): nexus db hygiene status|plan|run|pause|resume|history
# ---------------------------------------------------------------------------


def _repo_root() -> Path:
    """Locates the repo root from the working directory (artifacts/ sibling)."""
    cwd = Path.cwd()
    for candidate in (cwd, cwd.parent, cwd.parent.parent):
        if (candidate / "artifacts").exists():
            return candidate
    return cwd


def _hygiene_worker(mode: str, apply_deletes: bool) -> Any:
    from nexus_scalp.hygiene import WorkerMode
    from nexus_scalp.hygiene.worker_runner import DatabaseHygieneWorker

    try:
        wmode = WorkerMode(mode)
    except ValueError:
        wmode = WorkerMode.AUDIT_ONLY
    return DatabaseHygieneWorker(repo_root=_repo_root(), mode=wmode, apply_deletes=apply_deletes)


def _hygiene_emit(payload: dict[str, Any], json_mode: bool, title: str = "") -> None:
    if json_mode:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
        return
    print(title)
    if isinstance(payload, dict):
        for k, v in payload.items():
            if isinstance(v, dict):
                print(f"  {k}:")
                for k2, v2 in v.items():
                    print(f"    {k2}: {v2}")
            else:
                print(f"  {k}: {v}")


def make_hygiene_app() -> typer.Typer:
    app = typer.Typer(help="Database hygiene worker (TASK-11) — non-destructive defaults.")

    @app.command("status")
    def hygiene_status(
        json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON."),
    ) -> None:
        """Show worker state + DB sizes + last run info (spec §42)."""
        w = _hygiene_worker("AUDIT_ONLY", False)
        _hygiene_emit(w.status(), json_mode, "DATABASE HYGIENE STATUS")

    @app.command("plan")
    def hygiene_plan(
        database: str = typer.Option(
            "", "--database", "-d", help="audit|news|candle_intel (default: all)"
        ),
        json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON."),
    ) -> None:
        """Build the cleanup PLAN (ZERO mutation — spec §40)."""
        w = _hygiene_worker("AUDIT_ONLY", False)
        targets = [database] if database else ["audit", "news", "candle_intel"]
        out: dict[str, Any] = {}
        for db in targets:
            out[db] = w.plan_database(db)
        _hygiene_emit(out, json_mode, "DATABASE HYGIENE PLAN (read-only, no mutation)")

    @app.command("run")
    def hygiene_run(
        mode: str = typer.Option(
            "AUDIT_ONLY", "--mode", help="AUDIT_ONLY|DRY_RUN|SAFE_CLEAN|AGGRESSIVE_CLEAN"
        ),
        database: str = typer.Option(
            "", "--database", "-d", help="audit|news|candle_intel (default: all)"
        ),
        apply: bool = typer.Option(False, "--apply", help="Actually apply SAFE_CLEAN deletes."),
        json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON."),
    ) -> None:
        """Run one hygiene cycle (spec §41/§42).

        --mode SAFE_CLEAN --apply is required for any destructive action.
        AUDIT_ONLY/DRY_RUN never modify data.
        """
        w = _hygiene_worker(mode, apply)
        targets = [database] if database else None
        res = w.run_cycle(targets)
        _hygiene_emit(res, json_mode, "DATABASE HYGIENE RUN")

    @app.command("health")
    def hygiene_health(
        json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON."),
    ) -> None:
        """Database Health Panel (TASK-22): runtime scheduler status + quarantine
        + last cycle telemetry. Read-only.
        """
        from nexus_scalp.hygiene.hygiene_runtime import (
            RuntimeCleanupScheduler,
        )

        s = RuntimeCleanupScheduler(repo_root=_repo_root())
        payload: dict[str, Any] = {"scheduler": s.status()}
        run_rows = s.state_store.list_runs(limit=5)
        payload["recent_runs"] = run_rows
        payload["quarantine"] = s.quarantine.stats()
        _hygiene_emit(payload, json_mode, "DATABASE HEALTH PANEL (TASK-22)")

    @app.command("cleanup")
    def hygiene_cleanup(
        dry_run: bool = typer.Option(True, "--dry-run", help="No changes applied."),
        deep: bool = typer.Option(False, "--deep", help="Deep maintenance cycle."),
        apply: bool = typer.Option(False, "--apply", help="Apply SAFE_CLEAN deletes."),
        json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON."),
    ) -> None:
        """One runtime cleanup cycle (TASK-22). Safe defaults: dry-run only."""
        from nexus_scalp.hygiene.hygiene_runtime import (
            RuntimeCleanupScheduler,
            RuntimeHygieneSettings,
        )

        settings = RuntimeHygieneSettings(
            dry_run=dry_run or not apply,
            apply_deletes=apply and not dry_run,
        )
        s = RuntimeCleanupScheduler(repo_root=_repo_root(), settings=settings)
        res = s.run_cycle(deep=deep)
        _hygiene_emit(
            {"cycle": res["cycle"], "telemetry": res["telemetry"], "result": res["result"]},
            json_mode,
            "DATABASE CLEANUP CYCLE (TASK-22)",
        )

    @app.command("quarantine")
    def hygiene_quarantine(
        status: str = typer.Option(
            "", "--status", help="QUARANTINED|RESTORED|RESOLVED_DELETED|EXTERMINATED (default: all)"
        ),
        limit: int = typer.Option(50, "--limit", help="Rows to show."),
        json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON."),
    ) -> None:
        """List quarantined records (TASK-22 spec §9)."""
        from nexus_scalp.hygiene.quarantine import QuarantineStore

        q = QuarantineStore(_repo_root())
        items = q.list(status=status or None, limit=limit)
        if json_mode:
            print(json.dumps(items, ensure_ascii=False, indent=2, default=str))
            return
        print("DATA QUARANTINE (TASK-22)")
        for it in items:
            print(
                f"  {it.get('quarantine_id', '')} {it.get('database', '')}.{it.get('table', '')} "
                f"row_id={it.get('row_id', '')} status={it.get('status', '')} "
                f"reason={it.get('reason', '')[:60]}"
            )

    @app.command("pause")
    def hygiene_pause(json_mode: bool = typer.Option(False, "--json")) -> None:
        w = _hygiene_worker("AUDIT_ONLY", False)
        _hygiene_emit(w.pause(), json_mode, "DATABASE HYGIENE PAUSED")

    @app.command("resume")
    def hygiene_resume(json_mode: bool = typer.Option(False, "--json")) -> None:
        w = _hygiene_worker("AUDIT_ONLY", False)
        _hygiene_emit(w.resume(), json_mode, "DATABASE HYGIENE RESUMED")

    @app.command("history")
    def hygiene_history(
        limit: int = typer.Option(50, "--limit", help="Rows to show."),
        json_mode: bool = typer.Option(False, "--json", help="Machine-readable JSON."),
    ) -> None:
        w = _hygiene_worker("AUDIT_ONLY", False)
        runs = w.history(limit=limit)
        if json_mode:
            print(json.dumps(runs, ensure_ascii=False, indent=2, default=str))
            return
        print("DATABASE HYGIENE HISTORY")
        for r in runs:
            print(
                f"  {r.get('run_id', '')} {r.get('database', '')} "
                f"mode={r.get('mode', '')} verification={r.get('verification_status', '')} "
                f"deleted={r.get('deleted', 0)}"
            )

    return app


hygiene_app = make_hygiene_app()

# Convenience: expose a ready-to-register typer app for cli/main.py.
db_app = make_db_app()
# TASK-11: `nexus db hygiene *` — registered as a SUBCOMMAND of the `db` typer.
db_app.add_typer(
    hygiene_app,
    name="hygiene",
    help="Database hygiene worker (TASK-11) — non-destructive defaults.",
)
