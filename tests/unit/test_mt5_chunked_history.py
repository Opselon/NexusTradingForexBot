"""Unit tests for MT5 chunked history, SQLite provider stickiness, and log root.

Guards against:
- MT5 terminal returning error_code=-2 on count=100_000
- Persisted database.postgresql_config overriding selected SQLite provider
- Unconfigured logging leaving the logs/ directory empty
"""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from nexus_scalp.database.config import DatabaseConfig, DatabaseProvider, load_database_config
from nexus_scalp.observability.logging import configure_logging, get_logger


def test_load_database_config_preserves_sqlite_when_pg_config_present(tmp_path: Path) -> None:
    """When database.provider is 'sqlite', persisted PG config must not override it."""
    from nexus_scalp.settings.service import SettingsDatabase

    db_path = tmp_path / "app_settings.db"
    sdb = SettingsDatabase(db_path=db_path)
    sdb.set("database.provider", "sqlite", actor="test")
    # Simulate a stored postgresql_config dict
    pg_data = {
        "host": "localhost",
        "port": 5432,
        "database": "nexusdb",
        "username": "postgres",
        "password_secret": "db.postgresql.password",
    }
    sdb.set("database.postgresql_config", pg_data, actor="test")
    sdb.close()

    cfg = load_database_config(domain="audit", settings_db_path=str(db_path), env={})
    assert cfg.provider == DatabaseProvider.SQLITE
    assert not cfg.is_postgresql


def test_load_database_config_applies_pg_when_provider_is_postgresql(tmp_path: Path) -> None:
    """When database.provider is 'postgresql', persisted PG config is applied."""
    from nexus_scalp.settings.service import SettingsDatabase

    db_path = tmp_path / "app_settings.db"
    sdb = SettingsDatabase(db_path=db_path)
    sdb.set("database.provider", "postgresql", actor="test")
    pg_data = {
        "host": "remotehost",
        "port": 5433,
        "database": "customdb",
        "username": "customuser",
        "password_secret": "db.postgresql.password",
    }
    sdb.set("database.postgresql_config", pg_data, actor="test")
    sdb.close()

    cfg = load_database_config(domain="audit", settings_db_path=str(db_path), env={})
    assert cfg.provider == DatabaseProvider.POSTGRESQL
    assert cfg.host == "remotehost"
    assert cfg.port == 5433
    assert cfg.database == "customdb"


def test_mt5_adapter_get_rate_history_chunks_when_count_exceeds_50k() -> None:
    """Requests for 100k bars are chunked into <=50k requests to satisfy MT5 limits."""
    from nexus_scalp.adapters.mt5.mt5_adapter import DirectMT5Adapter

    # Mock the MetaTrader5 module
    mock_mt5 = MagicMock()
    mock_mt5.TIMEFRAME_M1 = 1
    mock_mt5.last_error.return_value = (1, "Success")

    # Generate mock structured record array chunks
    dt = np.dtype(
        [
            ("time", "<i8"),
            ("open", "<f8"),
            ("high", "<f8"),
            ("low", "<f8"),
            ("close", "<f8"),
            ("tick_volume", "<i8"),
            ("spread", "<i4"),
            ("real_volume", "<i8"),
        ]
    )

    def fake_copy(sym: str, tf: int, offset: int, count: int) -> np.ndarray:
        # Create monotonic timestamps
        arr = np.zeros(count, dtype=dt)
        # offset 0 has newer times, higher offset has older times
        base_time = 1_000_000_000 - offset * 60
        arr["time"] = base_time + np.arange(count) * 60
        arr["open"] = 2000.0
        arr["high"] = 2001.0
        arr["low"] = 1999.0
        arr["close"] = 2000.5
        arr["spread"] = 10
        return arr

    mock_mt5.copy_rates_from_pos.side_effect = fake_copy

    with (
        patch("nexus_scalp.adapters.mt5.mt5_adapter.mt5", mock_mt5),
        patch("nexus_scalp.adapters.mt5.mt5_adapter.HAS_NATIVE_MT5", True),
    ):
        adapter = DirectMT5Adapter()
        adapter._connected = True
        bars = adapter.get_rate_history("XAUUSD", timeframe="M1", count=100_000)

        # Must have called copy_rates_from_pos twice: chunk 1 (50000), chunk 2 (50000)
        assert mock_mt5.copy_rates_from_pos.call_count == 2
        assert len(bars) == 100_000
        # Times must be strictly increasing
        times = [b.time for b in bars]
        assert all(times[i] < times[i + 1] for i in range(len(times) - 1))


def test_configure_logging_creates_log_directory_and_writes_files(tmp_path: Path) -> None:
    """configure_logging creates log directory and dated log file on record."""
    log_dir = tmp_path / "test_logs"
    configure_logging(log_level="INFO", log_file_path=log_dir)

    test_logger = get_logger("nexus_test.logging")
    test_logger.info("TEST_LOG_EVENT", key="value_123")

    # Verify log_dir was created and contains log files
    assert log_dir.is_dir()
    logs = list(log_dir.rglob("*.log"))
    assert len(logs) > 0
    content = "".join(f.read_text(encoding="utf-8", errors="replace") for f in logs)
    assert "TEST_LOG_EVENT" in content
