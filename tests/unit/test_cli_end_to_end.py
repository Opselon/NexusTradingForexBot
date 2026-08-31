"""CLI END-TO-END tests (TASK-CLI-E2E, 2026-08-31 Nexus-Main) — 68 tests.

Exactly 68 end-to-end tests over the canonical Typer CLI
(``nexus_scalp.cli.main.app``) driven through ``typer.testing.CliRunner`` —
every command group, its answers (prompts/confirmations), its input/output
contract and its stable exit codes (docs/RELEASE.md):

    0 SUCCESS | 1 RUNTIME_OR_VALIDATION | 2 USAGE | 3 ENVIRONMENT_BLOCKED |
    4 RELEASE_VERIFICATION | 5 UPDATE_NOT_APPLICABLE_OR_FAILED

Each test exercises the REAL command path (no engine start, no broker, no
network-mandatory success): JSON parity keys, human panels, safety guards
(LIVE confirmation, uninstall data deletion), artifact-first model factory
flows, DB migration/health groups, incident forensics and dependency
intelligence. Sensitive machine state (settings DB, user config, logs dir,
data root) is isolated per-test via monkeypatch, mirroring tests/conftest.py.

Known-real CLI defects intentionally pinned here (BUG-150/BUG-151 in
agents/bugs.md — fixes tracked separately):
    * BUG-150: ``model-dataset-build --with-news`` without an explicit
      ``--news-db`` file crashes with sqlite3.OperationalError because the
      empty-Path sentinel ``Path("")`` normalizes to ``Path(".")`` (truthy,
      "exists"), so the command opens ``.`` as the news DB.
    * BUG-151: ``model-train-3`` imports the nonexistent module
      ``nexus_scalp.model_generation.three_model_pipeline`` (the canonical
      module is ``three_model``), so every invocation crashes.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import zipfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from nexus_scalp.cli.main import app
from nexus_scalp.features.schema import FEATURE_SCHEMAS
from nexus_scalp.release import exit_codes as xc

runner = CliRunner()

REPO_ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def _invoke(args: list[str], inp: str | None = None):
    """Invoke the CLI in-process (CliRunner captures stdout/exit codes)."""
    return runner.invoke(app, args, input=inp)


def _parse_json_output(res) -> dict:
    """Extract the trailing JSON document from mixed panel+json output."""
    lines = (res.stdout or "").splitlines()
    for i, line in enumerate(lines):
        if line.strip().startswith("{") or line.strip().startswith("["):
            try:
                return json.loads("\n".join(lines[i:]))
            except json.JSONDecodeError:
                continue
    raise AssertionError(f"no JSON document in output: {(res.stdout or '')[:200]!r}")


def _make_bars_csv(tmp_path: Path, rows: int = 900, seed: int = 42) -> Path:
    """Deterministic bars CSV carrying scalp_v1 feature columns — the
    artifact-first dataset factory needs feat_0..feat_49 + atr."""
    import random

    import polars as pl

    dim = FEATURE_SCHEMAS.resolve("scalp_v1").dimension
    rng = random.Random(seed)
    price = 2400.0
    data: list[dict] = []
    for i in range(rows):
        o = price
        c = price + rng.uniform(-1.5, 1.5)
        h = max(o, c) + abs(rng.uniform(0, 1))
        low = min(o, c) - abs(rng.uniform(0, 1))
        row = {
            "timestamp": f"2026-01-{1 + i // 1440:02d} {i // 60 % 24:02d}:{i % 60:02d}:00",
            "open": round(o, 3),
            "high": round(h, 3),
            "low": round(low, 3),
            "close": round(c, 3),
            "volume": round(rng.uniform(50, 500), 1),
            "atr": round(0.5 + rng.uniform(0, 1), 3),
        }
        for j in range(dim):
            row[f"feat_{j}"] = round(rng.uniform(-2, 2), 4)
        data.append(row)
        price = c
    out = tmp_path / "bars.csv"
    pl.DataFrame(data).write_csv(out)
    return out


def _isolated_cwd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Fresh CWD so artifacts/, scratch/ and logs never touch the repo."""
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _fake_logs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    log_dir = tmp_path / "logs"
    log_dir.mkdir(exist_ok=True)
    import nexus_scalp.release.paths as rpaths

    monkeypatch.setattr(rpaths, "get_logs_dir", lambda: log_dir)
    return log_dir


def _seed_incident(tmp_path: Path) -> None:
    """Seed one OPEN/HIGH incident into the isolated audit DB."""
    from nexus_scalp.incidents.models import (
        Incident,
        IncidentCategory,
        IncidentSeverity,
        IncidentStatus,
    )
    from nexus_scalp.incidents.store import IncidentStore

    (tmp_path / "artifacts").mkdir(exist_ok=True)
    store = IncidentStore(db_path=str(tmp_path / "artifacts" / "audit.db"))
    store.ensure_schema()
    store.save(
        Incident(
            incident_id="INC-E2E-TEST1",
            severity=IncidentSeverity.HIGH,
            category=IncidentCategory.MT5,
            status=IncidentStatus.OPEN,
            component="mt5",
            operation="E2E_TEST_OP",
        )
    )


# ===========================================================================
# 1. top-level surface + version/health/status/doctor/test (tests 01-06)
# ===========================================================================


def test_e2e_01_main_help_lists_every_command() -> None:
    res = _invoke(["--help"])
    assert res.exit_code == xc.EXIT_OK
    for cmd in (
        "version",
        "doctor",
        "health",
        "status",
        "test",
        "logs",
        "config",
        "config-validate",
        "settings",
        "repair",
        "audit-purge",
        "diagnostics",
        "verify-release",
        "update",
        "release",
        "install",
        "setup",
        "uninstall",
        "start",
        "stop",
        "restart",
        "run",
        "db",
        "db-portability",
        "incidents",
        "analyze",
        "dependency",
        "model-dataset-build",
        "model-experiment-create",
        "model-train",
        "model-inspect",
        "model-validate",
        "model-replay",
        "model-doctor",
        "model-train-3",
    ):
        assert cmd in res.stdout, f"missing command {cmd}"


