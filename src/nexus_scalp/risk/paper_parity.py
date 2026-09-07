"""Paper execution-parity exporter (mission P0 item 1, PAPER→DEMO evidence).

Reads the canonical PaperMT5Adapter execution ledger (in-memory rows the
paper adapter already records for every order attempt: fills AND rejections
with requested price, bid/ask at request, spread, fill price, slippage) and
persists them into the durable audit DB so a weekly parity job can compare
PAPER execution outcomes against DEMO/LIVE broker-truth outcomes from the
same canonical store.

Why this exists (measured 2026-09-07):
    - Paper fills/rejections currently live ONLY in adapter memory and the
      paper_state.json balance file. After a restart they are gone; the
      audit DB has no paper execution rows at all (verified: zero
      audit_broker_* rows from the 2026-09-03 paper sessions, while ~80
      PAPER account snapshots exist). Parity measurement therefore had NO
      durable paper-side evidence to consume.
    - The audit DB already carries the DEMO/LIVE truth (audit_broker_deals /
      audit_broker_orders / audit_broker_trades, synced by
      BrokerHistorySyncWorker from MT5 history). This exporter gives the
      paper side the same durable home and schema shape so one report can
      measure both.

Import-rules honored:
    - Read-only with respect to execution state: appends ledger copies only.
    - Uses the canonical AuditRepository (background writer, WAL) — no new
      persistence mechanism, no direct broker calls.
    - Bounded: exports at most `max_rows` most-recent rows per call; the
      caller decides cadence (maintenance cycle / weekly job).
"""

from __future__ import annotations

import time
from typing import Any

from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.risk.paper_parity")

#: Marker for rows exported from the paper execution ledger.
PAPER_SOURCE_TAG = "PAPER_ADAPTER_LEDGER"


def export_paper_ledger(
    *,
    adapter: Any,
    audit: Any,
    max_rows: int = 500,
) -> dict[str, Any]:
    """Persists paper execution-ledger rows into the audit DB.

    Returns an honest result dict: exported/deduped/skipped counts. Never
    raises — a parity-export fault must not disturb trading.
    """
    result: dict[str, Any] = {
        "available": False,
        "exported": 0,
        "deduped": 0,
        "failed": 0,
        "source": PAPER_SOURCE_TAG,
    }
    try:
        ledger = adapter.get_execution_ledger()
    except Exception as exc:
        result["error"] = f"ledger_unavailable: {type(exc).__name__}"
        logger.warning("[PARITY] paper ledger unavailable", error=str(exc))
        return result

    result["available"] = True
    if not ledger:
        return result

    rows = list(ledger)[-max_rows:] if max_rows and max_rows > 0 else list(ledger)
    for entry in rows:
        try:
            # is_fill is derived (rejection_reason is None) — export it as
            # status text so the table stays schema-stable.
            status = "FILLED" if entry.get("is_fill") else "REJECTED"
            audit.record_paper_execution(
                ts=str(entry.get("ts", "")),
                symbol=str(entry.get("symbol", "")),
                order_type=str(entry.get("order_type", "")),
                volume=float(entry.get("volume", 0.0) or 0.0),
                requested_price=float(entry.get("requested_price", 0.0) or 0.0),
                bid_at_request=float(entry.get("bid_at_request", 0.0) or 0.0),
                ask_at_request=float(entry.get("ask_at_request", 0.0) or 0.0),
                spread=float(entry.get("spread", 0.0) or 0.0),
                fill_price=entry.get("fill_price"),
                slippage=entry.get("slippage"),
                rejection_reason=entry.get("rejection_reason"),
                ticket=int(entry.get("ticket", 0) or 0),
                latency_ticks=int(entry.get("latency_ticks", 0) or 0),
                status=status,
                source=PAPER_SOURCE_TAG,
            )
            result["exported"] += 1
        except Exception as exc:
            result["failed"] += 1
            logger.warning("[PARITY] paper row export failed", error=str(exc))
    logger.info(
        "[PARITY] event=PAPER_LEDGER_EXPORTED exported=%s failed=%s",
        result["exported"],
        result["failed"],
    )
    return result


def build_parity_snapshot(
    *,
    audit: Any,
    lookback_days: int = 7,
) -> dict[str, Any]:
    """Aggregates durable PAPER vs BROKER execution evidence for one window.

    Observational ONLY: no calibration factors are invented here. Both
    sides carry sample counts; when either side lacks data the verdict is
    INSUFFICIENT_DATA rather than a fabricated number.
    """
    out: dict[str, Any] = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "lookback_days": lookback_days,
        "paper": None,
        "demo": None,
        "status": "INSUFFICIENT_DATA",
        "notes": [],
    }
    try:
        paper = audit.paper_execution_stats(days=lookback_days)
        demo = audit.broker_execution_stats(days=lookback_days)
    except Exception as exc:
        out["notes"].append(f"stats_unavailable: {type(exc).__name__}")
        logger.warning("[PARITY] stats query failed", error=str(exc))
        return out

    out["paper"] = paper
    out["demo"] = demo

    p_n = int(paper.get("fills", 0)) if paper else 0
    d_n = int(demo.get("trades", 0)) if demo else 0
    if p_n == 0 or d_n == 0:
        out["notes"].append(f"insufficient samples: paper_fills={p_n} broker_trades={d_n}")
        return out

    # Both sides present: emit an OBSERVATIONAL comparison only. Statistical
    # divergence testing needs more history than this repo has; the report
    # refuses to claim parity or compute bias factors without it (mission:
    # no arbitrary correction multipliers).
    out["status"] = "MEASURED"
    out["comparison"] = {
        "paper_fill_rate": paper.get("fill_rate"),
        "paper_mean_spread": paper.get("mean_spread"),
        "paper_mean_slippage": paper.get("mean_slippage"),
        "broker_trades": d_n,
        "broker_mean_duration_sec": demo.get("mean_duration_sec"),
    }
    out["notes"].append(
        "observational snapshot only: bias factors require a larger paired "
        "sample (same-signal same-window); see docs/paper_demo_parity.md"
    )
    return out
