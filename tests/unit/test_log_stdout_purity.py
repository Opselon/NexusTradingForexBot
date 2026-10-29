"""Console logs must never corrupt machine-readable CLI stdout.

``--json`` commands are parsed with ``json.loads(result.stdout)``. Any log
record emitted before the payload breaks those callers, and it did: an
unconfigured structlog default (``PrintLoggerFactory`` -> stdout) let a
migration debug record precede ``health --json`` and
``model-train-dataset --json`` output.
"""

from __future__ import annotations

import json
import logging

import structlog

from nexus_scalp.observability import logging as obs_logging


def test_get_logger_defaults_to_stderr_when_unconfigured(monkeypatch) -> None:
    """The bare-CLI default must not write records to stdout."""
    monkeypatch.setattr(structlog, "is_configured", lambda: False)

    configured: dict[str, object] = {}

    class _Recorder:
        @staticmethod
        def configure(**kwargs: object) -> None:
            configured.update(kwargs)

    monkeypatch.setattr(obs_logging.structlog, "configure", _Recorder.configure)
    obs_logging.get_logger("test.stdout.purity")

    factory = configured.get("logger_factory")
    assert factory is not None, "an unconfigured logger must install a factory"
    assert getattr(factory, "_file", None) is obs_logging.sys.stderr


def test_db_driver_connect_log_carries_no_connection_url() -> None:
    """CodeQL taints any password source reaching a log call, so the connect
    record must log host/port/db rather than the DSN."""
    from pathlib import Path

    source = Path(obs_logging.__file__).resolve().parents[1] / "database" / "drivers"
    text = (source / "postgres_driver.py").read_text(encoding="utf-8")
    assert "mask_url_password(url)" not in text.split("postgres connect")[1][:400]


def test_health_json_stdout_is_pure_json() -> None:
    from typer.testing import CliRunner

    from nexus_scalp.cli.main import app

    result = CliRunner().invoke(app, ["health", "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["overall"] in ("READY", "DEGRADED", "NOT READY")
    assert logging.getLogger("nexus_scalp").handlers is not None
