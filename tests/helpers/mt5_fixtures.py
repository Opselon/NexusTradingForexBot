"""Shared helpers: load the REAL MT5 capture fixtures.

The fixtures under tests/fixtures/mt5/ were captured READ-ONLY from the live
MetaQuotes-Demo terminal on 2026-08-17 (see capture_mt5_contract.py, deleted
after capture). Every value is a real broker response — no synthetic data.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "mt5"

#: Object keys that are namedtuple plumbing, never broker data.
_SKIP_KEYS = frozenset(
    {
        "count",
        "index",
        "n_fields",
        "n_sequence_fields",
        "n_unnamed_fields",
        "_none",
    }
)


def fixture_path(name: str) -> Path:
    path = _FIXTURE_DIR / f"{name}.json"
    assert path.exists(), f"fixture missing: {path} (run the capture script first)"
    return path


def load_fixture(name: str) -> dict[str, Any]:
    return json.loads(fixture_path(name).read_text(encoding="utf-8"))


def fixture_objects(name: str) -> list[dict[str, Any]]:
    """Returns the cleaned per-object field dicts (value-only)."""
    payload = load_fixture(name)
    out: list[dict[str, Any]] = []
    for obj in payload.get("objects", []):
        cleaned = {
            k: v["value"] if isinstance(v, dict) else v
            for k, v in obj.items()
            if k not in _SKIP_KEYS
        }
        out.append(cleaned)
    return out


def fixture_object(name: str) -> dict[str, Any]:
    """Returns the cleaned field dict for single-object fixtures."""
    payload = load_fixture(name)
    obj = payload.get("object", {})
    return {k: v["value"] for k, v in obj.items() if k not in _SKIP_KEYS}


def count_objects(name: str) -> int:
    return int(load_fixture(name).get("count", 0))


# ---------------------------------------------------------------------------
# Canonical expected values computed from the REAL captures (deterministic).
# ---------------------------------------------------------------------------
EXPECTED = {
    "account_login": 10011755849,
    "deals_count": 88,
    "orders_count": 136,
    # 44 positions in the deal stream; 2 have only ENTRY deals (still open at
    # capture time, no realized result) -> only their entry deals persist.
    "positions_count": 44,
    "open_positions_in_window": 2,
    "closed_trades": 42,
    "trades_net_total": 741.05,
    "wins": 37,
    "losses": 5,
    "breakeven": 0,  # the two zero-net positions are still OPEN (no OUT deal)
    "best_trade": 178.11,
    "worst_trade": -11.60,
    "partial_close_position": 152487940044,
    "partial_close_gross": 178.11,
    "net_pnl_winning_close": 87.62,  # single partial-close deal profit
}
