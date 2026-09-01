"""AGENT 3 (offline-70D) — MT5 Python API JSON probe suite (live-terminal integration).

Contract (user MASTER STEER):
- DOCUMENT -> PROBE -> CAPTURE REAL OUTPUT -> NORMALIZE TO JSON -> TEST -> INTEGRATE.
- Every probe serializes the ACTUAL return value (no repr, no invented fields)
  into a JSON-safe envelope. Account-identifying fields are REDACTED.
- Read-only: initialize/terminal/account/symbol/ticks/rates/order_calc_*/positions.
  NEVER order_send. NEVER order_check with dispatch intent (probed with an
  explicitly-invalid request that cannot execute).

Run:
    .venv/Scripts/python.exe -m pytest tests/integration/test_mt5_api_probes.py -v

Terminal-offline behavior: probes that require a connection SKIP cleanly
(MT5_CONNECTED guard) so the deterministic unit lanes stay green on CI.
"""

from __future__ import annotations

import datetime
import json
import math
from typing import Any

import pytest

try:  # pragma: no cover - import guard mirrors mt5_adapter convention
    import MetaTrader5 as mt5
except Exception:  # pragma: no cover
    mt5 = None

HAS_MT5 = mt5 is not None
SYMBOL = "XAUUSD"

REDACTED_ACCOUNT_FIELDS = ("login", "name", "company", "server", "trade_expert")


