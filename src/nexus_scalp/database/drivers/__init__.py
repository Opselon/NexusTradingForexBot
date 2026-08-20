"""Driver registry — build the provider driver for a DatabaseConfig.

Usage::

    from nexus_scalp.database.drivers import get_driver
    driver = get_driver(cfg)          # SQLite or PostgreSQL
    rows = driver.query("SELECT ...", args)

The driver is the persistence abstraction boundary: business logic calls
``driver.*`` and never inspects the provider.
"""

from __future__ import annotations

from nexus_scalp.database.config import DatabaseConfig
from nexus_scalp.database.drivers.base import DatabaseDriver
from nexus_scalp.database.drivers.postgres_driver import PostgreSQLDriver
from nexus_scalp.database.drivers.sqlite_driver import SQLiteDriver


def get_driver(config: DatabaseConfig) -> DatabaseDriver:
    """Instantiate the driver matching ``config.provider``."""
    if config.is_postgresql:
        return PostgreSQLDriver(config)
    return SQLiteDriver(config)


def driver_available(config: DatabaseConfig) -> bool:
    """True when the driver for the config can be used right now.

    PostgreSQL requires the optional ``psycopg`` dependency.
    """
    if not config.is_postgresql:
        return True
    return PostgreSQLDriver.available()


__all__ = [
    "DatabaseDriver",
    "DatabaseConfig",
    "SQLiteDriver",
    "PostgreSQLDriver",
    "get_driver",
    "driver_available",
]