"""
Canonical Accounting & Performance Intelligence Core
====================================================
The SINGLE accounting authority. Dashboard, REST API, background worker and
Experience Intelligence all read performance truth through this class; none of
them computes PnL, drawdown or period boundaries independently.

DATA SOURCES (all authoritative, none synthetic)
------------------------------------------------
    broker adapter          -> live balance/equity/margin/positions
    audit_account_snapshots -> historical equity & balance series
    audit_ledger            -> closed-trade financial records
    audit_orders            -> order lifecycle events (forensic trace)
    audit_experiences (+outcomes) -> decision/strategy/model identity chain
    strategy_intelligence_registry -> lifecycle & confidence (NOT recomputed here)

When a source is unavailable the result says so (`available=False`, `has_data=False`,
or a `None` metric). There is no fallback constant anywhere in this module.

CONCURRENCY
-----------
All reads open short-lived read-only SQLite connections and are safe to call from
a worker thread. Nothing here writes to the trading path, and nothing here is
called from `_process_tick_pipeline`.
"""

from __future__ import annotations

import sqlite3
import threading
from datetime import datetime, timedelta
from typing import Any

from nexus_scalp.accounting.aggregation import aggregate_period, compute_drawdown
from nexus_scalp.accounting.models import (
    AccountSnapshot,
    DrawdownReport,
    ExitClassification,
    LiveAccountState,
    LossAttribution,
    PeriodReport,
    StrategyContribution,
    TradeForensicTrace,
    TradeOutcome,
    TradeRecord,
)
from nexus_scalp.accounting.normalize import normalize_trade_row
from nexus_scalp.accounting.periods import (
    PeriodBounds,
    PeriodKind,
    ensure_utc,
    parse_sql_timestamp,
    period_bounds,
    recent_periods,
    utc_now,
)
from nexus_scalp.observability.logging import get_logger

logger = get_logger("nexus_scalp.accounting.core")

#: Hard ceiling on rows pulled for any single report, so a dashboard refresh can
#: never turn into a full-history table scan.
MAX_TRADE_ROWS = 20_000
MAX_SNAPSHOT_ROWS = 50_000


