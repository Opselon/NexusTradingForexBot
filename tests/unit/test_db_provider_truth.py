"""Provider truth contract (DATABASE TAB, 2026-09-23).

The tab's badge echoed only what settings CLAIM (`database.provider`) while
every panel was fed by local SQLite files and the configured PostgreSQL
target did not answer — the operator saw "postgresql" and data from sqlite
with nothing explaining the gap.

Pinned here:
  * configured postgresql + failed probe + recent local writes
    -> effective "sqlite", mismatch=True, evidence lists the files;
  * probe answered -> effective "postgresql", mismatch=False;
  * recent-activity window decides sqlite vs "unavailable" (never guess);
  * configured sqlite never pays for a probe (pg_reachable is None);
  * probe errors go through db_console._fail(), so exception TEXT (which may
    embed credentials) is never echoed — type name / known-sentence only;
  * /api/db/manage/status carries `provider_truth`.
"""

from __future__ import annotations

from typing import Any

import pytest

from nexus_scalp.database.config import DatabaseConfig
from nexus_scalp.web import diagnostics_state_routes as routes

_PG_TARGET = "localhost:5432/nse_audit"


@pytest.fixture(autouse=True)
def _reset_probe_cache() -> Any:
    routes._PROBE_CACHE.update({"at": 0.0, "target": "", "ok": None, "error": ""})
    yield
    routes._PROBE_CACHE.update({"at": 0.0, "target": "", "ok": None, "error": ""})


def _ui(provider: str = "postgresql") -> dict[str, Any]:
    return {"provider": provider}


def _row(age_seconds: int, active: bool, name: str = "audit") -> dict[str, Any]:
    return {
        "name": name,
        "file": f"{name}.db",
        "path": f"C:/x/artifacts/{name}.db",
        "bytes": 425_725_952,
        "age_seconds": age_seconds,
        "mtime_utc": "2026-09-23T01:42:06+00:00",
        "active": active,
    }


