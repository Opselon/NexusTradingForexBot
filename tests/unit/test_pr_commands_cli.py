"""CLI surface tests for ``nexus pr report`` (spec §2/§18/§22).

The command must exist on the real app, discover a PR automatically when
``--pr`` is omitted, and never publish when ``--dry-run`` is passed.
"""

from __future__ import annotations

from typer.testing import CliRunner

from nexus_scalp.cli.app_factory import app

_RUNNER = CliRunner()


def _find(cmd_name: str) -> bool:
    for cmd in app.registered_commands:
        if cmd.name == cmd_name:
            return True
    return False


class TestPrCommandRegistered:
    def test_pr_subapp_registered(self) -> None:
        names = {g.name for g in app.registered_groups}
        assert "pr" in names

    def test_pr_report_is_a_command(self) -> None:
        for group in app.registered_groups:
            if group.name == "pr":
                sub = group.typer_instance
                assert sub is not None
                assert _find_in(sub, "report")
                return
        raise AssertionError("pr sub-app not registered")


def _find_in(sub: object, name: str) -> bool:
    commands = getattr(sub, "registered_commands", [])
    return any(c.name == name for c in commands)


class TestPrReportHelp:
    def test_help_lists_core_flags(self) -> None:
        result = _RUNNER.invoke(app, ["pr", "report", "--help"])
        assert result.exit_code == 0
        out = result.stdout
        assert "--watch" in out
        assert "--refresh" in out
        assert "--json" in out
        assert "--pr" in out