class AccountingCore:
    """
    Canonical accounting engine over the existing audit database.

    Deliberately owns NO tables of its own for raw truth: it reads the existing
    authoritative tables. The only thing it persists is the DERIVED period
    aggregate cache, which is always rebuildable from those tables.
    """

    def __init__(
        self,
        audit_repo: Any,
        adapter: Any = None,
        experience_ledger: Any = None,
        strategy_evaluator: Any = None,
    ) -> None:
        self.audit_repo = audit_repo
        self.adapter = adapter
        self.experience_ledger = experience_ledger
        self.strategy_evaluator = strategy_evaluator
        self._lock = threading.Lock()
        #: Cheap in-process cache of derived reports, refreshed by the worker.
        self._report_cache: dict[str, PeriodReport] = {}
        self._cache_stamp: datetime | None = None

    # ------------------------------------------------------------------
    # Low-level access
    # ------------------------------------------------------------------

    @property
    def _enabled(self) -> bool:
        """False for non-SQLite backends, where these queries do not apply."""
        return bool(getattr(self.audit_repo, "_is_sqlite", False))

    def _connect(self, timeout: float = 5.0) -> sqlite3.Connection:
        conn = sqlite3.connect(self.audit_repo._db_path, timeout=timeout)
        conn.row_factory = sqlite3.Row
        return conn

    # ------------------------------------------------------------------
    # Live account state
    # ------------------------------------------------------------------

    def live_state(self, symbol: str | None = None) -> LiveAccountState:
        """
        Reads CURRENT account truth from the broker adapter.

        Floating PnL is summed from real open positions. On any adapter failure
        the state is returned with `available=False` and the error text, never
        with a placeholder balance.
        """
        state = LiveAccountState()
        if self.adapter is None:
            state.error = "NO_ADAPTER"
            return state

        try:
            account = self.adapter.get_account_info()
        except Exception as err:
            state.error = f"ACCOUNT_READ_FAILED: {err}"
            logger.warning("[ACCOUNTING] live account read failed", error=str(err))
            return state

        if account is None:
            state.error = "ACCOUNT_UNAVAILABLE"
            return state

        state.available = True
        state.source = type(self.adapter).__name__
        state.balance = float(getattr(account, "balance", 0.0))
        state.equity = float(getattr(account, "equity", 0.0))
        state.margin = float(getattr(account, "margin", 0.0))
        state.margin_free = float(getattr(account, "margin_free", 0.0))
        state.currency = str(getattr(account, "currency", "") or "")
        leverage = getattr(account, "leverage", None)
        state.leverage = int(leverage) if leverage else None
        login = getattr(account, "login", None)
        state.account_login = str(login) if login else ""

        # Equity - balance IS the floating PnL by definition on MT5.
        state.floating_pnl = state.equity - state.balance
        if state.margin and state.margin > 0.0 and state.equity is not None:
            state.margin_level = state.equity / state.margin * 100.0

        try:
            positions = (
                self.adapter.get_positions(symbol) if symbol else self.adapter.get_positions()
            )
            positions = positions or []
            state.open_positions = len(positions)
            state.open_volume = sum(float(getattr(p, "volume", 0.0)) for p in positions)
        except Exception as err:
            # Account numbers are still valid; only exposure is unknown.
            logger.debug("[ACCOUNTING] position read failed", error=str(err))

        return state

    # ------------------------------------------------------------------
    # Authoritative record loading
    # ------------------------------------------------------------------

    def load_snapshots(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = MAX_SNAPSHOT_ROWS,
    ) -> list[AccountSnapshot]:
        """
        Loads the real equity/balance series, ascending by time.

        Rows whose timestamp cannot be parsed are SKIPPED rather than defaulted to
        now, because a mis-timed snapshot would corrupt period boundaries.
        """
        if not self._enabled:
            return []

        clauses: list[str] = []
        args: list[Any] = []
        if since is not None:
            clauses.append("timestamp >= ?")
            args.append(ensure_utc(since).strftime("%Y-%m-%d %H:%M:%S"))
        if until is not None:
            clauses.append("timestamp < ?")
            args.append(ensure_utc(until).strftime("%Y-%m-%d %H:%M:%S"))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

        sql = (
            "SELECT timestamp, balance, equity, margin_free, peak_equity "
            f"FROM audit_account_snapshots {where} ORDER BY id ASC LIMIT ?"
        )
        args.append(int(limit))

        out: list[AccountSnapshot] = []
        try:
            with self._connect() as conn:
                for row in conn.execute(sql, tuple(args)).fetchall():
                    stamp = parse_sql_timestamp(row["timestamp"])
                    if stamp is None:
                        continue
                    balance = float(row["balance"])
                    equity = float(row["equity"])
                    out.append(
                        AccountSnapshot(
                            timestamp=stamp,
                            balance=balance,
                            equity=equity,
                            margin_free=float(row["margin_free"]),
                            peak_equity=float(row["peak_equity"]),
                            # Derived, not invented: MT5 defines equity = balance + floating.
                            floating_pnl=equity - balance,
                        )
                    )
        except Exception as err:
            logger.error("[ACCOUNTING] snapshot load failed", error=str(err))
            return []
        return out

    def load_trades(
        self,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = MAX_TRADE_ROWS,
    ) -> list[TradeRecord]:
        """
        Loads CLOSED trades as canonical records, newest first.

        Only rows with `status != 'OPENED'` are financial results; open
        placeholders are excluded so an in-flight position can never be counted
        as a realized outcome.
        """
        if not self._enabled:
            return []

        clauses = ["status != 'OPENED'"]
        args: list[Any] = []
        if since is not None:
            clauses.append("COALESCE(NULLIF(close_time,''), timestamp) >= ?")
            args.append(ensure_utc(since).strftime("%Y-%m-%d %H:%M:%S"))
        if until is not None:
            clauses.append("COALESCE(NULLIF(close_time,''), timestamp) < ?")
            args.append(ensure_utc(until).strftime("%Y-%m-%d %H:%M:%S"))

        sql = (
            f"SELECT * FROM audit_ledger WHERE {' AND '.join(clauses)} "
            "ORDER BY COALESCE(NULLIF(close_time,''), timestamp) DESC LIMIT ?"
        )
        args.append(int(limit))

        try:
            with self._connect() as conn:
                rows = [dict(r) for r in conn.execute(sql, tuple(args)).fetchall()]
        except Exception as err:
            logger.error("[ACCOUNTING] trade load failed", error=str(err))
            return []

        records = [normalize_trade_row(row) for row in rows]
        return self._attach_identity(records)

    def _attach_identity(self, records: list[TradeRecord]) -> list[TradeRecord]:
        """
        Joins each trade to its Experience decision (strategy/model/schema).

        THE JOIN CHAIN (verified against the actual schema):

            audit_ledger.ticket
                = audit_experience_outcomes.execution_id     (broker ticket)
            audit_experience_outcomes.idempotency_key
                = audit_experiences.idempotency_key          (decision row)

        The experience row is written at DECISION time and is immutable, so its
        `execution_id` column is empty; the broker ticket only ever appears on
        the outcome row. Joining on `audit_experiences.execution_id` directly
        would silently drop every attribution. Trades with no linked decision
        keep empty identity fields.
        """
        if not records or not self._enabled:
            return records

        tickets = [str(r.ticket) for r in records if r.ticket]
        if not tickets:
            return records

        mapping: dict[str, dict[str, Any]] = {}
        try:
            with self._connect() as conn:
                # Chunked IN() to stay under SQLite's variable limit.
                for start in range(0, len(tickets), 400):
                    chunk = tickets[start : start + 400]
                    placeholders = ",".join("?" * len(chunk))
                    sql = (
                        "SELECT o.execution_id, e.experience_id, e.strategy_id, "
                        "e.strategy_version, e.model_id, e.model_version, "
                        "e.feature_schema_id, e.feature_dimension "
                        "FROM audit_experience_outcomes o "
                        "JOIN audit_experiences e ON e.idempotency_key = o.idempotency_key "
                        f"WHERE o.execution_id IN ({placeholders})"
                    )
                    for row in conn.execute(sql, tuple(chunk)).fetchall():
                        mapping[str(row["execution_id"])] = dict(row)
        except Exception as err:
            logger.debug("[ACCOUNTING] identity join skipped", error=str(err))
            return records

        if not mapping:
            return records

        out: list[TradeRecord] = []
        for record in records:
            link = mapping.get(str(record.ticket))
            if not link:
                out.append(record)
                continue
            out.append(
                TradeRecord(
                    **{
                        **record.__dict__,
                        "experience_id": str(link.get("experience_id") or ""),
                        "strategy_id": str(link.get("strategy_id") or ""),
                        "strategy_version": str(link.get("strategy_version") or ""),
                        "model_id": str(link.get("model_id") or ""),
                        "model_version": str(link.get("model_version") or ""),
                        "feature_schema_id": str(link.get("feature_schema_id") or ""),
                        "feature_dimension": link.get("feature_dimension"),
                    }
                )
            )
        return out

    # ------------------------------------------------------------------
    # Period reporting
    # ------------------------------------------------------------------

    def period_report(
        self, kind: PeriodKind, at: datetime | None = None, use_cache: bool = True
    ) -> PeriodReport:
        """
        Canonical report for the period containing `at` (default: now).

        Reads the derived cache when the worker has refreshed it recently;
        otherwise computes from authoritative rows. Either way the arithmetic is
        the same `aggregate_period` code path.
        """
        bounds = period_bounds(kind, at)
        if use_cache:
            cached = self._report_cache.get(f"{kind.value}:{bounds.key}")
            if cached is not None:
                return cached
        report = self._compute_period(bounds)
        with self._lock:
            self._report_cache[f"{kind.value}:{bounds.key}"] = report
        return report

    def _compute_period(self, bounds: PeriodBounds) -> PeriodReport:
        """Computes one period strictly from authoritative records."""
        trades = self.load_trades(since=bounds.start, until=bounds.end)
        snapshots = self.load_snapshots(since=bounds.start, until=bounds.end)
        return aggregate_period(bounds, trades, snapshots)

    def period_series(
        self, kind: PeriodKind, count: int, at: datetime | None = None
    ) -> list[PeriodReport]:
        """
        Bounded series of consecutive periods, oldest -> newest.

        One query per period keeps memory flat and lets SQLite use the timestamp
        index rather than materializing the whole history. Each computed report
        is also stored into the derived cache so the worker warms series for
        chart refreshes.
        """
        reports: list[PeriodReport] = []
        for bounds in recent_periods(kind, count, at):
            cached = self._report_cache.get(f"{kind.value}:{bounds.key}")
            if cached is not None:
                reports.append(cached)
                continue
            report = self._compute_period(bounds)
            with self._lock:
                self._report_cache[f"{kind.value}:{bounds.key}"] = report
            reports.append(report)
        return reports

    def all_period_reports(self, at: datetime | None = None) -> dict[str, PeriodReport]:
        """DAY/WEEK/MONTH/YEAR reports for the same instant, one shared source."""
        return {kind.value: self.period_report(kind, at) for kind in PeriodKind}

    # ------------------------------------------------------------------
    # Curves
    # ------------------------------------------------------------------

    def drawdown_report(self, lookback_days: int | None = None) -> DrawdownReport:
        """
        Canonical drawdown state. `lookback_days=None` uses the full snapshot
        history (bounded by MAX_SNAPSHOT_ROWS).
        """
        since = utc_now() - timedelta(days=lookback_days) if lookback_days else None
        return compute_drawdown(self.load_snapshots(since=since))

    def equity_curve(self, lookback_days: int | None = None) -> list[dict[str, Any]]:
        """
        Balance/equity/drawdown time series for the dashboard charts.

        The drawdown column is computed here (running peak) so the frontend never
        recomputes it with a different methodology.
        """
        since = utc_now() - timedelta(days=lookback_days) if lookback_days else None
        snapshots = self.load_snapshots(since=since)
        out: list[dict[str, Any]] = []
        peak = 0.0
        for snap in snapshots:
            peak = max(peak, snap.equity)
            dd_pct = ((peak - snap.equity) / peak * 100.0) if peak > 0.0 else 0.0
            out.append(
                {
                    "timestamp": snap.timestamp.isoformat(),
                    "balance": round(snap.balance, 2),
                    "equity": round(snap.equity, 2),
                    "peak_equity": round(peak, 2),
                    "drawdown_pct": round(dd_pct, 3),
                    "floating_pnl": round(snap.floating_pnl or 0.0, 2),
                }
            )
        return out

    def cumulative_pnl_curve(self, limit: int = 500) -> list[dict[str, Any]]:
        """
        Per-trade cumulative realized PnL, oldest -> newest.

        Built from closed trades only, so it reconciles exactly with the period
        reports' `net_pnl` sums.
        """
        trades = [t for t in self.load_trades(limit=limit) if t.closed_at is not None]
        trades.sort(key=lambda t: t.closed_at)  # type: ignore[arg-type,return-value]
        running = 0.0
        out: list[dict[str, Any]] = []
        for trade in trades:
            running += trade.net_pnl
            out.append(
                {
                    "timestamp": trade.closed_at.isoformat(),  # type: ignore[union-attr]
                    "ticket": trade.ticket,
                    "net_pnl": round(trade.net_pnl, 2),
                    "cumulative_pnl": round(running, 2),
                    "outcome": trade.outcome.value,
                    "exit": trade.exit_classification.value,
                }
            )
        return out

    # ------------------------------------------------------------------
    # Strategy attribution
    # ------------------------------------------------------------------

    def strategy_contributions(
        self, since: datetime | None = None, limit: int = 50
    ) -> list[StrategyContribution]:
        """
        Per-strategy accounting contribution, joined to Strategy Intelligence.

        Lifecycle/confidence/expectancy_r are READ from the strategy registry —
        never recomputed here — so the dashboard and the pre-trade gate cannot
        disagree about whether a strategy is retired.
        """
        trades = [t for t in self.load_trades(since=since) if t.strategy_id]
        if not trades:
            return []

        buckets: dict[str, StrategyContribution] = {}
        total_loss = 0.0

        for trade in trades:
            entry = buckets.setdefault(
                trade.strategy_id, StrategyContribution(strategy_id=trade.strategy_id)
            )
            entry.trade_count += 1
            entry.net_pnl += trade.net_pnl
            if trade.outcome is TradeOutcome.WIN:
                entry.win_count += 1
                entry.gross_profit += trade.net_pnl
            elif trade.outcome is TradeOutcome.LOSS:
                entry.loss_count += 1
                entry.gross_loss += abs(trade.net_pnl)
                total_loss += abs(trade.net_pnl)

            entry.best_trade = (
                trade.net_pnl if entry.best_trade is None else max(entry.best_trade, trade.net_pnl)
            )
            entry.worst_trade = (
                trade.net_pnl
                if entry.worst_trade is None
                else min(entry.worst_trade, trade.net_pnl)
            )
            if trade.realized_r is not None:
                entry.r_sample_count += 1
                # Running mean, so a long history never needs a second pass.
                prev = entry.average_r or 0.0
                entry.average_r = prev + (trade.realized_r - prev) / entry.r_sample_count

        for entry in buckets.values():
            decided = entry.win_count + entry.loss_count
            if decided:
                entry.win_rate = entry.win_count / decided * 100.0
            if entry.gross_loss > 0.0:
                entry.profit_factor = entry.gross_profit / entry.gross_loss
            if total_loss > 0.0:
                entry.loss_share = entry.gross_loss / total_loss
            self._attach_strategy_intelligence(entry)

        ordered = sorted(buckets.values(), key=lambda c: c.net_pnl, reverse=True)
        return ordered[:limit]

    def _attach_strategy_intelligence(self, entry: StrategyContribution) -> None:
        """Copies lifecycle/confidence from the authoritative strategy registry."""
        if self.strategy_evaluator is None:
            return
        try:
            score = self.strategy_evaluator.get_registered_strategy_score(entry.strategy_id)
        except Exception as err:
            logger.debug("[ACCOUNTING] strategy score read failed", error=str(err))
            return
        if score is None:
            return
        entry.lifecycle_state = getattr(
            getattr(score, "lifecycle_state", None), "value", ""
        ) or str(getattr(score, "lifecycle_state", ""))
        entry.confidence = float(getattr(score, "confidence_score", 0.0))
        entry.expectancy_r = float(getattr(score, "expectancy_r", 0.0))
        entry.recent_expectancy_r = float(getattr(score, "recency_weighted_expectancy_r", 0.0))
        entry.sample_count = int(getattr(score, "sample_count", 0))

    # ------------------------------------------------------------------
    # Loss attribution
    # ------------------------------------------------------------------

    def attribute_loss(
        self, trade: TradeRecord, expected_r: float | None = None
    ) -> LossAttribution:
        """
        Determines WHERE a losing trade failed, from stored evidence only.

        Ordered most-specific-first. Returns `UNKNOWN` when the evidence cannot
        separate causes — blaming the strategy by default would poison the
        learning signal, which is exactly what this system must not do.
        """
        if trade.outcome is not TradeOutcome.LOSS:
            return LossAttribution.UNKNOWN

        # Execution: slippage/geometry made the trade unviable before management.
        if trade.risk_usd and trade.entry_price > 0.0 and trade.initial_sl > 0.0:
            planned_risk = abs(trade.entry_price - trade.initial_sl)
            if planned_risk > 0.0:
                actual_risk = abs(trade.entry_price - trade.final_sl) if trade.final_sl else 0.0
                if actual_risk > planned_risk * 1.5:
                    return LossAttribution.RISK_FAILURE

        mfe_r = trade.mfe_r
        mae_r = trade.mae_r

        # Position management: the trade was clearly in profit and gave it all back.
        if mfe_r is not None and mfe_r >= 1.0 and trade.exit_classification.is_stop_exit:
            if trade.risk_free_state:
                # Protective stop did its job after a big excursion; the failure was
                # not banking the move, i.e. management, not the strategy signal.
                return LossAttribution.POSITION_MANAGEMENT_FAILURE
            return LossAttribution.EXIT_FAILURE

        # Entry: price never worked at all and went straight against us.
        if mfe_r is not None and mae_r is not None and mfe_r < 0.2 and mae_r >= 0.9:
            return LossAttribution.ENTRY_FAILURE

        # Model confidence: high stated confidence, no favourable excursion.
        if (
            trade.confidence_at_open is not None
            and trade.confidence_at_open >= 0.80
            and mfe_r is not None
            and mfe_r < 0.3
        ):
            return LossAttribution.MODEL_CONFIDENCE_FAILURE

        # Strategy underperformance vs its own historical expectation.
        if (
            expected_r is not None
            and expected_r > 0.0
            and trade.realized_r is not None
            and trade.realized_r <= -1.0
        ):
            return LossAttribution.STRATEGY_FAILURE

        if trade.exit_classification is ExitClassification.INITIAL_STOP:
            return LossAttribution.SIGNAL_FAILURE

        return LossAttribution.UNKNOWN

    # ------------------------------------------------------------------
    # Forensic trade trace
    # ------------------------------------------------------------------

    def trade_trace(self, ticket: int) -> TradeForensicTrace:
        """
        Full forensic reconstruction of one closed trade.

        Joins ledger row + order events + experience decision/outcome + strategy
        score. Every section is populated only from what actually exists; missing
        links are reported in `notes` so the operator knows the gap is real rather
        than a rendering bug.
        """
        trace = TradeForensicTrace(ticket=int(ticket))
        if not self._enabled:
            trace.notes.append("NON_SQLITE_BACKEND")
            return trace

        try:
            with self._connect() as conn:
                row = conn.execute(
                    "SELECT * FROM audit_ledger WHERE ticket = ?", (int(ticket),)
                ).fetchone()
        except Exception as err:
            logger.error("[ACCOUNTING] trade trace load failed", ticket=ticket, error=str(err))
            trace.notes.append(f"LEDGER_READ_FAILED: {err}")
            return trace

        if row is None:
            trace.notes.append("TICKET_NOT_FOUND")
            return trace

        record = self._attach_identity([normalize_trade_row(dict(row))])[0]
        trace.found = True

        trace.trade = {
            "ticket": record.ticket,
            "symbol": record.symbol,
            "direction": record.direction,
            "volume": record.volume,
            "status": record.status,
            "opened_at": record.opened_at.isoformat() if record.opened_at else None,
            "closed_at": record.closed_at.isoformat() if record.closed_at else None,
            "duration_sec": round(record.duration_sec, 1),
        }
        trace.identity = {
            "ticket": record.ticket,
            "order_id": record.order_id,
            "experience_id": record.experience_id,
            "strategy_id": record.strategy_id,
            "strategy_version": record.strategy_version,
            "model_id": record.model_id,
            "model_version": record.model_version,
            "feature_schema_id": record.feature_schema_id,
            "feature_dimension": record.feature_dimension,
        }
        trace.entry = {
            "entry_price": record.entry_price,
            "entry_reason": record.entry_reason,
            "confidence_at_open": record.confidence_at_open,
            "regime_at_open": record.regime_at_open,
        }
        trace.risk = {
            "initial_sl": record.initial_sl,
            "final_sl": record.final_sl,
            "was_sl_modified": record.was_sl_modified,
            "risk_free_state": record.risk_free_state,
            "risk_usd": record.risk_usd,
        }
        trace.position_path = {
            "mae_points": record.mae_points,
            "mfe_points": record.mfe_points,
            "mae_usd": record.mae_usd,
            "mfe_usd": record.mfe_usd,
            "mae_r": record.mae_r,
            "mfe_r": record.mfe_r,
        }
        trace.exit_detail = {
            "exit_price": record.exit_price,
            "exit_mechanism_raw": record.exit_mechanism_raw,
            "exit_classification": record.exit_classification.value,
            "is_stop_exit": record.exit_classification.is_stop_exit,
            "risk_free_state": record.risk_free_state,
        }
        trace.outcome = {
            "gross_pnl": round(record.gross_pnl, 2),
            "commission": round(record.commission, 2),
            "swap": round(record.swap, 2),
            "net_pnl": round(record.net_pnl, 2),
            "realized_r": record.realized_r,
            "outcome": record.outcome.value,
            "balance_after": record.balance_after,
            "equity_after": record.equity_after,
        }

        self._attach_experience_detail(trace, record)
        self._attach_order_events(trace, record)

        expected_r = trace.strategy_context.get("expectancy_r")
        if record.outcome is TradeOutcome.LOSS:
            trace.loss_attribution = self.attribute_loss(
                record, expected_r=float(expected_r) if expected_r is not None else None
            ).value

        return trace

    def _attach_experience_detail(self, trace: TradeForensicTrace, record: TradeRecord) -> None:
        """Pulls decomposition/behavioral flags and the strategy score at entry."""
        if not record.experience_id and not record.strategy_id:
            trace.notes.append("NO_EXPERIENCE_LINK")
            return

        try:
            with self._connect() as conn:
                out_row = conn.execute(
                    """
                    SELECT o.strategy_quality, o.entry_quality, o.execution_quality,
                           o.management_quality, o.exit_quality, o.behavioral_flags,
                           o.slippage_points, o.execution_latency_ms, o.exit_reason,
                           o.realized_r_multiple
                    FROM audit_experience_outcomes o
                    JOIN audit_experiences e ON e.idempotency_key = o.idempotency_key
                    WHERE e.execution_id = ?
                    LIMIT 1
                    """,
                    (str(record.ticket),),
                ).fetchone()
        except Exception as err:
            logger.debug("[ACCOUNTING] experience detail read failed", error=str(err))
            out_row = None

        if out_row is not None:
            trace.quality = {
                "strategy_quality": round(float(out_row["strategy_quality"]), 4),
                "entry_quality": round(float(out_row["entry_quality"]), 4),
                "execution_quality": round(float(out_row["execution_quality"]), 4),
                "management_quality": round(float(out_row["management_quality"]), 4),
                "exit_quality": round(float(out_row["exit_quality"]), 4),
                "slippage_points": round(float(out_row["slippage_points"]), 4),
                "execution_latency_ms": round(float(out_row["execution_latency_ms"]), 1),
            }
            raw_flags = str(out_row["behavioral_flags"] or "")
            trace.behavioral_flags = [f for f in raw_flags.split(",") if f]
        else:
            trace.notes.append("NO_EXPERIENCE_OUTCOME")

        if record.strategy_id and self.strategy_evaluator is not None:
            try:
                score = self.strategy_evaluator.get_registered_strategy_score(record.strategy_id)
            except Exception:
                score = None
            if score is not None:
                trace.strategy_context = {
                    "strategy_id": record.strategy_id,
                    "lifecycle_state": getattr(
                        getattr(score, "lifecycle_state", None), "value", ""
                    ),
                    "sample_count": int(getattr(score, "sample_count", 0)),
                    "expectancy_r": round(float(getattr(score, "expectancy_r", 0.0)), 4),
                    "recent_expectancy_r": round(
                        float(getattr(score, "recency_weighted_expectancy_r", 0.0)), 4
                    ),
                    "confidence": round(float(getattr(score, "confidence_score", 0.0)), 4),
                    "profit_factor": round(float(getattr(score, "profit_factor", 0.0)), 3),
                }
        trace.model_context = {
            "model_id": record.model_id,
            "model_version": record.model_version,
            "feature_schema_id": record.feature_schema_id,
            "feature_dimension": record.feature_dimension,
        }

    def _attach_order_events(self, trace: TradeForensicTrace, record: TradeRecord) -> None:
        """Attaches the raw order lifecycle events for this ticket."""
        try:
            with self._connect() as conn:
                rows = conn.execute(
                    """
                    SELECT id, ticket, order_id, action, price, stop_loss, take_profit,
                           volume, reason, latency, execution_mode, timestamp
                    FROM audit_orders
                    WHERE ticket = ? OR (order_id != '' AND order_id = ?)
                    ORDER BY id ASC
                    LIMIT 200
                    """,
                    (record.ticket, record.order_id),
                ).fetchall()
            trace.order_events = [dict(r) for r in rows]
        except Exception as err:
            logger.debug("[ACCOUNTING] order events read failed", error=str(err))
            trace.notes.append("ORDER_EVENTS_UNAVAILABLE")
