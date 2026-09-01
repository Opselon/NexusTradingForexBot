"""Pro-auto console telemetry ring (Agent-5 P2-B modularization).

Extracted VERBATIM from news/pro_auto.py (CHG modularization program): the
bounded observability ring backing the News AI console API
(/api/news/auto-analysis + console history). Self-contained: module state
(_CONSOLE deque maxlen=500, _CONSOLE_SEQ) + push/read/status.

USED BY: news/pro_auto.py (facade re-exports get_console_history /
console_status for compatibility; call sites use _console_push), and
web/news_intelligence_routes.py imports read via the facade.
DO-NOT-PUT-HERE: provider status, article selection, LLM analysis.
"""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
from typing import Any

_CONSOLE: deque[dict[str, Any]] = deque(maxlen=500)
_CONSOLE_SEQ: int = 0


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def push_console(entry: dict[str, Any]) -> None:
    """Append one console entry (seq + ts defaulted; ring-bounded)."""
    global _CONSOLE_SEQ  # noqa: PLW0603
    _CONSOLE_SEQ += 1
    entry.setdefault("seq", _CONSOLE_SEQ)
    entry.setdefault("ts", _now_iso())
    _CONSOLE.append(entry)


def get_console_history(limit: int = 200, since_seq: int = 0) -> list[dict[str, Any]]:
    """Return console entries after since_seq (bounded, never unbounded)."""
    bounded = max(1, min(int(limit), 500))
    try:
        since = int(since_seq)
    except Exception:
        since = 0
    out: list[dict[str, Any]] = []
    for e in list(_CONSOLE):
        if int(e.get("seq", 0)) > since:
            out.append(dict(e))
            if len(out) >= bounded:
                break
    return out


def console_status() -> dict[str, Any]:
    return {"size": len(_CONSOLE), "latest_seq": _CONSOLE_SEQ, "available": True}


def console_latest_seq() -> int:
    """Latest assigned sequence number (run-cycle summary reads this)."""
    return _CONSOLE_SEQ


# The historical private name used by pro_auto call sites; kept as an alias
# so the extraction does not rename call sites (zero-churn move).
_console_push = push_console


def reset_console_for_tests() -> None:
    """Test isolation: clear ring + seq (NOT used by production code)."""
    global _CONSOLE_SEQ  # noqa: PLW0603
    _CONSOLE.clear()
    _CONSOLE_SEQ = 0