def _jsonable(value: Any) -> Any:
    """Test-only normalizer: numpy scalars/arrays, namedtuples, datetimes, None."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else ("inf" if value > 0 else "-inf")
    if isinstance(value, datetime.datetime):
        return value.isoformat()
    try:
        import numpy as np

        if isinstance(value, np.integer):
            return int(value)
        if isinstance(value, np.floating):
            return float(value)
        if isinstance(value, np.ndarray):
            fields = None
            if getattr(value.dtype, "fields", None):
                fields = list(value.dtype.fields.keys())
            return {
                "_type": "ndarray",
                "shape": [int(d) for d in value.shape],
                "dtype": str(value.dtype.names or value.dtype),
                "fields": fields,
            }
    except ImportError:  # pragma: no cover
        pass
    if hasattr(value, "_asdict"):
        return {k: _jsonable(v) for k, v in value._asdict().items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return repr(value)


def _envelope(function: str, **extra: Any) -> dict[str, Any]:
    env: dict[str, Any] = {
        "function": function,
        "module": "MetaTrader5",
        "package_version": getattr(mt5, "__version__", "unknown"),
        "captured_at": datetime.datetime.now(datetime.UTC).isoformat(),
    }
    env.update(extra)
    return env


@pytest.fixture(scope="module")
def terminal() -> MT5Session:
    if not HAS_MT5:
        pytest.skip("MetaTrader5 package not installed")
    return MT5Session()


class MT5Session:
    """Bounded live session for probes; shut down once per module."""

    def __init__(self) -> None:
        self.connected = bool(mt5.initialize())
        self.conn_envelope = _envelope(
            "initialize", success=self.connected, last_error=_jsonable(mt5.last_error())
        )

    def close(self) -> None:
        if self.connected:
            mt5.shutdown()


@pytest.fixture(scope="module")
def mt5_connected(terminal: MT5Session):
    yield terminal.connected
    terminal.close()


@pytest.fixture(scope="module")
def probe_dir(tmp_path_factory):
    return tmp_path_factory.mktemp("mt5_probe")


def _write_probe(probe_dir, name: str, payload: dict[str, Any]) -> str:
    """Sanitized probe artifact (compact; no secrets by construction)."""
    path = probe_dir / f"{name}.json"
    path.write_text(json.dumps(payload, indent=1, default=str), encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# 1. CONNECTION PROBES
# ---------------------------------------------------------------------------


def test_probe_initialize_envelope(terminal, probe_dir):
    env = terminal.conn_envelope
    assert env["success"] in (True, False)
    le = env["last_error"]
    assert isinstance(le, list) and len(le) == 2
    path = _write_probe(probe_dir, "initialize", env)
    print("initialize envelope:", path)


def test_probe_version_and_last_error(terminal, mt5_connected, probe_dir):
    if not mt5_connected:
        pytest.skip("MT5 terminal not connected")
    v = mt5.version()
    env = _envelope(
        "version",
        success=True,
        return_type=type(v).__name__,
        build=v[0],
        release_date=str(v[2]),
        last_error=_jsonable(mt5.last_error()),
    )
    assert isinstance(v, tuple) and len(v) == 3
    _write_probe(probe_dir, "version", env)


def test_probe_terminal_info(terminal, mt5_connected, probe_dir):
    if not mt5_connected:
        pytest.skip("MT5 terminal not connected")
    ti = mt5.terminal_info()
    d = _jsonable(ti._asdict() if hasattr(ti, "_asdict") else {})
    env = _envelope(
        "terminal_info",
        success=ti is not None,
        fields=sorted(d.keys()),
        connected=d.get("connected"),
        maxbars=d.get("maxbars"),
        trade_allowed=d.get("trade_allowed"),
        last_error=_jsonable(mt5.last_error()),
    )
    assert ti is not None
    assert env["connected"] is True
    _write_probe(probe_dir, "terminal_info", env)


def test_probe_account_info_redacted(terminal, mt5_connected, probe_dir):
    if not mt5_connected:
        pytest.skip("MT5 terminal not connected")
    ai = mt5.account_info()
    d = ai._asdict() if ai else {}
    redacted = {
        k: ("<redacted>" if k in REDACTED_ACCOUNT_FIELDS else _jsonable(v)) for k, v in d.items()
    }
    env = _envelope(
        "account_info",
        success=ai is not None,
        fields=sorted(d.keys()),
        currency=d.get("currency"),
        leverage=d.get("leverage"),
        redacted_fields=list(REDACTED_ACCOUNT_FIELDS),
        payload=redacted,
    )
    assert env["payload"]["login"] == "<redacted>"
    _write_probe(probe_dir, "account_info_redacted", env)


# ---------------------------------------------------------------------------
# 2. SYMBOL PROBES
# ---------------------------------------------------------------------------


def test_probe_symbol_info(terminal, mt5_connected, probe_dir):
    if not mt5_connected:
        pytest.skip("MT5 terminal not connected")
    assert mt5.symbol_select(SYMBOL, True) is True
    si = mt5.symbol_info(SYMBOL)
    d = si._asdict() if si else {}
    want = [
        "name",
        "visible",
        "trade_mode",
        "digits",
        "point",
        "trade_tick_size",
        "trade_tick_value",
        "volume_min",
        "volume_max",
        "volume_step",
        "trade_contract_size",
        "currency_profit",
        "currency_margin",
        "spread",
        "trade_stops_level",
    ]
    env = _envelope(
        "symbol_info",
        success=si is not None,
        symbol=SYMBOL,
        fields_present=sorted(d.keys()),
        normalized={k: _jsonable(d.get(k)) for k in want if k in d},
        missing_from_installed=[k for k in want if k not in d],
    )
    assert env["normalized"]["point"] == 0.01
    assert env["normalized"]["trade_contract_size"] == 100.0
    _write_probe(probe_dir, "symbol_info_xauusd", env)


def test_probe_symbol_info_tick(terminal, mt5_connected, probe_dir):
    if not mt5_connected:
        pytest.skip("MT5 terminal not connected")
    st = mt5.symbol_info_tick(SYMBOL)
    d = st._asdict() if st else {}
    env = _envelope(
        "symbol_info_tick",
        success=st is not None,
        symbol=SYMBOL,
        fields=sorted(d.keys()),
        sample={
            k: _jsonable(v) for k, v in d.items() if k in ("time", "bid", "ask", "last", "flags")
        },
        note="CURRENT terminal tick - never valid as a historical replay price",
    )
    assert env["sample"]["ask"] > 0 and env["sample"]["bid"] > 0
    _write_probe(probe_dir, "symbol_info_tick", env)


# ---------------------------------------------------------------------------
# 3. BAR API PROBES
# ---------------------------------------------------------------------------


def test_probe_copy_rates_from_pos(terminal, mt5_connected, probe_dir):
    if not mt5_connected:
        pytest.skip("MT5 terminal not connected")
    raw = mt5.copy_rates_from_pos(SYMBOL, mt5.TIMEFRAME_M1, 0, 5)
    env = _envelope(
        "copy_rates_from_pos",
        success=raw is not None,
        return_type=type(raw).__name__,
        rows=0 if raw is None else len(raw),
        fields=list(raw.dtype.fields.keys()) if raw is not None else [],
        dtype=str(raw.dtype) if raw is not None else None,
        last_error=_jsonable(mt5.last_error()),
    )
    assert raw is not None and env["rows"] == 5
    assert env["fields"] == [
        "time",
        "open",
        "high",
        "low",
        "close",
        "tick_volume",
        "spread",
        "real_volume",
    ]
    _write_probe(probe_dir, "copy_rates_from_pos", env)


def test_probe_copy_rates_range_cases(terminal, mt5_connected, probe_dir):
    if not mt5_connected:
        pytest.skip("MT5 terminal not connected")
    now = datetime.datetime.now(datetime.UTC)
    start = now - datetime.timedelta(minutes=10)
    normal = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M1, start, now)
    empty = mt5.copy_rates_range(SYMBOL, mt5.TIMEFRAME_M1, now, now)
    invalid = mt5.copy_rates_range("NO_SUCH_SYMBOL_XY", mt5.TIMEFRAME_M1, start, now)
    err_after_invalid = mt5.last_error()
    future = mt5.copy_rates_range(
        SYMBOL,
        mt5.TIMEFRAME_M1,
        now + datetime.timedelta(days=30),
        now + datetime.timedelta(days=31),
    )
    env = _envelope(
        "copy_rates_range",
        normal_rows=0 if normal is None else len(normal),
        start_eq_end_rows=0 if empty is None else len(empty),
        invalid_symbol_returns=None if invalid is None else len(invalid),
        # PROBED BEHAVIOR: last_error is stateful and may still report the
        # previous SUCCESS right after a None return; the None return itself
        # is the reliable failure signal (verified on package 5.0.6090).
        invalid_symbol_last_error=_jsonable(err_after_invalid),
        future_range_rows=0 if future is None else len(future),
        last_error=_jsonable(mt5.last_error()),
    )
    assert normal is not None and env["normal_rows"] > 0
    assert env["start_eq_end_rows"] == 0
    assert invalid is None, "invalid symbol must return None"
    _write_probe(probe_dir, "copy_rates_range_cases", env)


# ---------------------------------------------------------------------------
# 4. TICK API PROBES
# ---------------------------------------------------------------------------


def test_probe_copy_ticks_range(terminal, mt5_connected, probe_dir):
    if not mt5_connected:
        pytest.skip("MT5 terminal not connected")
    now = datetime.datetime.now(datetime.UTC)
    start = now - datetime.timedelta(minutes=10)
    tk = mt5.copy_ticks_range(SYMBOL, start, now, mt5.COPY_TICKS_ALL)
    fields = list(tk.dtype.fields.keys()) if tk is not None else []
    env = _envelope(
        "copy_ticks_range",
        success=tk is not None,
        copy_flags="COPY_TICKS_ALL",
        rows=0 if tk is None else len(tk),
        fields=fields,
        first_tick={k: _jsonable(tk[0][k]) for k in ("time", "bid", "ask", "time_msc")}
        if tk is not None and len(tk)
        else None,
        last_error=_jsonable(mt5.last_error()),
    )
    assert tk is not None and env["rows"] > 0
    assert {"time", "bid", "ask", "time_msc", "flags", "volume"}.issubset(set(fields))
    _write_probe(probe_dir, "copy_ticks_range", env)


# ---------------------------------------------------------------------------
# 5. PROFIT / MARGIN CALIBRATION PROBES (reference only, not per-tick)
# ---------------------------------------------------------------------------


def test_probe_order_calc_profit_calibration(terminal, mt5_connected, probe_dir):
    if not mt5_connected:
        pytest.skip("MT5 terminal not connected")
    si = mt5.symbol_info(SYMBOL)._asdict()
    contract = float(si["trade_contract_size"])
    open_px, close_px = 4379.33, 4380.33  # +1.00 USD move
    buy = mt5.order_calc_profit(mt5.ORDER_TYPE_BUY, SYMBOL, 0.1, open_px, close_px)
    sell = mt5.order_calc_profit(mt5.ORDER_TYPE_SELL, SYMBOL, 0.1, open_px, close_px)
    env = _envelope(
        "order_calc_profit",
        action="BUY/SELL calibration",
        symbol=SYMBOL,
        volume=0.1,
        price_open=open_px,
        price_close=close_px,
        buy_result=_jsonable(buy),
        sell_result=_jsonable(sell),
        contract_size=contract,
        expected_buy=round(0.1 * contract * (close_px - open_px), 6),
        last_error=_jsonable(mt5.last_error()),
    )
    assert buy == pytest.approx(env["expected_buy"], rel=1e-6)
    assert sell == pytest.approx(-env["expected_buy"], rel=1e-6)
    _write_probe(probe_dir, "order_calc_profit", env)


def test_probe_order_calc_margin(terminal, mt5_connected, probe_dir):
    if not mt5_connected:
        pytest.skip("MT5 terminal not connected")
    m = mt5.order_calc_margin(mt5.ORDER_TYPE_BUY, SYMBOL, 0.1, 4379.33)
    env = _envelope(
        "order_calc_margin",
        action="BUY",
        symbol=SYMBOL,
        volume=0.1,
        price=4379.33,
        result=_jsonable(m),
        last_error=_jsonable(mt5.last_error()),
    )
    assert m is not None and float(m) > 0
    _write_probe(probe_dir, "order_calc_margin", env)


# ---------------------------------------------------------------------------
# 6. POSITION / HISTORY PROBES (observation only)
# ---------------------------------------------------------------------------


def test_probe_positions_get(terminal, mt5_connected, probe_dir):
    if not mt5_connected:
        pytest.skip("MT5 terminal not connected")
    pos = mt5.positions_get(symbol=SYMBOL)
    rows = [] if pos is None or len(pos) == 0 else [p._asdict() for p in pos]
    env = _envelope(
        "positions_get",
        success=pos is not None,
        rows=len(rows),
        fields=sorted(rows[0].keys()) if rows else [],
        tickets=[r.get("ticket") for r in rows],
        last_error=_jsonable(mt5.last_error()),
    )
    _write_probe(probe_dir, "positions_get", env)


def test_probe_history_deals_get(terminal, mt5_connected, probe_dir):
    if not mt5_connected:
        pytest.skip("MT5 terminal not connected")
    now = datetime.datetime.now(datetime.UTC)
    start = now - datetime.timedelta(days=2)
    deals = mt5.history_deals_get(start, now, group=SYMBOL)
    # PROBED BEHAVIOR (package 5.0.6090): with results the call returns a
    # TUPLE of TradeDeal namedtuples (NOT a structured ndarray). Normalize
    # each element via _asdict.
    rows = []
    fields: list[str] = []
    if deals is not None and len(deals) > 0:
        first = deals[0]
        if hasattr(first, "_asdict"):
            fields = sorted(first._asdict().keys())
            rows = [d._asdict() for d in deals]
        else:  # structured ndarray fallback (older package builds)
            fields = list(first.dtype.fields.keys()) if hasattr(first, "dtype") else []
            rows = [{k: first[k] for k in fields}]
    env = _envelope(
        "history_deals_get",
        success=deals is not None,
        return_shape=type(deals).__name__,
        rows=len(rows),
        fields=fields,
        sample={k: _jsonable(rows[0][k]) for k in ("ticket", "position_id", "profit", "entry")}
        if rows
        else None,
        last_error=_jsonable(mt5.last_error()),
    )
    _write_probe(probe_dir, "history_deals_get", env)


def test_probe_order_check_is_not_execution(terminal, mt5_connected, probe_dir):
    """order_check is a VALIDATION call. Probed with an unfillable price far
    beyond stops level so it can never mutate state; asserts envelope shape
    and that retcode != trade_done semantics stay observable."""
    if not mt5_connected:
        pytest.skip("MT5 terminal not connected")
    request = {
        "action": mt5.TRADE_ACTION_DEAL,
        "symbol": SYMBOL,
        "volume": 0.01,
        "type": mt5.ORDER_TYPE_BUY,
        "price": 1.0,  # absurd price -> check-only, never executable
        "sl": 0.0,
        "tp": 0.0,
        "magic": 0,
        "comment": "NSE_PROBE_CHECK_ONLY",
        "type_time": mt5.ORDER_TIME_GTC,
        "type_filling": mt5.ORDER_FILLING_IOC,
    }
    result = mt5.order_check(request)
    d = result._asdict() if result is not None else {}
    env = _envelope(
        "order_check",
        success=result is not None,
        fields=sorted(d.keys()),
        retcode=d.get("retcode"),
        comment=d.get("comment"),
        note="VALIDATION ONLY - order_check success != fill; order_send is FORBIDDEN in replay",
        last_error=_jsonable(mt5.last_error()),
    )
    assert result is not None and "retcode" in env["fields"]
    _write_probe(probe_dir, "order_check", env)
