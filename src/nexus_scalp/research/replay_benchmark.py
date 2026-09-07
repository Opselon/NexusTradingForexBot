"""Replay Candidate Benchmark (research/training-parity P0).

CAUSAL COUNTERFACTUAL BENCHMARK SUBSTRATE.

The experience ledger records what the engine ACTUALLY did — it cannot answer
"what would the challenger have done on the same historical stream?". Grading
candidates only where the champion traded is selection-biased. This module
evaluates candidate policies on the SAME causal historical stream through the
ONE certified replay engine (research.streaming_replay, CHG-0035):

    historical bars/ticks -> StreamingReplayEngine
        -> causal feature generation -> frozen candidate model
        -> production SignalPolicy -> RiskEngine -> simulated execution
        -> deterministic ledger

HARD RULES:

* SAME STREAM for every candidate: one frozen event record list is projected
  through one BarEventSource/TickEventSource semantics per run; each
  candidate gets a FRESH engine (frozen model/policy binding). The event
  hashes of all candidate runs must agree — enforced here (mismatch aborts).
* NO FUTURE INFORMATION: inherited from the engine (logical clock, causal
  features, prefix-streamed events). This module adds no data path.
* NO SIDE EFFECTS: no ledger writes, no adapter import, no broker surface,
  no live state mutation, no champion touch. Research-only by construction.
* DETERMINISTIC: same events + same candidates + same config => identical
  per-candidate ledger hashes and identical benchmark_id.
* NOT TRAINING DATA: benchmark output is evaluation evidence only. It is
  never appended to the experience ledger and never enters any training
  dataset (see TRAINING/BENCHMARK SEPARATION below).

EXECUTED vs COUNTERFACTUAL (explicit semantics, never conflated):
  * EXECUTED_HISTORICAL_TRADES live in the experience ledger (real fills).
  * Every trade reported here is a COUNTERFACTUAL_REPLAY trade — a simulated
    decision on the historical stream under the frozen execution contract.
  * evaluation_mode="COUNTERFACTUAL_REPLAY" is stamped on every candidate
    block and on the artifact.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from nexus_scalp.observability.logging import get_logger
from nexus_scalp.research.event_source import BarEventSource, HistoricalEventSource, TickEventSource
from nexus_scalp.research.streaming_replay import (
    ReplayExecutionConfig,
    ReplayRunResult,
    ReplaySessionConfig,
    StreamingReplayEngine,
)

logger = get_logger("nexus_scalp.research.replay_benchmark")

#: Evaluation-mode stamp carried on every candidate block + the artifact.
EVALUATION_MODE: str = "COUNTERFACTUAL_REPLAY"

#: Valid candidate roles (A/B/C benchmark modes).
VALID_ROLES: tuple[str, ...] = ("CHAMPION", "CHALLENGER", "CONTROL")


# ---------------------------------------------------------------------------
# Candidate identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReplayCandidate:
    """One candidate evaluated over the shared replay stream.

    `artifact_path` must point at a loadable model bundle (model.pt +
    model.scaler.npz sidecar). `policy_params` freezes the SignalPolicy
    constructor params for this candidate (fingerprinted by the engine).
    """

    name: str
    role: str  # CHAMPION | CHALLENGER | CONTROL
    artifact_path: str
    policy_params: dict[str, Any] = field(default_factory=dict)

    def identity(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "artifact_path": str(self.artifact_path),
            "policy_params": dict(sorted(self.policy_params.items())),
        }


# ---------------------------------------------------------------------------
# Per-candidate comparable metrics (from the engine's own ledger)
# ---------------------------------------------------------------------------


def _ledger_content_hash(trades: list[dict[str, Any]]) -> str:
    """Deterministic CONTENT identity of a simulated ledger.

    The engine's own ``ledger_hash`` embeds the run_id label (trade ids are
    ``<run_id>-T####``), so it is stable per-run but not comparable across
    run labels. The benchmark identity must depend on CONTENT ONLY:
    direction, volume, prices, timestamps, exit reason, PnL and excursions —
    never on the run label. Same stream + same candidate bytes + same config
    => the same content hash regardless of run_id.
    """
    h = hashlib.sha256()
    for t in trades:
        h.update(
            "|".join(
                (
                    str(t.get("direction", "")),
                    f"{float(t.get('volume', 0.0)):.6f}",
                    str(t.get("entry_time", "")),
                    f"{float(t.get('entry_price', 0.0)):.6f}",
                    str(t.get("exit_time", "")),
                    f"{float(t.get('exit_price', 0.0)):.6f}",
                    str(t.get("exit_reason", "")),
                    f"{float(t.get('pnl_usd', 0.0)):.8f}",
                    f"{float(t.get('mae_usd', 0.0)):.8f}",
                    f"{float(t.get('mfe_usd', 0.0)):.8f}",
                )
            ).encode("utf-8")
        )
    return h.hexdigest()[:32]


def _metrics_from_run(
    result: ReplayRunResult,
    cfg: ReplaySessionConfig,
) -> dict[str, Any]:
    """Comparable metric block for ONE candidate run (spec: benchmark outputs).

    PnL from the engine is already net of direction-aware spread fills
    (BUY entry ASK / SELL exit BID and vice versa) under the frozen execution
    contract; the entry-spread sum is reported separately as an approximate
    friction figure (labeled as such — it is not a filled-cost truth).
    """
    trades = result.trades
    pnls = [float(t.get("pnl_usd", 0.0)) for t in trades]
    trade_count = len(trades)
    wins = sum(1 for p in pnls if p > 0.0)
    losses = sum(1 for p in pnls if p < 0.0)
    win_rate = (wins / trade_count) if trade_count else 0.0

    # Equity-path drawdown over closed trades (starting equity anchor).
    equity = float(cfg.starting_equity_usd)
    peak = equity
    max_dd = 0.0
    for p in pnls:
        equity += p
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)

    # Confidence statistics from the bounded decision trace (observability).
    trace = result.decision_trace
    all_confs = [float(r.get("confidence", 0.0) or 0.0) for r in trace]
    trade_confs = [
        float(r.get("confidence", 0.0) or 0.0) for r in trace if r.get("is_trade")
    ]
    mean_conf_all = sum(all_confs) / len(all_confs) if all_confs else 0.0
    mean_conf_trades = sum(trade_confs) / len(trade_confs) if trade_confs else 0.0

    # Opportunity capture: of all decisions, how many became trades.
    decisions = result.decisions
    trade_rate = (trade_count / decisions) if decisions else 0.0

    directions = {"BUY": 0, "SELL": 0}
    for t in trades:
        d = str(t.get("direction", ""))
        if d in directions:
            directions[d] += 1

    # Approximate entry friction: spread at decision time paid on entry
    # (volume * contract_size * spread). From the decision trace spread where
    # the trade's order id is recorded; missing joins contribute nothing.
    contract = float(cfg.execution.contract_size)
    spread_by_order: dict[str, float] = {}
    for r in trace:
        oid = r.get("order_id")
        if oid:
            spread_by_order[str(oid)] = float(r.get("spread", 0.0) or 0.0)
    approx_cost = 0.0
    for o in result.orders:
        s = spread_by_order.get(str(o.get("order_id", "")))
        if s is not None:
            approx_cost += float(o.get("volume", 0.0)) * contract * s

    return {
        "evaluation_mode": EVALUATION_MODE,
        "trade_count": trade_count,
        "wins": wins,
        "losses": losses,
        "win_rate": round(win_rate, 6),
        "net_pnl_usd": round(float(result.total_pnl_usd), 6),
        "final_equity_usd": round(float(result.final_equity_usd), 6),
        "max_drawdown_usd": round(max_dd, 6),
        "turnover_orders": len(result.orders),
        "decisions": decisions,
        "trade_rate": round(trade_rate, 6),
        "mean_confidence_all": round(mean_conf_all, 6),
        "mean_confidence_trades": round(mean_conf_trades, 6),
        "buy_trades": directions["BUY"],
        "sell_trades": directions["SELL"],
        "approx_entry_spread_cost_usd": round(approx_cost, 6),
        "data_errors": len(result.data_errors),
        "warmup_skipped_decisions": result.warmup_skipped_decisions,
    }


# ---------------------------------------------------------------------------
# The benchmark runner
# ---------------------------------------------------------------------------


class ReplayCandidateBenchmark:
    """Evaluates champion/challenger/control candidates over ONE stream."""

    def __init__(
        self,
        *,
        symbol: str = "XAUUSD",
        timeframe: str = "M1",
        decide_on: str = "bar_close",
        starting_equity_usd: float = 10_000.0,
        execution: ReplayExecutionConfig | None = None,
        regime_enabled: bool = False,
        news_frame: Any = None,
        git_commit: str = "",
    ) -> None:
        if decide_on not in ("bar_close", "every_tick"):
            raise ValueError(f"decide_on must be bar_close|every_tick, got {decide_on!r}")
        self.symbol = symbol
        self.timeframe = timeframe
        self.decide_on = decide_on
        self.starting_equity_usd = float(starting_equity_usd)
        self.execution = execution or ReplayExecutionConfig()
        self.regime_enabled = bool(regime_enabled)
        self.news_frame = news_frame
        self.git_commit = git_commit

    # ------------------------------------------------------------------

    def _session_config(self, candidate: ReplayCandidate) -> ReplaySessionConfig:
        return ReplaySessionConfig(
            experiment_type="REPLAY",
            symbol=self.symbol,
            timeframe=self.timeframe,
            model_artifact_path=str(candidate.artifact_path),
            policy_params=dict(candidate.policy_params),
            execution=self.execution,
            decide_on=self.decide_on,
            news_frame=self.news_frame,
            git_commit=self.git_commit,
            starting_equity_usd=self.starting_equity_usd,
            regime_enabled=self.regime_enabled,
        )

    def _make_source(self, events: list[dict[str, Any]], name: str) -> HistoricalEventSource:
        """One frozen source over the shared record list.

        The stream is the SAME ordered record list for every candidate;
        event semantics (bar vs tick) come from the records themselves
        (``kind`` == "TICK" -> tick replay, else bar replay) so all
        candidates consume byte-identical events.
        """
        is_tick = any(str(r.get("kind", "BAR")).upper() == "TICK" for r in events)
        if is_tick:
            return TickEventSource(events, symbol=self.symbol, name=name)
        return BarEventSource(events, symbol=self.symbol, name=name)

    # ------------------------------------------------------------------

    def run(
        self,
        events: list[dict[str, Any]],
        candidates: list[ReplayCandidate],
        *,
        run_id: str | None = None,
    ) -> dict[str, Any]:
        """Runs every candidate over the shared stream; returns the artifact.

        Raises on: empty events/candidates, duplicate names, unknown roles,
        or event-hash disagreement between candidate runs (same-stream
        violation — never report cross-stream comparisons).
        """
        if not events:
            raise ValueError("ReplayCandidateBenchmark: no events supplied")
        if not candidates:
            raise ValueError("ReplayCandidateBenchmark: no candidates supplied")
        names = [c.name for c in candidates]
        if len(set(names)) != len(names):
            raise ValueError(f"ReplayCandidateBenchmark: duplicate candidate names {names}")
        for c in candidates:
            if c.role not in VALID_ROLES:
                raise ValueError(
                    f"candidate {c.name!r}: role {c.role!r} not in {VALID_ROLES}"
                )

        rid = run_id or f"RBM-{datetime.now(UTC):%Y%m%d%H%M%S}"
        ordered = sorted(events, key=lambda r: r["timestamp"])

        candidate_blocks: dict[str, Any] = {}
        reference_event_hash: str | None = None
        for cand in candidates:
            cfg = self._session_config(cand)
            # FRESH engine per candidate: frozen binding of THIS candidate's
            # model/policy; zero state carries between candidates.
            engine = StreamingReplayEngine(cfg)
            source = self._make_source(ordered, f"replay_benchmark[{cand.name}]")
            result = engine.run(source, run_id=f"{rid}-{cand.name}")
            if reference_event_hash is None:
                reference_event_hash = result.event_hash
            elif result.event_hash != reference_event_hash:
                raise RuntimeError(
                    "ReplayCandidateBenchmark: SAME-STREAM violation — candidate "
                    f"{cand.name!r} consumed a different event stream "
                    f"({result.event_hash} != {reference_event_hash}); refusing to "
                    "publish a cross-stream comparison"
                )
            block: dict[str, Any] = {
                "candidate": cand.identity(),
                "role": cand.role,
                "metrics": _metrics_from_run(result, cfg),
                "determinism": {
                    "event_hash": result.event_hash,
                    "ledger_hash": result.ledger_hash,
                    "ledger_content_hash": _ledger_content_hash(result.trades),
                    "config_fingerprint": result.config_fingerprint,
                    "model_fingerprint": result.model_identity.get("model_fingerprint", ""),
                    "scaler_fingerprint": result.model_identity.get("scaler_fingerprint", ""),
                    "strategy_fingerprint": result.strategy_fingerprint,
                },
                "first_event": result.first_event,
                "last_event": result.last_event,
            }
            candidate_blocks[cand.name] = block
            logger.info(
                "[REPLAY_BENCHMARK] candidate=%s role=%s trades=%s net_pnl=%.2f ledger=%s",
                cand.name,
                cand.role,
                block["metrics"]["trade_count"],
                block["metrics"]["net_pnl_usd"],
                result.ledger_hash,
            )

        assert reference_event_hash is not None
        benchmark_id = self._benchmark_id(candidate_blocks, reference_event_hash)
        artifact = {
            "benchmark_id": benchmark_id,
            "run_id": rid,
            "evaluation_mode": EVALUATION_MODE,
            "stream": {
                "event_count": len(ordered),
                "event_hash": reference_event_hash,
                "first_event": ordered[0]["timestamp"].isoformat()
                if hasattr(ordered[0]["timestamp"], "isoformat")
                else str(ordered[0]["timestamp"]),
                "last_event": ordered[-1]["timestamp"].isoformat()
                if hasattr(ordered[-1]["timestamp"], "isoformat")
                else str(ordered[-1]["timestamp"]),
            },
            "config": {
                "symbol": self.symbol,
                "timeframe": self.timeframe,
                "decide_on": self.decide_on,
                "starting_equity_usd": self.starting_equity_usd,
                "execution": self.execution.identity(),
                "regime_enabled": self.regime_enabled,
                "git_commit": self.git_commit,
            },
            "candidates": candidate_blocks,
            "separation_note": (
                "COUNTERFACTUAL_REPLAY trades are simulated decisions on the "
                "historical stream under frozen execution assumptions. They are "
                "NOT executed historical trades (experience ledger) and are "
                "never written to the ledger or used as training data."
            ),
        }
        logger.info("[REPLAY_BENCHMARK] artifact=%s candidates=%d", benchmark_id, len(candidates))
        return artifact

    @staticmethod
    def _benchmark_id(candidate_blocks: dict[str, Any], event_hash: str) -> str:
        """Deterministic identity: same stream + same candidate outcomes."""
        payload = {
            "event_hash": event_hash,
            "candidates": [
                {
                    "identity": block["candidate"],
                    "ledger_content_hash": block["determinism"]["ledger_content_hash"],
                    "metrics": block["metrics"],
                }
                for _, block in sorted(candidate_blocks.items())
            ],
        }
        raw = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
        return "rbm_" + hashlib.sha256(raw).hexdigest()[:16]


__all__ = [
    "EVALUATION_MODE",
    "VALID_ROLES",
    "ReplayCandidate",
    "ReplayCandidateBenchmark",
]
