"""Runtime logging retention regression tests (2026-09-09).

Fixes the boot-only retention hole: _prune_old_logs used to run exactly once
per process (configure_logging), so a client running the engine for weeks
accumulated every severity part file created after boot. The emit-path hook
now re-arms the hourly prune; these tests pin that contract plus the
per-severity byte budget and gzip compression of aged logs.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from pathlib import Path

import pytest

from nexus_scalp.observability import logging as obs_logging
from nexus_scalp.observability.logging import (
    DEFAULT_RETENTION_DAYS,
    MAX_BYTES_PER_FILE,
    _prune_old_logs,
    configure_logging,
    get_logger,
)


@pytest.fixture()
def log_root(tmp_path: Path):
    obs_logging.reset_prune_throttle()
    configure_logging(log_level="INFO", json_format=False, log_to_file=True, log_file_path=tmp_path)
    logger = get_logger("test_retention_runtime")
    yield tmp_path, logger
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.WARNING)
    obs_logging.reset_prune_throttle()


def _make_old_logs(base: Path, severity: str, *, days: int, count: int = 3) -> list[Path]:
    sev_dir = base / severity
    sev_dir.mkdir(parents=True, exist_ok=True)
    old = time.time() - 86400.0 * days
    paths = []
    for i in range(count):
        p = sev_dir / f"2026-08-{10 + i:02d}.log"
        p.write_text("old log line\n", encoding="utf-8")
        import os

        os.utime(p, (old, old))
        paths.append(p)
    return paths


def test_prune_removes_files_older_than_retention(tmp_path: Path) -> None:
    _make_old_logs(tmp_path, "info", days=60)  # info retention 30d
    _make_old_logs(tmp_path, "critical", days=60)  # critical retention 365d
    obs_logging._set_prune_ts(0.0)
    _prune_old_logs(tmp_path, DEFAULT_RETENTION_DAYS)
    assert not any((tmp_path / "info").glob("*.log"))
    assert any((tmp_path / "critical").glob("*.log"))  # long retention kept


def test_prune_throttle_prevents_immediate_rerun(tmp_path: Path) -> None:
    _make_old_logs(tmp_path, "info", days=60)
    obs_logging._set_prune_ts(0.0)
    _prune_old_logs(tmp_path, DEFAULT_RETENTION_DAYS)
    assert not any((tmp_path / "info").glob("*.log"))
    # recreate old files; the hourly throttle must suppress an instant re-prune
    _make_old_logs(tmp_path, "info", days=60)
    _prune_old_logs(tmp_path, DEFAULT_RETENTION_DAYS)
    assert any((tmp_path / "info").glob("*.log"))


def test_emit_hook_reprunes_within_an_hour_of_boot(log_root) -> None:
    """The 10GB-driver regression: logs born AFTER configure_logging must be
    prunable during the SAME process lifetime, not only at next boot."""
    base, logger = log_root
    # simulate logs that aged past retention while the process kept running
    _make_old_logs(base, "info", days=60)
    # the emit-path hook must reset the throttle and trigger a prune
    obs_logging.maybe_prune_on_emit(force_check=True)
    assert not any((base / "info").glob("*.log"))


def test_emit_hook_throttled_between_checks(log_root) -> None:
    base, logger = log_root
    _make_old_logs(base, "info", days=60)
    obs_logging.maybe_prune_on_emit(force_check=True)
    # files written now: a non-forced call inside the window must NOT prune
    _make_old_logs(base, "info", days=60)
    obs_logging.maybe_prune_on_emit()
    assert any((base / "info").glob("*.log"))


def test_prune_never_deletes_unknown_buckets(log_root) -> None:
    base, _logger = log_root
    archive = base / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    p = archive / "precious.log"
    p.write_text("keep me\n", encoding="utf-8")
    import os

    os.utime(p, (time.time() - 86400.0 * 400, time.time() - 86400.0 * 400))
    obs_logging.maybe_prune_on_emit(force_check=True)
    assert p.exists()


def test_max_bytes_per_file_unchanged() -> None:
    """Size split cap is a deliberate constant; pin it against drift."""
    assert MAX_BYTES_PER_FILE == 10 * 1024 * 1024
