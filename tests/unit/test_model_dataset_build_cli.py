"""Regression tests for model-dataset-build CLI command.

Ensures directory defaults, missing files, and invalid schemas produce
clean, actionable usage/runtime panels without raw tracebacks.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from nexus_scalp.cli.main import app
from nexus_scalp.release import exit_codes as xc


def test_model_dataset_build_no_bars_refuses_directory(tmp_path: Path) -> None:
    """Invoking model-dataset-build without --bars in a directory containing

    mixed files must exit with xc.EXIT_USAGE and a clear error panel, NOT an
    unhandled polars.exceptions.InvalidOperationError.
    """
    runner = CliRunner()
    result = runner.invoke(app, ["model-dataset-build"])

    assert result.exit_code == xc.EXIT_USAGE
    assert "No bars file given" in result.stdout
    assert "--bars points at a directory" in result.stdout
    assert "nexus data-restore" in result.stdout


def test_model_dataset_build_explicit_directory_refused(tmp_path: Path) -> None:
    """Explicitly passing a directory to --bars must be rejected cleanly."""
    runner = CliRunner()
    result = runner.invoke(app, ["model-dataset-build", "--bars", str(tmp_path)])

    assert result.exit_code == xc.EXIT_USAGE
    assert "No bars file given" in result.stdout


def test_model_dataset_build_missing_file_reports_clean_error(tmp_path: Path) -> None:
    """A non-existent file path must exit cleanly with an error panel."""
    missing = tmp_path / "absent.parquet"
    runner = CliRunner()
    result = runner.invoke(app, ["model-dataset-build", "--bars", str(missing)])

    assert result.exit_code == xc.EXIT_RUNTIME
    assert "No bars file" in result.stdout