class TestEffectiveProvider:
    def test_failed_probe_with_live_local_files_reports_effective_sqlite(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            routes,
            "_probe_postgres",
            lambda cfg: (
                False,
                _PG_TARGET,
                f"PostgreSQL at {_PG_TARGET} did not answer the connection probe.",
            ),
        )
        monkeypatch.setattr(
            routes,
            "_local_sqlite_evidence",
            lambda: [_row(30, True), _row(7200, False, name="news")],
        )
        truth = routes._provider_truth(_ui())
        assert truth["configured"] == "postgresql"
        assert truth["effective"] == "sqlite"
        assert truth["mismatch"] is True
        assert truth["pg_reachable"] is False
        assert truth["pg_target"] == _PG_TARGET
        assert "did not answer" in truth["note"]
        assert "sqlite" in truth["note"]
        # evidence is preserved for the UI band (file bytes + recency)
        assert [e["name"] for e in truth["evidence"]] == ["audit", "news"]
        assert truth["evidence"][0]["active"] is True
        assert truth["evidence"][1]["active"] is False

    def test_answered_probe_is_not_a_mismatch(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(routes, "_probe_postgres", lambda cfg: (True, _PG_TARGET, ""))
        monkeypatch.setattr(routes, "_local_sqlite_evidence", lambda: [])
        truth = routes._provider_truth(_ui())
        assert truth["effective"] == "postgresql"
        assert truth["mismatch"] is False
        assert truth["pg_reachable"] is True
        assert _PG_TARGET in truth["note"]

    def test_failed_probe_without_recent_writes_reports_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # no recent local writes -> do NOT claim sqlite; say stale/unavailable
        monkeypatch.setattr(routes, "_probe_postgres", lambda cfg: (False, _PG_TARGET, "nope"))
        monkeypatch.setattr(routes, "_local_sqlite_evidence", lambda: [_row(999_999, False)])
        truth = routes._provider_truth(_ui())
        assert truth["effective"] == "unavailable"
        assert truth["mismatch"] is True
        assert "stale" in truth["note"]

    def test_configured_sqlite_never_pays_for_a_probe(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _explode(cfg: Any) -> tuple[bool, str, str]:
            raise AssertionError("configured sqlite must not probe postgresql")

        monkeypatch.setattr(routes, "_probe_postgres", _explode)
        monkeypatch.setattr(routes, "_local_sqlite_evidence", lambda: [])
        truth = routes._provider_truth(_ui("sqlite"))
        assert truth["effective"] == "sqlite"
        assert truth["mismatch"] is False
        assert truth["pg_reachable"] is None
        assert truth["pg_target"] == ""


class TestProbeSafety:
    def test_probe_error_never_echoes_exception_text(self, monkeypatch: pytest.MonkeyPatch) -> None:
        secret = "password=hunter2 secret-token=abc"

        class _Boom:
            def ping(self) -> bool:
                raise ValueError(secret)

            def close(self) -> None:
                return None

        from nexus_scalp.database import drivers

        monkeypatch.setattr(drivers, "get_driver", lambda cfg: _Boom())
        cfg = DatabaseConfig.for_postgres(
            domain="audit", host="db.internal", port=5433, database="nse_audit", username="nse"
        )
        ok, target, error = routes._probe_postgres(cfg)
        assert ok is False
        assert target == "db.internal:5433/nse_audit"
        assert "hunter2" not in error
        assert "abc" not in error
        assert "ValueError" in error  # type name only (db_console._fail contract)

    def test_probe_result_is_cached_for_the_poll_window(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = {"n": 0}

        class _Down:
            def ping(self) -> bool:
                calls["n"] += 1
                return False

            def close(self) -> None:
                return None

        from nexus_scalp.database import drivers

        monkeypatch.setattr(drivers, "get_driver", lambda cfg: _Down())
        cfg = DatabaseConfig.for_postgres(
            domain="audit", host="db.internal", port=5433, database="nse_audit", username="nse"
        )
        first = routes._probe_postgres(cfg)
        second = routes._probe_postgres(cfg)
        assert first == second
        assert calls["n"] == 1, "second probe inside the TTL must be cache-served"


class TestEvidenceBuilder:
    def test_evidence_reports_real_file_metadata(
        self, tmp_path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os
        import time

        fresh = tmp_path / "audit.db"
        fresh.write_bytes(b"x" * 2048)
        stale = tmp_path / "stale.db"
        stale.write_bytes(b"y" * 100)
        os.utime(stale, (time.time() - 5000, time.time() - 5000))

        class _Cfg:
            sqlite_connect_path = str(fresh)

        class _CfgStale:
            sqlite_connect_path = str(stale)

        import nexus_scalp.database.config as cfgmod

        monkeypatch.setattr(cfgmod, "load_database_config", lambda domain: _Cfg())
        monkeypatch.setattr(routes, "_TRUTH_DOMAINS", ("audit",))
        rows = routes._local_sqlite_evidence()
        assert len(rows) == 1
        assert rows[0]["file"] == "audit.db"
        assert rows[0]["bytes"] == 2048
        assert rows[0]["active"] is True
        assert rows[0]["age_seconds"] <= routes._TRUTH_ACTIVE_WINDOW_S

        monkeypatch.setattr(cfgmod, "load_database_config", lambda domain: _CfgStale())
        monkeypatch.setattr(routes, "_TRUTH_DOMAINS", ("audit",))
        rows = routes._local_sqlite_evidence()
        assert rows[0]["active"] is False
        assert rows[0]["age_seconds"] >= 5000

    def test_missing_files_are_skipped_not_invented(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        class _Cfg:
            sqlite_connect_path = str(tmp_path / "does_not_exist.db")

        import nexus_scalp.database.config as cfgmod

        monkeypatch.setattr(cfgmod, "load_database_config", lambda domain: _Cfg())
        monkeypatch.setattr(routes, "_TRUTH_DOMAINS", ("audit", "news"))
        assert routes._local_sqlite_evidence() == []


class TestRouteCarriesTruth:
    def test_manage_status_exposes_provider_truth(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        import nexus_scalp.database.drivers.postgres_driver as pgdrv
        from nexus_scalp.database import health

        monkeypatch.setattr(
            health,
            "health_snapshot",
            lambda: {
                "supported_providers": ["sqlite", "postgresql"],
                "overall": "Warning",
                "domains": {},
            },
        )
        monkeypatch.setattr(
            health,
            "load_ui_config",
            lambda: {"provider": "postgresql", "postgres": None, "password_set": False},
        )
        monkeypatch.setattr(pgdrv.PostgreSQLDriver, "available", staticmethod(lambda: True))
        sentinel = {
            "configured": "postgresql",
            "effective": "sqlite",
            "mismatch": True,
            "note": "sentinel",
            "evidence": [_row(30, True)],
        }
        monkeypatch.setattr(routes, "_provider_truth", lambda ui: sentinel)

        app = FastAPI()
        routes.register_diagnostics_state_routes(
            app,
            _err=None,
            _log_err=lambda *a, **k: None,
            serialize_enums=lambda x: x,
            get_system_state=lambda: {},
        )
        out = TestClient(app).get("/api/db/manage/status").json()
        assert out["provider_truth"] == sentinel
        assert out["provider"] == "postgresql"