def test_e2e_02_version_plain_and_json_report_canonical_identity() -> None:
    from nexus_scalp.release.metadata import get_version_info

    res_plain = _invoke(["version", "--plain"])
    assert res_plain.exit_code == xc.EXIT_OK
    assert get_version_info()["version"] in res_plain.stdout
    res_json = _invoke(["version", "--json"])
    assert res_json.exit_code == xc.EXIT_OK
    data = json.loads(res_json.stdout)
    for key in ("version", "channel", "architecture", "commit", "feature_schema"):
        assert key in data


def test_e2e_03_health_and_status_json_emit_verdicts_with_check_items() -> None:
    res_h = _invoke(["health", "--json"])
    assert res_h.exit_code == xc.EXIT_OK
    data = json.loads(res_h.stdout)
    assert data["overall"] in ("READY", "DEGRADED", "NOT READY")
    assert data["checks"]
    assert {"category", "verdict", "reason"} <= set(data["checks"][0])
    res_s = _invoke(["status", "--json"])
    assert res_s.exit_code == xc.EXIT_OK
    status = json.loads(res_s.stdout)
    assert {"overall", "checks", "version", "environment"} <= set(status)


def test_e2e_04_doctor_json_reports_20_checks_plus_environment() -> None:
    res = _invoke(["doctor", "--json"])
    assert res.exit_code == xc.EXIT_OK
    data = json.loads(res.stdout)
    assert data["overall"] in ("READY", "DEGRADED", "NOT READY")
    assert len(data["checks"]) >= 19
    assert data["environment"]


def test_e2e_05_doctor_fix_repairs_then_reverifies_to_ready() -> None:
    # BUG-158: --yes makes the repair non-interactive. Without it, a fresh
    # environment (no user config yet) has fixable fails and the doctor
    # prompts; CliRunner EOF then raises Abort -> exit 1. Machine-state
    # dependency, not a CLI defect (human TTY gets default=True on Enter).
    res = _invoke(["doctor", "--fix", "--yes", "--json"])
    assert res.exit_code == xc.EXIT_OK
    data = json.loads(res.stdout)
    assert {"checks", "overall", "repair"} <= set(data)
    assert data["overall"] in ("READY", "DEGRADED", "PASS")


def test_e2e_06_test_unknown_mode_is_usage_error_with_hint() -> None:
    res = _invoke(["test", "--mode", "bogus", "--json"])
    assert res.exit_code == xc.EXIT_USAGE
    data = json.loads(res.stdout)
    assert "unknown test mode" in data["error"]
    assert "quick|unit|integration" in data["hint"]


# ===========================================================================
# 2. config / settings (tests 07-10)
# ===========================================================================


def test_e2e_07_config_validate_happy_path_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    res = _invoke(["config", "--validate", str(REPO_ROOT / "configs" / "base.yaml"), "--json"])
    assert res.exit_code == xc.EXIT_OK
    data = json.loads(res.stdout)
    assert data["valid"] is True
    assert data["symbol"] == "XAUUSD"
    assert data["mode"] in ("PAPER", "SHADOW", "LIVE")


def test_e2e_08_config_validate_missing_and_invalid_inputs_exit_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    res_missing = _invoke(["config-validate", "--config", str(tmp_path / "none.yaml"), "--json"])
    assert res_missing.exit_code == xc.EXIT_RUNTIME
    data = json.loads(res_missing.stdout)
    assert data["exists"] is False and data["valid"] is False
    bad = tmp_path / "bad.yaml"
    bad.write_text("execution: [unclosed", encoding="utf-8")
    res_bad = _invoke(["config-validate", "--config", str(bad), "--json"])
    assert res_bad.exit_code == xc.EXIT_RUNTIME
    data_bad = json.loads(res_bad.stdout)
    assert data_bad["valid"] is False and "error" in data_bad


