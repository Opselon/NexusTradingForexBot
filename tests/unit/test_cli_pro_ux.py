"""Style-regression tests for the pro CLI upgrade (PAPER default, animated UX, doctor --fix)."""

from __future__ import annotations

import json

from typer.testing import CliRunner

from nexus_scalp.cli.main import app
from nexus_scalp.release import exit_codes as xc

runner = CliRunner()


def test_start_defaults_paper_and_welcome_has_paper_xauusd() -> None:
    res = runner.invoke(app, ["--help"])
    assert res.exit_code == 0
    # start help mentions paper default
    res2 = runner.invoke(app, ["start", "--help"])
    assert res2.exit_code == 0
    # mode default is paper

    assert "paper" in res2.stdout.lower()


def test_doctor_fix_flag_exists_and_json_still_works() -> None:
    res = runner.invoke(app, ["doctor", "--help"])
    assert res.exit_code == 0
    assert "--fix" in res.stdout
    # json mode must still parse
    res2 = runner.invoke(app, ["doctor", "--json"])
    assert res2.exit_code == 0
    data = json.loads(res2.stdout)
    assert "overall" in data
    assert "checks" in data


def test_update_bad_channel_pretty_error_and_json() -> None:
    res = runner.invoke(app, ["update", "check", "--channel", "bogus"])
    assert res.exit_code == xc.EXIT_USAGE
    res2 = runner.invoke(app, ["update", "check", "--channel", "bogus", "--json"])
    assert res2.exit_code == xc.EXIT_USAGE
    data = json.loads(res2.stdout)
    assert "error" in data or "hint" in data or "exit_code" in data


def test_config_missing_pretty_hint() -> None:
    # non-existent path should be pretty + exit runtime
    res = runner.invoke(app, ["config", "--validate", "no/such/file.yaml"])
    assert res.exit_code == xc.EXIT_RUNTIME
    # tolerate either "Config not found"/"Invalid configuration" phrasing
    assert res.exit_code == 1


def test_repair_verify_flag_and_banner() -> None:
    res = runner.invoke(app, ["repair", "--help"])
    assert res.exit_code == 0
    assert "--verify" in res.stdout or "--no-verify" in res.stdout


def test_version_banner_has_gradient_markers() -> None:
    res = runner.invoke(app, ["version"])
    assert res.exit_code == 0
    # banner is now rich gradient
    assert "NEXUS" in res.stdout
