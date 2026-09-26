"""Lane G (live wave) - shared helpers for probing the LIVE nexusdb.

No NSE_DATABASE__PG_* env vars are exported: the settings DB is pinned to
localhost:5432/nexusdb and everything resolves through load_database_config.
"""

from __future__ import annotations

import time
from typing import Any

from nexus_scalp.database.config import (
    DatabaseConfig,
    build_postgres_url,
    load_database_config,
)
from nexus_scalp.settings.secret_store import SecureSecretStore

LIVE_DOMAINS = (
    "audit",
    "news",
    "candle_intel",
    "ops_shadow",
    "ops_hygiene",
    "strategies",
    "marketplace",
    "models",
)


def live_dsn(domain: str = "audit") -> str:
    """Real postgresql:// URL for the canonical target (password injected)."""
    return build_postgres_url(load_database_config(domain), SecureSecretStore())


def connect(domain: str = "audit"):
    import psycopg

    return psycopg.connect(live_dsn(domain))


def table_counts() -> tuple[dict[str, int], list[str]]:
    """Per-table row counts on the live cluster, public schema."""
    counts: dict[str, int] = {}
    errors: list[str] = []
    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema='public' AND table_type='BASE TABLE' "
            "ORDER BY table_name"
        )
        names = [r[0] for r in cur.fetchall()]
        for name in names:
            try:
                cur.execute(f'SELECT count(*) FROM "{name}"')
                counts[name] = int(cur.fetchone()[0])
            except Exception as exc:
                conn.rollback()
                errors.append(f"{name}: {type(exc).__name__}: {exc}")
    return counts, errors


class Stopwatch:
    """Monotonic ms timing for write/read probes."""

    def __init__(self) -> None:
        self.samples: list[float] = []

    def lap(self) -> float:
        self.samples.append(time.perf_counter())
        return self.samples[-1]

    def start(self) -> Stopwatch:
        self.samples = [time.perf_counter()]
        return self

    @property
    def intervals_ms(self) -> list[float]:
        return [
            (b - a) * 1000.0 for a, b in zip(self.samples, self.samples[1:], strict=True)
        ]


def stats_ms(samples: list[float]) -> dict[str, float]:
    """min/avg/p95/max in milliseconds."""
    if not samples:
        return {"n": 0, "min_ms": 0.0, "avg_ms": 0.0, "p95_ms": 0.0, "max_ms": 0.0}
    ordered = sorted(samples)
    idx = max(0, min(len(ordered) - 1, round(0.95 * (len(ordered) - 1))))
    return {
        "n": len(samples),
        "min_ms": round(min(samples), 3),
        "avg_ms": round(sum(samples) / len(samples), 3),
        "p95_ms": round(ordered[idx], 3),
        "max_ms": round(max(samples), 3),
    }


def cfg_summary(domain: str) -> dict[str, Any]:
    c: DatabaseConfig = load_database_config(domain)
    return {
        "domain": domain,
        "provider": c.provider.value if hasattr(c.provider, "value") else str(c.provider),
        "host": c.host,
        "port": c.port,
        "database": c.database,
        "username": c.username,
        "is_postgresql": c.is_postgresql,
    }