def test_e2e_09_settings_json_masks_telegram_token_completely(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    res = _invoke(["settings", "--json"])
    assert res.exit_code == xc.EXIT_OK
    data = json.loads(res.stdout)
    assert {"state", "db_path", "telegram", "settings"} <= set(data)
    tg = data["telegram"]
    assert {"configured", "token_present", "masked_token", "admin_id_shape_valid"} <= set(tg)
    if tg["token_present"]:
        assert tg["masked_token"], "masked token must never be the raw secret"


def test_e2e_10_settings_roundtrip_via_cli_never_leaks_plaintext_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    from nexus_scalp.settings.service import load_settings_service

    svc = load_settings_service()
    svc.set_telegram(
        enabled=True,
        bot_token="1234567890:ABCDEFGHIJKLMNOPQRSTUVWXyz",
        admin_id="5094837833",
        actor="e2e-test",
    )
    svc.close()
    res = _invoke(["settings", "--json"])
    assert res.exit_code == xc.EXIT_OK
    raw = res.stdout
    assert "ABCDEFGHIJKLMNOPQRSTUVWXyz" not in raw, "plaintext token leaked via --json"
    data = json.loads(raw)
    assert data["telegram"]["admin_id_shape_valid"] is True
    assert data["telegram"]["token_length"] == 37


# ===========================================================================
# 3. repair / audit-purge / diagnostics / verify-release (tests 11-14)
# ===========================================================================


def test_e2e_11_repair_json_reports_actions_and_is_never_destructive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    res = _invoke(["repair", "--json"])
    assert res.exit_code == xc.EXIT_OK
    data = _parse_json_output(res)
    assert data["overall"] in ("OK", "FAILED")
    actions = data["actions"]
    assert "directories" in {a["action"] for a in actions}
    assert all(a["status"] != "DELETED" for a in actions), "repair never deletes user data"


def test_e2e_12_audit_purge_json_reports_deleted_tables_and_window(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    (tmp_path / "artifacts").mkdir()
    res = _invoke(["audit-purge", "--json"])
    assert res.exit_code == xc.EXIT_OK
    data = _parse_json_output(res)
    assert {"deleted", "duration_ms"} <= set(data)
    res_custom = _invoke(["audit-purge", "--signal-days", "3", "--telemetry-days", "5", "--json"])
    assert res_custom.exit_code == xc.EXIT_OK
    custom = _parse_json_output(res_custom)
    assert custom["signal_retention_days"] == 3.0
    assert custom["telemetry_retention_days"] == 5.0


def test_e2e_13_diagnostics_export_is_sanitized_zip_without_secrets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    res = _invoke(["diagnostics"])
    assert res.exit_code == xc.EXIT_OK
    assert "Diagnostics exported" in res.stdout
    assert "no passwords" in res.stdout
    from nexus_scalp.release import diagnostics as rdiag

    archive = rdiag.export_diagnostics(workspace=tmp_path)
    with zipfile.ZipFile(archive) as zf:
        payload = json.loads(zf.read("diagnostics.json"))
    blob = json.dumps(payload).lower()
    assert "bot_token" not in blob and "password" not in blob
    assert {"version", "health", "dependencies"} <= set(payload)


def test_e2e_14_verify_release_empty_dir_fails_with_exit_4_and_check_matrix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    empty = tmp_path / "release"
    empty.mkdir()
    res_json = _invoke(["verify-release", "--root", str(empty), "--json"])
    assert res_json.exit_code == xc.EXIT_OK  # --json path returns; failure travels in payload
    data = json.loads(res_json.stdout)
    assert data["valid"] is False
    assert data["exit_code"] == xc.EXIT_RELEASE
    checks = {c["check"] for c in data["checks"]}
    assert "EXE exists" in checks
    # human path raises the documented EXIT_RELEASE (4)
    res_human = _invoke(["verify-release", "--root", str(empty)])
    assert res_human.exit_code == xc.EXIT_RELEASE


# ===========================================================================
# 4. update / release metadata (tests 15-22)
# ===========================================================================


def test_e2e_15_update_bad_channel_is_usage_error_in_both_modes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    res = _invoke(["update", "check", "--channel", "bogus", "--json"])
    assert res.exit_code == xc.EXIT_USAGE
    data = json.loads(res.stdout)
    assert "unknown channel" in data["error"]


def test_e2e_16_update_status_and_history_json_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    res_status = _invoke(["update", "status", "--json"])
    assert res_status.exit_code == xc.EXIT_OK
    status = json.loads(res_status.stdout)
    assert {"state", "recovery", "lock_held", "current_version", "channel"} <= set(status)
    res_hist = _invoke(["update", "history", "--json"])
    assert res_hist.exit_code == xc.EXIT_OK
    assert isinstance(json.loads(res_hist.stdout), list)


def test_e2e_17_update_doctor_json_overall_with_named_checks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    res = _invoke(["update", "doctor", "--json"])
    assert res.exit_code == xc.EXIT_OK
    data = json.loads(res.stdout)
    names = [c["name"] for c in data["checks"]]
    assert "github_connectivity" in names and "process_state" in names
    assert data["overall"] in ("READY", "NOT READY")


def test_e2e_18_update_rollback_without_backup_is_failed_safe_exit_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    res = _invoke(["update", "rollback", "--json"])
    assert res.exit_code == xc.EXIT_OK
    data = json.loads(res.stdout)
    assert data["state"] in ("FAILED_SAFE", "ROLLED_BACK")


def test_e2e_19_update_unknown_subcommand_is_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    res = _invoke(["update", "bogus-sub", "--json"])
    assert res.exit_code == xc.EXIT_USAGE
    data = json.loads(res.stdout)
    assert "unknown update subcommand" in data["error"]
    assert "check|latest|download" in data["hint"]


def test_e2e_20_update_manifest_missing_file_exits_runtime(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    res = _invoke(["update", "--manifest", str(tmp_path / "no.json"), "--json"])
    assert res.exit_code == xc.EXIT_RUNTIME
    data = json.loads(res.stdout)
    assert "manifest not found" in data["error"]


def test_e2e_21_update_manifest_unverifiable_asset_is_security_blocked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No resolvable SHA-256 → update refuses (no silent fallback)."""
    _isolated_cwd(tmp_path, monkeypatch)
    # BUG-154: pin the INSTALLED version below the manifest tag. The plan
    # builder short-circuits NO_UPDATE (exit 0) when target == installed,
    # so these tests must never inherit the live pyproject version
    # (same version-coupled time-bomb class as BUG-153).
    monkeypatch.setattr(
        "nexus_scalp.cli.main.get_version_info",
        lambda: {
            "version": "9.0.3",
            "architecture": "x64",
            "channel": "stable",
            "commit": None,
        },
    )
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps(
            {
                "assets": [
                    {
                        "name": "NexusScalpEngine-9.0.4-win-x64.zip",
                        "size": 1,
                        "browser_download_url": "http://invalid.invalid/y.zip",
                    }
                ],
                "tag_name": "v9.0.4",
                "prerelease": False,
                "body": "",
            }
        ),
        encoding="utf-8",
    )
    res = _invoke(["update", "--manifest", str(manifest), "--json"])
    assert res.exit_code == xc.EXIT_UPDATE
    data = json.loads(res.stdout)
    assert data["status"] in ("SECURITY_BLOCKED", "INCOMPATIBLE", "RELEASE_NOT_FOUND")


def test_e2e_22_release_info_json_and_unknown_subcommand_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    res = _invoke(["release", "info", "--json"])
    assert res.exit_code == xc.EXIT_OK
    data = json.loads(res.stdout)
    assert data["exit_code"] == xc.EXIT_OK
    assert {"current_version", "channel", "architecture"} <= set(data)
    res_bad = _invoke(["release", "bogus", "--json"])
    assert res_bad.exit_code == xc.EXIT_USAGE
    assert "unknown release subcommand" in json.loads(res_bad.stdout)["error"]


# ===========================================================================
# 5. start/stop/uninstall SAFETY contract (tests 23-31)
# ===========================================================================


def test_e2e_23_start_missing_config_json_exits_runtime_with_hint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    res = _invoke(["start", "--config", str(tmp_path / "none.yaml"), "--json"])
    assert res.exit_code == xc.EXIT_RUNTIME
    data = json.loads(res.stdout)
    assert "Config missing" in data["error"]
    assert "nexus setup" in data["hint"]


def test_e2e_24_start_invalid_mode_is_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    res = _invoke(["start", "--mode", "bogus", "--json"])
    assert res.exit_code == xc.EXIT_USAGE
    data = json.loads(res.stdout)
    assert "mode must be paper|shadow|live" in data["error"]


def test_e2e_25_start_live_via_json_without_yes_never_runs_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    import nexus_scalp.cli.main as cmain

    called: dict[str, bool] = {}
    monkeypatch.setattr(cmain, "_run_engine", lambda *a, **k: called.setdefault("ran", True))
    res = _invoke(
        ["start", "--config", str(REPO_ROOT / "configs" / "base.yaml"), "--mode", "live", "--json"]
    )
    assert res.exit_code == xc.EXIT_USAGE
    assert "requires explicit --yes" in json.loads(res.stdout)["error"]
    assert not called.get("ran"), "LIVE start must be impossible without --yes"


def test_e2e_26_start_live_interactive_decline_aborts_without_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    import nexus_scalp.cli.main as cmain

    called: dict[str, bool] = {}
    monkeypatch.setattr(cmain, "_run_engine", lambda *a, **k: called.setdefault("ran", True))
    res = _invoke(
        ["start", "--config", str(REPO_ROOT / "configs" / "base.yaml"), "--mode", "live"],
        inp="n\n",
    )
    assert res.exit_code == xc.EXIT_OK
    assert "aborted" in (res.stdout or "").lower()
    assert not called.get("ran")


def test_e2e_27_start_paper_json_reaches_engine_with_mode_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    import nexus_scalp.cli.main as cmain
    from nexus_scalp.domain.enums import ExecutionMode

    seen: dict = {}

    def fake_run(cfg, *, gateway=False, port=8080, mode_override=None):
        seen["mode"] = mode_override
        raise KeyboardInterrupt  # end the start ceremony cleanly

    monkeypatch.setattr(cmain, "_run_engine", fake_run)
    res = _invoke(
        ["start", "--config", str(REPO_ROOT / "configs" / "base.yaml"), "--mode", "paper", "--json"]
    )
    assert res.exit_code in (xc.EXIT_OK, 130)  # 130 = clean KeyboardInterrupt ceremony
    data = json.loads(res.stdout)
    assert data["status"] == "starting"
    assert data["mode"] == "PAPER"
    assert data["symbol"] == "XAUUSD"
    assert seen["mode"] == ExecutionMode.PAPER
    assert data["endpoints"][0].startswith("http://localhost:")


def test_e2e_28_start_daemon_json_spawns_detached_command(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    import nexus_scalp.cli.main as cmain

    captured: dict = {}
    monkeypatch.setattr(cmain, "_spawn_daemon", lambda cmd: captured.setdefault("cmd", cmd))
    res = _invoke(
        ["start", "--config", str(REPO_ROOT / "configs" / "base.yaml"), "--daemon", "--json"]
    )
    assert res.exit_code == xc.EXIT_OK
    data = json.loads(res.stdout)
    assert data["status"] == "starting_daemon"
    cmd = captured["cmd"]
    assert "--mode" in cmd and "paper" in cmd


def test_e2e_29_stop_handles_missing_and_stale_pidfiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "data"
    data_root.mkdir()
    import nexus_scalp.release.paths as rpaths

    monkeypatch.setattr(rpaths, "get_data_root", lambda: data_root)
    res_none = _invoke(["stop"])
    assert res_none.exit_code == xc.EXIT_OK
    assert "No pidfile" in res_none.stdout
    pidfile = data_root / "nexus.pid"
    pidfile.write_text("999999", encoding="utf-8")  # dead pid
    res_stale = _invoke(["stop"])
    assert res_stale.exit_code == xc.EXIT_OK
    assert "Engine stopped" in res_stale.stdout
    assert not pidfile.exists(), "stale pidfile must be removed"


def test_e2e_30_uninstall_json_reports_keep_data_and_never_deletes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "udata"
    data_root.mkdir()
    (data_root / "keep.txt").write_text("x", encoding="utf-8")
    import nexus_scalp.release.paths as rpaths

    monkeypatch.setattr(rpaths, "get_data_root", lambda: data_root)
    res = _invoke(["uninstall", "--json"])
    assert res.exit_code == xc.EXIT_OK
    data = json.loads(res.stdout)
    assert data["keep_data"] is True
    assert data["exit_code"] == xc.EXIT_OK
    assert (data_root / "keep.txt").exists(), "json uninstall must never delete data"


def test_e2e_31_uninstall_remove_data_decline_preserves_confirm_deletes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data_root = tmp_path / "udata2"
    data_root.mkdir()
    (data_root / "file.txt").write_text("x", encoding="utf-8")
    import nexus_scalp.release.paths as rpaths

    monkeypatch.setattr(rpaths, "get_data_root", lambda: data_root)
    res_decline = _invoke(["uninstall", "--remove-data"], inp="n\n")
    assert res_decline.exit_code == xc.EXIT_OK
    assert "data preserved" in (res_decline.stdout or "").lower()
    assert (data_root / "file.txt").exists(), "declined removal must preserve data"
    res_confirm = _invoke(["uninstall", "--remove-data"], inp="y\n")
    assert res_confirm.exit_code == xc.EXIT_OK
    assert "User data removed" in res_confirm.stdout
    assert not (data_root / "file.txt").exists()


# ===========================================================================
# 6. setup wizard — answers → persisted outcome (tests 32-34)
# ===========================================================================


def test_e2e_32_setup_wizard_json_paper_persists_choice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    import nexus_scalp.cli.main as cmain

    monkeypatch.setattr(cmain.reval, "overall_verdict", lambda results: ("PASS", []))
    res = _invoke(["setup", "--json"], inp="PAPER\nXAUUSD\n\n")
    assert res.exit_code == xc.EXIT_OK
    data = _parse_json_output(res)
    assert data["mode"] == "PAPER"
    assert data["symbol"] == "XAUUSD"
    assert data["exit_code"] == xc.EXIT_OK
    assert data["port"] == 8080
    assert data["web_endpoints"], "wizard must advertise dashboard endpoints"


def test_e2e_33_setup_wizard_invalid_mode_falls_back_to_paper_never_live(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    import nexus_scalp.cli.main as cmain

    monkeypatch.setattr(cmain.reval, "overall_verdict", lambda results: ("PASS", []))
    res = _invoke(["setup", "--json"], inp="BOGUS\n\n\n")
    assert res.exit_code == xc.EXIT_OK
    data = _parse_json_output(res)
    assert data["mode"] == "PAPER", "invalid input must never escalate to LIVE"


def test_e2e_34_setup_wizard_live_decline_aborts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    import nexus_scalp.cli.main as cmain

    monkeypatch.setattr(cmain.reval, "overall_verdict", lambda results: ("PASS", []))
    res = _invoke(["setup", "--json"], inp="LIVE\nn\n\n")
    assert res.exit_code == xc.EXIT_RUNTIME
    assert "not confirmed" in (res.stdout or "").lower()


# ===========================================================================
# 7. logs (tests 35-37)
# ===========================================================================


def test_e2e_35_logs_without_any_log_file_shows_friendly_panel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fake_logs_dir(tmp_path, monkeypatch)
    res = _invoke(["logs", "--tail", "5"])
    assert res.exit_code == xc.EXIT_OK
    assert "No logs" in res.stdout
    assert "nexus start" in res.stdout


def test_e2e_36_logs_tail_and_error_filters_shape_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_dir = _fake_logs_dir(tmp_path, monkeypatch)
    (log_dir / "2026-08-31.log").write_text("l1\nl2\nl3\nERROR bad\n", encoding="utf-8")
    res_tail = _invoke(["logs", "--tail", "2"])
    assert res_tail.exit_code == xc.EXIT_OK
    assert "l3" in res_tail.stdout and "l1" not in res_tail.stdout
    res_err = _invoke(["logs", "--errors"])
    assert res_err.exit_code == xc.EXIT_OK
    assert "ERROR bad" in res_err.stdout
    assert "l1" not in res_err.stdout and "l2" not in res_err.stdout


def test_e2e_37_logs_export_writes_zip_with_log_contents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log_dir = _fake_logs_dir(tmp_path, monkeypatch)
    (log_dir / "2026-08-31.log").write_text("body-1\nbody-2\n", encoding="utf-8")
    target = tmp_path / "exported.zip"
    res = _invoke(["logs", "--export", str(target)])
    assert res.exit_code == xc.EXIT_OK
    assert "Logs exported" in res.stdout
    with zipfile.ZipFile(target) as zf:
        names = zf.namelist()
    assert "2026-08-31.log" in names


# ===========================================================================
# 8. db migrations + hygiene (tests 38-42)
# ===========================================================================


def test_e2e_38_db_status_json_all_domains_shape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    (tmp_path / "artifacts").mkdir()
    res = _invoke(["db", "status", "--json"])
    assert res.exit_code == xc.EXIT_OK
    data = json.loads(res.stdout)
    assert {"audit", "news", "candle_intel"} <= set(data)
    audit = data["audit"]
    assert {
        "database",
        "current_version",
        "expected_version",
        "pending_count",
        "migration_state",
        "integrity",
        "tamper_detected",
    } <= set(audit)


def test_e2e_39_db_status_domain_filter_and_unknown_domain_usage_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    (tmp_path / "artifacts").mkdir()
    res_single = _invoke(["db", "status", "--database", "audit", "--json"])
    assert res_single.exit_code == xc.EXIT_OK
    assert list(json.loads(res_single.stdout)) == ["audit"]
    res_bad = _invoke(["db", "status", "--database", "bogus", "--json"])
    assert res_bad.exit_code == xc.EXIT_USAGE


def test_e2e_40_db_migrate_idempotent_then_verify_and_doctor_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    (tmp_path / "artifacts").mkdir()
    res1 = _invoke(["db", "migrate", "--json"])  # all domains
    res2 = _invoke(["db", "migrate", "--json"])  # idempotent second run
    assert res1.exit_code == res2.exit_code == xc.EXIT_OK
    assert json.loads(res2.stdout)["audit"]["state"] == "DB_MIGRATION_NOT_REQUIRED"
    res_v = _invoke(["db", "verify", "--json"])
    assert res_v.exit_code == xc.EXIT_OK
    assert json.loads(res_v.stdout)["audit"]["integrity"] == "ok"
    res_d = _invoke(["db", "doctor", "--json"])
    assert res_d.exit_code == xc.EXIT_OK
    assert json.loads(res_d.stdout)["audit"]["verdict"] in ("READY", "DEGRADED")


def test_e2e_41_db_create_migration_writes_template_into_scratch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    res = _invoke(["db", "create-migration", "--database", "audit", "--name", "e2e_probe_template"])
    assert res.exit_code == xc.EXIT_OK
    out = tmp_path / "scratch" / "migration_audit_e2e_probe_template.py"
    assert out.exists()
    body = out.read_text(encoding="utf-8")
    assert "def apply" in body and "def verify" in body


def test_e2e_42_db_hygiene_pause_resume_and_dry_run_never_destructive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    (tmp_path / "artifacts").mkdir()
    res_p = _invoke(["db", "hygiene", "pause", "--json"])
    assert res_p.exit_code == xc.EXIT_OK
    assert json.loads(res_p.stdout)["state"] == "PAUSED"
    res_r = _invoke(["db", "hygiene", "resume", "--json"])
    assert res_r.exit_code == xc.EXIT_OK
    assert json.loads(res_r.stdout)["state"] == "IDLE"
    res_run = _invoke(["db", "hygiene", "run", "--mode", "DRY_RUN", "--json"])
    assert res_run.exit_code == xc.EXIT_OK
    run = json.loads(res_run.stdout)
    assert run["mode"] == "DRY_RUN"
    assert {"run_id", "databases", "verification"} <= set(run)


# ===========================================================================
# 9. model-dataset-build + model lifecycle (tests 43-49)
# ===========================================================================


def test_e2e_43_model_dataset_build_artifact_first_happy_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    (tmp_path / "artifacts").mkdir()
    bars = _make_bars_csv(tmp_path)
    res = _invoke(["model-dataset-build", "--bars", str(bars), "--symbol", "XAUUSD"])
    assert res.exit_code == xc.EXIT_OK
    match = re.search(r"dataset_id: (ds_[0-9a-f]+)", res.stdout)
    assert match, "plain output must carry the dataset_id"
    handle_dir = Path("artifacts") / "model_generation" / "datasets" / match.group(1)
    assert (handle_dir / "dataset.parquet").exists()
    assert "Dataset built" in res.stdout
    assert "counts:" in res.stdout


def test_e2e_44_model_dataset_build_missing_bars_file_is_runtime_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    res = _invoke(["model-dataset-build", "--bars", str(tmp_path / "nope.csv")])
    assert res.exit_code == xc.EXIT_RUNTIME
    assert "No bars file" in res.stdout
    assert "--bars path/to/bars.csv" in res.stdout


def test_e2e_45_model_dataset_build_deterministic_identity_same_input_same_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    (tmp_path / "artifacts").mkdir()
    bars = _make_bars_csv(tmp_path, seed=7)
    res1 = _invoke(["model-dataset-build", "--bars", str(bars)])
    res2 = _invoke(["model-dataset-build", "--bars", str(bars)])
    assert res1.exit_code == res2.exit_code == xc.EXIT_OK
    id1 = re.search(r"dataset_id: (ds_[0-9a-f]+)", res1.stdout).group(1)
    id2 = re.search(r"dataset_id: (ds_[0-9a-f]+)", res2.stdout).group(1)
    assert id1 == id2, "content-addressed dataset identity must be deterministic"


def test_e2e_46_model_dataset_build_with_news_empty_db_warns_all_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    (tmp_path / "artifacts").mkdir()
    from nexus_scalp.news.database import NewsDatabase

    news_db = tmp_path / "artifacts" / "news.db"
    NewsDatabase(str(news_db)).close()
    bars = _make_bars_csv(tmp_path, rows=300, seed=5)
    res = _invoke(
        ["model-dataset-build", "--bars", str(bars), "--with-news", "--news-db", str(news_db)]
    )
    assert res.exit_code == xc.EXIT_OK
    assert "all-zero" in res.stdout, "empty news DB must warn that news context is neutral"


def test_e2e_47_bug150_with_news_without_news_db_degrades_gracefully(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BUG-150 FIXED: bare ``--with-news`` (no --news-db) resolves the default
    to artifacts/news.db; when that file is absent the command degrades to the
    documented all-zero warning (news ON == news OFF) — never the old crash
    that opened the CURRENT DIRECTORY as a sqlite database."""
    _isolated_cwd(tmp_path, monkeypatch)
    (tmp_path / "artifacts").mkdir()
    bars = _make_bars_csv(tmp_path, rows=300, seed=5)
    res = _invoke(["model-dataset-build", "--bars", str(bars), "--with-news"])
    assert res.exit_code == xc.EXIT_OK
    assert "no --news file or --news-db found" in res.stdout
    assert "Dataset built" in res.stdout


def test_e2e_48_bug151_model_train_3_uses_canonical_pipeline_module(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """BUG-151 FIXED: model-train-3 imports the CANONICAL ``three_model``
    module (train_all); the bogus ``three_model_pipeline`` import is gone and
    an invalid --variant is rejected with the usage exit code, not a crash."""
    _isolated_cwd(tmp_path, monkeypatch)
    import importlib

    mod = importlib.import_module("nexus_scalp.model_generation.three_model")
    assert hasattr(mod, "train_all")
    res = _invoke(["model-train-3", "--variant", "nope"])
    assert res.exit_code == xc.EXIT_USAGE
    assert "50d_main" in res.stdout


def test_e2e_49_model_lifecycle_error_paths_exit_runtime_with_panels(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    (tmp_path / "artifacts").mkdir()
    bars = _make_bars_csv(tmp_path, rows=300, seed=9)
    res_b = _invoke(["model-dataset-build", "--bars", str(bars)])
    dsid = re.search(r"dataset_id: (ds_[0-9a-f]+)", res_b.stdout).group(1)
    res_tpl = _invoke(
        ["model-experiment-create", "--dataset", dsid, "--template", "no_such_template"]
    )
    assert res_tpl.exit_code == xc.EXIT_RUNTIME
    assert "Unknown experiment template" in res_tpl.stdout
    res_train = _invoke(["model-train", "--experiment", "no_such_exp"])
    assert res_train.exit_code == xc.EXIT_RUNTIME
    assert "Could not load experiment/dataset" in res_train.stdout
    res_inspect = _invoke(["model-inspect", "--model", "no_such_model"])
    assert res_inspect.exit_code == xc.EXIT_RUNTIME
    assert "Model not found" in res_inspect.stdout
    res_doctor = _invoke(["model-doctor", "--model", "no_such_model"])
    assert res_doctor.exit_code == xc.EXIT_RUNTIME
    assert "MANIFEST_MISSING" in res_doctor.stdout


# ===========================================================================
# 10. db-portability (tests 50-54)
# ===========================================================================


def test_e2e_50_db_portability_status_json_provider_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    (tmp_path / "artifacts").mkdir()
    res = _invoke(["db-portability", "status", "--json"])
    assert res.exit_code == xc.EXIT_OK
    data = json.loads(res.stdout)
    assert {"provider", "supported_providers", "overall", "domains"} <= set(data)
    assert "sqlite" in data["supported_providers"]


def test_e2e_51_db_portability_switch_roundtrip_persists_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    res_pg = _invoke(["db-portability", "switch", "postgresql", "--json"])
    assert res_pg.exit_code == xc.EXIT_OK
    assert json.loads(res_pg.stdout)["restart_required"] is True
    res_sq = _invoke(["db-portability", "switch", "sqlite", "--json"])
    assert res_sq.exit_code == xc.EXIT_OK
    assert json.loads(res_sq.stdout)["provider"] == "sqlite"
    from nexus_scalp.settings.service import load_settings_service

    svc = load_settings_service()
    stored = svc.db.get("database.provider")
    svc.close()
    assert stored is not None and stored.value == "sqlite"


def test_e2e_52_db_portability_config_persists_postgres_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    res = _invoke(
        [
            "db-portability",
            "config",
            "--host",
            "db.test",
            "--port",
            "6543",
            "--database",
            "nse_t",
            "--username",
            "u1",
            "--json",
        ]
    )
    assert res.exit_code == xc.EXIT_OK
    assert json.loads(res.stdout)["success"] is True
    settings_db = Path(os.environ["NEXUS_SETTINGS_DB"])
    conn = sqlite3.connect(str(settings_db))
    try:
        row = conn.execute(
            "SELECT value FROM application_settings WHERE key='database.postgresql_config'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    assert json.loads(row[0])["host"] == "db.test"


def test_e2e_53_db_portability_test_connection_dead_host_reports_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    res = _invoke(
        ["db-portability", "test-connection", "--host", "127.0.0.1", "--port", "1", "--json"]
    )
    assert res.exit_code == xc.EXIT_OK
    data = json.loads(res.stdout)
    assert data["success"] is False
    assert data["connected"] is False


def test_e2e_54_db_portability_backup_creates_real_sqlite_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    (tmp_path / "artifacts").mkdir()
    conn = sqlite3.connect(str(tmp_path / "artifacts" / "audit.db"))
    conn.execute("CREATE TABLE IF NOT EXISTS e2e_probe (id INTEGER)")
    conn.commit()
    conn.close()
    res = _invoke(["db-portability", "backup", "--json"])
    assert res.exit_code == xc.EXIT_OK
    data = json.loads(res.stdout)
    backup = Path(data["backup_path"])
    assert backup.exists() and backup.stat().st_size > 0


# ===========================================================================
# 11. incidents (tests 55-59)
# ===========================================================================


def test_e2e_55_incidents_list_show_search_roundtrip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    _seed_incident(tmp_path)
    res_l = _invoke(["incidents", "list", "--json"])
    assert res_l.exit_code == xc.EXIT_OK
    assert "INC-E2E-TEST1" in res_l.stdout
    res_s = _invoke(["incidents", "show", "INC-E2E-TEST1", "--json"])
    assert res_s.exit_code == xc.EXIT_OK
    data = json.loads(res_s.stdout)
    assert data["incident_id"] == "INC-E2E-TEST1"
    assert data["severity"] == "HIGH"
    res_f = _invoke(["incidents", "search", "E2E_TEST_OP", "--json"])
    assert res_f.exit_code == xc.EXIT_OK
    assert "INC-E2E-TEST1" in res_f.stdout


def test_e2e_56_incidents_severity_filter_and_missing_id_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    _seed_incident(tmp_path)
    res_filter = _invoke(["incidents", "list", "--json", "--severity", "CRITICAL"])
    assert res_filter.exit_code == xc.EXIT_OK
    assert '"incidents": []' in res_filter.stdout
    res_missing = _invoke(["incidents", "show", "INC-DOES-NOT-EXIST", "--json"])
    assert res_missing.exit_code == xc.EXIT_RUNTIME
    assert "not found" in res_missing.stdout


def test_e2e_57_incidents_stats_counts_seeded_incident(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    _seed_incident(tmp_path)
    res = _invoke(["incidents", "stats", "--json"])
    assert res.exit_code == xc.EXIT_OK
    data = json.loads(res.stdout)
    assert data["counts"]["total"] >= 1


def test_e2e_58_incidents_report_and_zip_export_write_bundles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    _seed_incident(tmp_path)
    export_dir = tmp_path / "reports"
    res_r = _invoke(["incidents", "report", "INC-E2E-TEST1", "--export-dir", str(export_dir)])
    assert res_r.exit_code == xc.EXIT_OK
    assert "JSON:" in res_r.stdout and "Markdown:" in res_r.stdout
    assert list(export_dir.rglob("*.json")), "json report must be written"
    res_z = _invoke(
        ["incidents", "export", "INC-E2E-TEST1", "--zip", "--export-dir", str(export_dir)]
    )
    assert res_z.exit_code == xc.EXIT_OK
    assert list(export_dir.rglob("*.zip")), "zip bundle must be written"


def test_e2e_59_incidents_scan_read_only_and_trace_why_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    (tmp_path / "artifacts").mkdir()
    res_scan = _invoke(["incidents", "scan", "--json"])
    assert res_scan.exit_code == xc.EXIT_OK
    assert '"scan": "read-only forensic baseline' in res_scan.stdout
    res_bad = _invoke(["incidents", "trace-why", "T1", "--what", "bogus"])
    assert res_bad.exit_code == xc.EXIT_RUNTIME
    assert "unknown --what" in res_bad.stdout
    res_blocked = _invoke(["incidents", "trace-why", "T1", "--what", "blocked"])
    assert res_blocked.exit_code == xc.EXIT_OK
    assert "{" in res_blocked.stdout


# ===========================================================================
# 12. dependency intelligence (tests 60-62)
# ===========================================================================


def test_e2e_60_dependency_scan_json_reports_stats_and_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    res = _invoke(["dependency", "scan", "--json"])
    assert res.exit_code == xc.EXIT_OK
    data = json.loads(res.stdout)
    assert data["status"] == "ok"
    assert data["stats"]["files_analyzed"] > 0
    assert {"cycles", "violations", "unresolved_imports"} <= set(data["analysis"]["summary"])


def test_e2e_61_dependency_graph_out_and_validate_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    out = tmp_path / "graph.json"
    res_g = _invoke(["dependency", "graph", "--out", str(out)])
    assert res_g.exit_code == xc.EXIT_OK
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert "nodes" in data and "edges" in data
    res_v = _invoke(["dependency", "validate"])
    assert res_v.exit_code in (xc.EXIT_OK, 1)  # 1 when cycles exist (current tree)
    assert "Validation" in res_v.stdout


def test_e2e_62_dependency_impact_explain_path_with_unknown_node_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(REPO_ROOT)
    res_impact = _invoke(["dependency", "impact", "mod:nexus_scalp.cli.main"])
    assert res_impact.exit_code == xc.EXIT_OK
    assert "Impact of mod:nexus_scalp.cli.main" in res_impact.stdout
    assert "direct" in res_impact.stdout and "transitive" in res_impact.stdout
    res_unknown = _invoke(["dependency", "explain", "no:such_node"])
    assert res_unknown.exit_code == 2
    assert "Unknown node" in res_unknown.stdout
    res_path = _invoke(
        ["dependency", "path", "mod:nexus_scalp.cli.main", "mod:nexus_scalp.domain.models"]
    )
    assert res_path.exit_code == xc.EXIT_OK
    assert "->" in res_path.stdout


# ===========================================================================
# 13. analyze (tests 63-64)
# ===========================================================================


def test_e2e_63_analyze_unknown_tool_and_clean_file_contract(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    res_bad = _invoke(["analyze", "--tool", "nope", "--json"])
    assert res_bad.exit_code == 4
    assert "unknown analyzer tool" in res_bad.stdout
    clean = tmp_path / "clean.py"
    clean.write_text("x = 1\n", encoding="utf-8")
    res_ok = _invoke(["analyze", "--file", str(clean), "--tool", "ruff", "--json"])
    assert res_ok.exit_code == xc.EXIT_OK
    data = json.loads(res_ok.stdout)
    assert data["status"] == "passed"
    assert data["summary"]["errors"] == 0
    assert list(data["analyzers"]) == ["ruff"]


def test_e2e_64_analyze_dirty_file_and_strict_escalation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    dirty = tmp_path / "dirty.py"
    dirty.write_text("import os\nx: int = 'text'\n", encoding="utf-8")
    res_plain = _invoke(["analyze", "--file", str(dirty), "--json"])
    data_plain = json.loads(res_plain.stdout)
    assert res_plain.exit_code in (1, 2, 3)  # warnings / errors / infrastructure
    assert data_plain["status"] == "failed"
    res_strict = _invoke(["analyze", "--file", str(dirty), "--strict", "--json"])
    data_strict = json.loads(res_strict.stdout)
    assert data_strict["summary"]["errors"] >= data_plain["summary"]["errors"]
    assert data_strict["summary"]["warnings"] == 0


# ===========================================================================
# 14. lineage + global exit-code sweep (tests 65-66)
# ===========================================================================


def test_e2e_65_incidents_lineage_traces_pnl_and_model_output_chains(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _isolated_cwd(tmp_path, monkeypatch)
    res_pnl = _invoke(["incidents", "lineage", "pnl"])
    assert res_pnl.exit_code == xc.EXIT_OK
    assert "MT5 deal history" in res_pnl.stdout
    res_model = _invoke(["incidents", "lineage", "model_output"])
    assert res_model.exit_code == xc.EXIT_OK
    assert "/api" in res_model.stdout
    res_unknown = _invoke(["incidents", "lineage", "unknown_field"])
    assert res_unknown.exit_code == xc.EXIT_OK
    assert "unknown_field" in res_unknown.stdout


def test_e2e_66_exit_code_contract_holds_across_command_families(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Sweep asserting the documented 0/1/2/4/5 mapping on representative
    commands from every family (docs/RELEASE.md contract)."""
    _isolated_cwd(tmp_path, monkeypatch)
    (tmp_path / "artifacts").mkdir()
    # 0: success family
    assert _invoke(["version", "--plain"]).exit_code == xc.EXIT_OK
    assert _invoke(["health", "--json"]).exit_code == xc.EXIT_OK
    assert _invoke(["update", "status", "--json"]).exit_code == xc.EXIT_OK
    # 2: usage family
    assert _invoke(["start", "--mode", "nope", "--json"]).exit_code == xc.EXIT_USAGE
    assert _invoke(["release", "nope", "--json"]).exit_code == xc.EXIT_USAGE
    # 4: release verification family (payload carries the contract in --json)
    assert _invoke(["verify-release", "--root", str(tmp_path), "--json"]).exit_code == xc.EXIT_OK
    assert (
        json.loads(_invoke(["verify-release", "--root", str(tmp_path), "--json"]).stdout)[
            "exit_code"
        ]
        == xc.EXIT_RELEASE
    )
    assert _invoke(["verify-release", "--root", str(tmp_path)]).exit_code == xc.EXIT_RELEASE
    # 1: runtime/validation family
    assert (
        _invoke(["config-validate", "--config", str(tmp_path / "x.yaml"), "--json"]).exit_code
        == xc.EXIT_RUNTIME
    )
    # 5: update-not-applicable family (newer release whose asset list cannot
    # satisfy the identity/security checks → INCOMPATIBLE/SECURITY_BLOCKED)
    # BUG-154: pin the INSTALLED version below the manifest tag. The plan
    # builder short-circuits NO_UPDATE (exit 0) when target == installed,
    # so these tests must never inherit the live pyproject version
    # (same version-coupled time-bomb class as BUG-153).
    monkeypatch.setattr(
        "nexus_scalp.cli.main.get_version_info",
        lambda: {
            "version": "9.0.3",
            "architecture": "x64",
            "channel": "stable",
            "commit": None,
        },
    )
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps({"assets": [], "tag_name": "v9.0.4", "prerelease": False, "body": ""}),
        encoding="utf-8",
    )
    assert _invoke(["update", "--manifest", str(manifest), "--json"]).exit_code == xc.EXIT_UPDATE


# ---------------------------------------------------------------------------
# BUG-155 drift guards (2026-08-31 Hermes-Coder): the version-comparison
# branches of the update contract (UpdatePlanBuilder) short-circuit BEFORE
# digest/asset evaluation and MUST keep their exit semantics:
#     tag == installed         -> NO_UPDATE, exit 0
#     tag OLDER than installed -> NO_UPDATE (downgrade_blocked), exit 0
# These guards pin that contract so a future bump or refactor that moves
# these exit codes fails loudly here and forces an explicit CLI_EXIT_CODES
# v1 contract review (docs/RELEASE.md) instead of a silent behavior change.
# Both tests are hermetic: manifest path (no network), pinned
# get_version_info, evergreen against future version bumps in both
# directions (the coupling BUG-154 removed must never come back).


def test_e2e_67_update_tag_equals_installed_is_no_update_exit_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """tag == installed short-circuits NO_UPDATE with EXIT_OK (BUG-155 drift guard)."""
    _isolated_cwd(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "nexus_scalp.cli.main.get_version_info",
        lambda: {
            "version": "9.0.4",
            "architecture": "x64",
            "channel": "stable",
            "commit": None,
        },
    )
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps({"assets": [], "tag_name": "v9.0.4", "prerelease": False, "body": ""}),
        encoding="utf-8",
    )
    res = _invoke(["update", "--manifest", str(manifest), "--json"])
    assert res.exit_code == xc.EXIT_OK
    data = json.loads(res.stdout)
    assert data["status"] == "NO_UPDATE"


def test_e2e_68_update_tag_older_than_installed_downgrade_blocked_is_no_update_exit_ok(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """tag OLDER than installed -> downgrade blocked, still NO_UPDATE/EXIT_OK (BUG-155)."""
    _isolated_cwd(tmp_path, monkeypatch)
    monkeypatch.setattr(
        "nexus_scalp.cli.main.get_version_info",
        lambda: {
            "version": "9.0.4",
            "architecture": "x64",
            "channel": "stable",
            "commit": None,
        },
    )
    manifest = tmp_path / "m.json"
    manifest.write_text(
        json.dumps({"assets": [], "tag_name": "v9.0.3", "prerelease": False, "body": ""}),
        encoding="utf-8",
    )
    res = _invoke(["update", "--manifest", str(manifest), "--json"])
    assert res.exit_code == xc.EXIT_OK
    data = json.loads(res.stdout)
    assert data["status"] == "NO_UPDATE"
    assert data.get("downgrade_blocked") is True
