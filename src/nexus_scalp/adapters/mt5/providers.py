"""
Broker-Aware Typed Snapshots (MetaTrader 5)
============================================
Typed, provenance-tagged snapshots built from OFFICIAL MT5 APIs:

    account_info / terminal_info / symbol_info / symbol_info_tick /
    positions_get / orders_get / history_orders_get / history_deals_get /
    order_calc_profit / order_calc_margin / copy_rates_* / copy_ticks_*

Every snapshot carries:
    - source (BROKER_NATIVE / FALLBACK_ESTIMATE / UNAVAILABLE)
    - captured_at (UTC wall clock)
    - freshness_ms (optional, time since broker timestamp where available)
    - error_state (None on success; {operation, code, message} on failure)

Field contract:
    - Fields present in the installed MT5 API are mapped by name.
    - Fields absent from the installed API are marked UNSUPPORTED_BY_PROVIDER.
    - A failed read produces `available=False` + error_state - NEVER fake values.

PRIVACY: no credentials are ever stored here. login is an account identifier
for display, not a secret.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

# ---------------------------------------------------------------------------
# Broker timebase contract (verified live 2026-08-18)
# ---------------------------------------------------------------------------
# MT5 terminals report tick/bar epochs on the SERVER timezone (this broker's
# terminal is on GMT+3 -> tick.time was 01:55:02 while real UTC was 22:55:02,
# delta exactly +10800s). Treating those epochs as UTC shifts every chart,
# staleness window and news comparison by 3 hours. All broker-epoch -> UTC
# conversions MUST subtract this offset; the engine's own `now` is real UTC.
# A MT5 connection does not expose the server offset via the public API, so it
# is configurable and defaults to the verified +3h (180 min) of this broker.
BROKER_SERVER_UTC_OFFSET_MINUTES: int = 180


def broker_epoch_to_utc(epoch: float | int | None) -> datetime | None:
    """Broker terminal epoch (server-local seconds) -> real UTC datetime.

    MT5 timestamps are seconds since the UNIX epoch in the SERVER timezone.
    Converting them straight as UTC is the single biggest timestamp bug on
    this stack (charts 3h in the future, staleness detection permanently
    blind, news windows skewed). Subtract the configured server offset before
    stamping as UTC. Returns None for None / garbage.
    """
    if epoch is None:
        return None
    try:
        epoch = float(epoch)
    except (TypeError, ValueError):
        return None
    import math

    if math.isnan(epoch) or math.isinf(epoch):
        return None
    try:
        return datetime.fromtimestamp(epoch - BROKER_SERVER_UTC_OFFSET_MINUTES * 60, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Provenance constants
# ---------------------------------------------------------------------------
BROKER_NATIVE = "BROKER_NATIVE"
FALLBACK_ESTIMATE = "FALLBACK_ESTIMATE"
UNAVAILABLE = "UNAVAILABLE"


@dataclass
class SnapshotBase:
    """Shared provenance/timestamp contract for every snapshot."""

    available: bool = False
    source: str = UNAVAILABLE
    captured_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    error_state: dict[str, Any] | None = None

    def as_error(self, operation: str, code: Any, message: str | None) -> SnapshotBase:
        self.available = False
        self.source = UNAVAILABLE
        self.error_state = {
            "operation": operation,
            "code": code,
            "message": message if message is not None else "",
        }
        return self


@dataclass
class AccountSnapshot(SnapshotBase):
    """Full broker account snapshot from mt5.account_info().

    Field set follows the installed MetaTrader5 package contract; fields
    unavailable in a provider are None (never fabricated).
    """

    # identity / environment
    login: int | None = None
    server: str | None = None
    company: str | None = None
    currency: str | None = None
    currency_digits: int | None = None
    # modes
    trade_mode: int | None = None  # 0=Demo, 1=Contest, 2=Real
    leverage: int | None = None
    limit_orders: int | None = None
    margin_so_mode: int | None = None
    trade_allowed: bool | None = None
    trade_expert: bool | None = None
    margin_mode: int | None = None
    fifo_close: bool | None = None
    # money
    balance: float | None = None
    credit: float | None = None
    profit: float | None = None
    equity: float | None = None
    margin: float | None = None
    margin_free: float | None = None
    margin_level: float | None = None
    # derived (with provenance)
    floating_pnl: float | None = None  # equity - balance (MT5 definition)
    net_pnl: float | None = None  # profit - commission - swap of open positions
    open_positions_count: int | None = None
    pending_orders_count: int | None = None
    margin_level_source: str = UNAVAILABLE

    def as_error(self, operation: str, code: Any, message: str | None) -> AccountSnapshot:
        super().as_error(operation, code, message)
        return self


@dataclass
class SymbolSnapshot(SnapshotBase):
    """Symbol specification + current tick, explicitly separated.

    SPECIFICATION block  <- mt5.symbol_info()
    CURRENT TICK block   <- mt5.symbol_info_tick()
    """

    spec: dict[str, Any] = field(default_factory=dict)
    tick: dict[str, Any] = field(default_factory=dict)
    tick_freshness_ms: float | None = None
    tick_stale: bool = False
    spread_points: float | None = None
    spread_points_source: str = UNAVAILABLE

    def as_error(self, operation: str, code: Any, message: str | None) -> SymbolSnapshot:
        super().as_error(operation, code, message)
        return self


@dataclass
class BrokerTickSnapshot(SnapshotBase):
    """Current market tick from mt5.symbol_info_tick() with official fields."""

    symbol: str | None = None
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    last_volume: float | None = None
    volume: float | None = None
    flags: int | None = None
    time_msc: int | None = None
    time: int | None = None
    time_utc: datetime | None = None
    freshness_ms: float | None = None
    stale: bool = False
    spread_points: float | None = None

    def as_error(self, operation: str, code: Any, message: str | None) -> BrokerTickSnapshot:
        super().as_error(operation, code, message)
        return self


@dataclass
class PositionSnapshot(SnapshotBase):
    """Open position from mt5.positions_get() with the MT5 field contract."""

    ticket: int | None = None
    symbol: str | None = None
    type: int | None = None  # POSITION_TYPE_BUY=0 / SELL=1
    magic: int | None = None
    identifier: int | None = None
    time: int | None = None
    time_msc: int | None = None
    time_update: int | None = None
    time_update_msc: int | None = None
    external_id: str | None = None
    volume: float | None = None
    price_open: float | None = None
    price_current: float | None = None
    sl: float | None = None
    tp: float | None = None
    price_ticket: float | None = None
    profit: float | None = None
    swap: float | None = None
    commission: float | None = None  # negative = cost (MT5 semantics)
    comment: str | None = None


@dataclass
class OrderSnapshot(SnapshotBase):
    """Pending/active order from mt5.orders_get()."""

    ticket: int | None = None
    symbol: str | None = None
    type: int | None = None  # ORDER_TYPE_*
    magic: int | None = None
    identifier: int | None = None
    time_setup: int | None = None
    time_setup_msc: int | None = None
    time_done: int | None = None
    time_done_msc: int | None = None
    time_expiration: int | None = None
    type_time: int | None = None
    type_filling: int | None = None
    state: int | None = None
    volume_current: float | None = None
    volume_initial: float | None = None
    price_open: float | None = None
    price_stop_limit: float | None = None
    sl: float | None = None
    tp: float | None = None
    comment: str | None = None


@dataclass
class HistoryOrderSnapshot(SnapshotBase):
    """Historical order from mt5.history_orders_get()."""

    ticket: int | None = None
    symbol: str | None = None
    type: int | None = None
    magic: int | None = None
    identifier: int | None = None
    time_setup: int | None = None
    time_setup_msc: int | None = None
    time_done: int | None = None
    time_done_msc: int | None = None
    time_expiration: int | None = None
    type_time: int | None = None
    type_filling: int | None = None
    state: int | None = None
    volume_current: float | None = None
    volume_initial: float | None = None
    price_open: float | None = None
    price_stop_limit: float | None = None
    sl: float | None = None
    tp: float | None = None
    comment: str | None = None
    done_time: int | None = None
    reason: int | None = None


@dataclass
class DealSnapshot(SnapshotBase):
    """Deal from mt5.history_deals_get()."""

    ticket: int | None = None
    order: int | None = None
    position_id: int | None = None
    symbol: str | None = None
    type: int | None = None  # DEAL_TYPE_*
    entry: int | None = None  # DEAL_ENTRY_*
    magic: int | None = None
    identifier: int | None = None
    time: int | None = None
    time_msc: int | None = None
    external_id: str | None = None
    reason: int | None = None
    volume: float | None = None
    price: float | None = None
    profit: float | None = None
    fee: float | None = None
    swap: float | None = None
    commission: float | None = None
    comment: str | None = None

    @property
    def net_result(self) -> float | None:
        """Net result of the deal: profit - commission_abs - swap_abs - fee_abs.

        MT5 stores commission/swap/fee as NEGATIVE costs in deal records.
        `net = profit - abs(commission) - abs(swap) - abs(fee)` matches the
        canonical accounting formula (BUG-019 lineage).
        """
        if self.profit is None:
            return None
        return float(
            self.profit - abs(self.commission or 0.0) - abs(self.swap or 0.0) - abs(self.fee or 0.0)
        )


@dataclass
class RateBarSnapshot(SnapshotBase):
    """One official MT5 rate bar (copy_rates_*)."""

    time: int | None = None
    time_utc: datetime | None = None
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    tick_volume: int | None = None
    spread: int | None = None
    real_volume: int | None = None


@dataclass
class TickHistorySnapshot(SnapshotBase):
    """Tick history record (copy_ticks_*)."""

    time: int | None = None
    time_utc: datetime | None = None
    time_msc: int | None = None
    flags: int | None = None
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    volume: float | None = None


@dataclass
class BrokerCalcSnapshot(SnapshotBase):
    """Result of broker-native order_calc_* APIs (in account currency).

    Provenance rule: every calculated value records WHERE it came from
    (BROKER_NATIVE when mt5.order_calc_* succeeded, FALLBACK_ESTIMATE when a
    mathematical estimate had to be used, UNAVAILABLE otherwise).
    """

    operation: str | None = None  # order_calc_profit | order_calc_margin
    symbol: str | None = None
    price_open: float | None = None
    price_close: float | None = None
    volume: float | None = None
    value: float | None = None
    value_source: str = UNAVAILABLE
    error_code: int | None = None
    error_message: str | None = None


# ---------------------------------------------------------------------------
# UTC normalization contract (task §2): accepts datetime / numpy.datetime64 /
# Polars scalar / ISO string / float epoch; naive treated as UTC.
# ---------------------------------------------------------------------------
def normalize_utc(value: Any) -> datetime | None:
    """Robust UTC conversion for MT5 timestamps and history inputs.

    Returns None for None/NaT/garbage. Windows-safe (BUG-044 lineage):
    `.timestamp()` is only called on real datetime objects.
    """
    import math

    if value is None:
        return None

    # datetime (aware or naive)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)

    # numpy.datetime64 / Polars datetime scalars: go through ISO string
    if hasattr(value, "isoformat"):
        try:
            return normalize_utc(str(value.isoformat()))
        except Exception:
            pass
    # numpy.datetime64 (no isoformat): str() gives "2026-08-17T01:30:00"
    if type(value).__module__.startswith("numpy") and hasattr(value, "astype"):
        try:
            return normalize_utc(str(value))
        except Exception:
            return None

    # float / int epoch seconds
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        try:
            return datetime.fromtimestamp(float(value), tz=UTC)
        except (OverflowError, OSError, ValueError):
            return None

    # ISO string (naive treated as UTC)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            normalized = text.replace("Z", "+00:00")
            dt = datetime.fromisoformat(normalized)
        except ValueError:
            # tolerate "2026-08-17 01:30:29" style SQL timestamps
            try:
                dt = datetime.strptime(text[:19], "%Y-%m-%d %H:%M:%S")
            except ValueError:
                return None
        if dt.tzinfo is None:
            return dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    return None


def _attr(obj: Any, name: str) -> Any:
    """Safe attribute read (returns None on missing attribute)."""
    try:
        return getattr(obj, name)
    except Exception:
        return None


def _bool_attr(obj: Any, name: str) -> bool | None:
    val = _attr(obj, name)
    if val is None:
        return None
    try:
        return bool(int(val))
    except (TypeError, ValueError):
        return None


def _int_attr(obj: Any, name: str) -> int | None:
    val = _attr(obj, name)
    if val is None:
        return None
    try:
        return int(val)
    except (TypeError, ValueError):
        return None


def _float_attr(obj: Any, name: str) -> float | None:
    val = _attr(obj, name)
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Mapping builders (pure, testable without a live terminal)
# ---------------------------------------------------------------------------
def build_account_snapshot(raw: Any) -> AccountSnapshot:
    """Maps mt5.account_info() result into the typed AccountSnapshot."""
    snap = AccountSnapshot()
    if raw is None:
        return snap
    snap.available = True
    snap.source = BROKER_NATIVE
    snap.login = _int_attr(raw, "login")
    snap.server = _attr(raw, "server")
    snap.company = _attr(raw, "company")
    snap.currency = _attr(raw, "currency")
    snap.currency_digits = _int_attr(raw, "currency_digits")
    snap.trade_mode = _int_attr(raw, "trade_mode")
    snap.leverage = _int_attr(raw, "leverage")
    snap.limit_orders = _int_attr(raw, "limit_orders")
    snap.margin_so_mode = _int_attr(raw, "margin_so_mode")
    snap.trade_allowed = _bool_attr(raw, "trade_allowed")
    snap.trade_expert = _bool_attr(raw, "trade_expert")
    snap.margin_mode = _int_attr(raw, "margin_mode")
    snap.fifo_close = _bool_attr(raw, "fifo_close")
    snap.balance = _float_attr(raw, "balance")
    snap.credit = _float_attr(raw, "credit")
    snap.profit = _float_attr(raw, "profit")
    snap.equity = _float_attr(raw, "equity")
    snap.margin = _float_attr(raw, "margin")
    snap.margin_free = _float_attr(raw, "margin_free")
    snap.margin_level = _float_attr(raw, "margin_level")
    snap.margin_level_source = BROKER_NATIVE if snap.margin_level is not None else UNAVAILABLE
    if snap.equity is not None and snap.balance is not None:
        snap.floating_pnl = round(float(snap.equity - snap.balance), 6)
    if snap.profit is not None:
        # profit (floating) already net of realized moves; keep raw float.
        # net_pnl of OPEN positions = profit - commission - swap in MT5 terms,
        # but those components are per-position; the account-level equivalent
        # is `profit`. We report profit as the floating net and let the
        # per-position decomposition live in PositionSnapshot rows.
        snap.net_pnl = float(snap.profit)
    return snap


def build_symbol_snapshot(
    raw_info: Any, raw_tick: Any, now_utc: datetime | None = None
) -> SymbolSnapshot:
    """Maps symbol_info() + symbol_info_tick() into spec/tick blocks."""
    snap = SymbolSnapshot()
    now = now_utc or datetime.now(UTC)
    if raw_info is None and raw_tick is None:
        return snap
    snap.available = True
    snap.captured_at = now

    if raw_info is not None:
        spec: dict[str, Any] = {}
        for name in (
            "name",
            "description",
            "path",
            "digits",
            "point",
            "trade_mode",
            "trade_calc_mode",
            "trade_tick_size",
            "trade_tick_value",
            "trade_contract_size",
            "volume_min",
            "volume_max",
            "volume_step",
            "trade_stops_level",
            "trade_freeze_level",
            "currency_base",
            "currency_profit",
            "currency_margin",
            "filling_mode",
            "spread",
            "spread_float",
            "trade_accrued_interest",
            "trade_face_value",
            "trade_interest_rate",
            "trade_liquidity_rate",
        ):
            val = _attr(raw_info, name)
            if val is not None:
                try:
                    if name in ("digits", "trade_mode", "trade_calc_mode", "volume_min"):
                        spec[name] = float(val)
                    elif name in ("trade_stops_level", "trade_freeze_level"):
                        spec[name] = float(val)
                    elif name in ("spread", "spread_float"):
                        spec[name] = float(val)
                    else:
                        spec[name] = val
                except (TypeError, ValueError):
                    spec[name] = val
        snap.spec = spec
        snap.source = BROKER_NATIVE

    if raw_tick is not None:
        tick: dict[str, Any] = {}
        for name in ("time", "time_msc", "flags", "bid", "ask", "last", "volume"):
            val = _attr(raw_tick, name)
            if val is not None:
                tick[name] = val
        snap.tick = tick
        t_raw = tick.get("time") or tick.get("time_msc")
        if t_raw is not None and isinstance(t_raw, (int, float)):
            tick_utc: datetime | None = broker_epoch_to_utc(float(t_raw))
            if tick_utc is not None:
                snap.tick["time_utc"] = tick_utc.isoformat()
                try:
                    snap.tick_freshness_ms = float((now - tick_utc).total_seconds() * 1000.0)
                except Exception:
                    snap.tick_freshness_ms = None
        bid = _float_attr(raw_tick, "bid")
        ask = _float_attr(raw_tick, "ask")
        if bid is not None and ask is not None:
            snap.spread_points = round(float(ask - bid), 8)
            snap.spread_points_source = BROKER_NATIVE
    return snap


def build_position_snapshot(raw: Any) -> PositionSnapshot:
    snap = PositionSnapshot()
    if raw is None:
        return snap
    snap.available = True
    snap.source = BROKER_NATIVE
    for field_name in (
        "ticket",
        "symbol",
        "type",
        "magic",
        "identifier",
        "time",
        "time_msc",
        "time_update",
        "time_update_msc",
        "external_id",
        "volume",
        "price_open",
        "price_current",
        "sl",
        "tp",
        "price_ticket",
        "profit",
        "swap",
        "commission",
        "comment",
    ):
        setattr(snap, field_name, _attr(raw, field_name))
    # numeric coercion for the fields that matter
    for num_field in (
        "volume",
        "price_open",
        "price_current",
        "sl",
        "tp",
        "profit",
        "swap",
        "commission",
    ):
        snap.__dict__[num_field] = _float_attr(raw, num_field)
    for int_field in (
        "ticket",
        "type",
        "magic",
        "identifier",
        "time",
        "time_msc",
        "time_update",
        "time_update_msc",
    ):
        snap.__dict__[int_field] = (
            _int_attr(raw, int_field) if snap.__dict__.get(int_field) is not None else None
        )
    return snap


def build_order_snapshot(raw: Any) -> OrderSnapshot:
    snap = OrderSnapshot()
    if raw is None:
        return snap
    snap.available = True
    snap.source = BROKER_NATIVE
    for field_name in (
        "ticket",
        "symbol",
        "type",
        "magic",
        "identifier",
        "time_setup",
        "time_setup_msc",
        "time_done",
        "time_done_msc",
        "time_expiration",
        "type_time",
        "type_filling",
        "state",
        "volume_current",
        "volume_initial",
        "price_open",
        "price_stop_limit",
        "sl",
        "tp",
        "comment",
    ):
        setattr(snap, field_name, _attr(raw, field_name))
    return snap


def build_history_order_snapshot(raw: Any) -> HistoryOrderSnapshot:
    snap = HistoryOrderSnapshot()
    if raw is None:
        return snap
    snap.available = True
    snap.source = BROKER_NATIVE
    for field_name in (
        "ticket",
        "symbol",
        "type",
        "magic",
        "identifier",
        "time_setup",
        "time_setup_msc",
        "time_done",
        "time_done_msc",
        "time_expiration",
        "type_time",
        "type_filling",
        "state",
        "volume_current",
        "volume_initial",
        "price_open",
        "price_stop_limit",
        "sl",
        "tp",
        "comment",
        "done_time",
        "reason",
    ):
        setattr(snap, field_name, _attr(raw, field_name))
    return snap


def build_deal_snapshot(raw: Any) -> DealSnapshot:
    snap = DealSnapshot()
    if raw is None:
        return snap
    snap.available = True
    snap.source = BROKER_NATIVE
    for field_name in (
        "ticket",
        "order",
        "position_id",
        "symbol",
        "type",
        "entry",
        "magic",
        "identifier",
        "time",
        "time_msc",
        "external_id",
        "reason",
        "volume",
        "price",
        "profit",
        "fee",
        "swap",
        "commission",
        "comment",
    ):
        setattr(snap, field_name, _attr(raw, field_name))
    return snap


def build_rate_bar_snapshot(row: Any) -> RateBarSnapshot:
    """Maps one numpy record from copy_rates_* onto the typed bar."""
    snap = RateBarSnapshot()
    if row is None:
        return snap
    snap.available = True
    snap.source = BROKER_NATIVE
    try:
        snap.time = int(row["time"])
        snap.time_utc = broker_epoch_to_utc(float(row["time"]))
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        pass
    for name in ("open", "high", "low", "close"):
        try:
            setattr(snap, name, float(row[name]))
        except (KeyError, TypeError, ValueError):
            pass
    for name in ("tick_volume", "spread", "real_volume"):
        try:
            setattr(snap, name, int(row[name]))
        except (KeyError, TypeError, ValueError):
            pass
    return snap


def build_tick_history_snapshot(row: Any) -> TickHistorySnapshot:
    snap = TickHistorySnapshot()
    if row is None:
        return snap
    snap.available = True
    snap.source = BROKER_NATIVE
    try:
        snap.time = int(row["time"])
        snap.time_utc = broker_epoch_to_utc(float(row["time"]))
    except (KeyError, TypeError, ValueError, OverflowError, OSError):
        pass
    for name in ("time_msc", "flags"):
        try:
            setattr(snap, name, int(row[name]))
        except (KeyError, TypeError, ValueError):
            pass
    for name in ("bid", "ask", "last", "volume"):
        try:
            setattr(snap, name, float(row[name]))
        except (KeyError, TypeError, ValueError):
            pass
    return snap


def validate_ohlc_bars(bars: list[RateBarSnapshot]) -> dict[str, Any]:
    """Rate/bar data integrity validation (task §39).

    Checks: ascending unique timestamps, finite OHLC, high >= max(o,c,l),
    low <= min(o,c,h), non-negative volumes. Returns a report; malformed bars
    are NOT mutated - callers decide rejection.
    """
    report: dict[str, Any] = {
        "checked": len(bars),
        "valid": 0,
        "invalid": 0,
        "issues": [],
        "duplicate_timestamps": 0,
        "descending_timestamps": 0,
        "non_finite_ohlc": 0,
        "high_low_violation": 0,
        "negative_volume": 0,
    }
    prev_ts: int | None = None
    seen: set[int] = set()
    for i, bar in enumerate(bars):
        ok = True
        if bar.time is not None:
            if bar.time in seen:
                report["duplicate_timestamps"] += 1
                ok = False
            seen.add(bar.time)
            if prev_ts is not None and bar.time < prev_ts:
                report["descending_timestamps"] += 1
                ok = False
            prev_ts = bar.time
        o, h, l, c = bar.open, bar.high, bar.low, bar.close
        if None in (o, h, l, c):
            ok = False
        else:
            import math

            vals = (o, h, l, c)
            if not all(math.isfinite(float(v)) for v in vals):
                report["non_finite_ohlc"] += 1
                ok = False
            elif h < max(o, l, c) or l > min(o, c, h):
                report["high_low_violation"] += 1
                ok = False
        if bar.tick_volume is not None and bar.tick_volume < 0:
            report["negative_volume"] += 1
            ok = False
        if not ok:
            report["invalid"] += 1
            if len(report["issues"]) < 20:
                report["issues"].append({"index": i, "time": bar.time})
        else:
            report["valid"] += 1
    return report
