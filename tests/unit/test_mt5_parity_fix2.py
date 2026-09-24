"""MT5-PARITY-FORENSICS — FIX-2 (datafetch) regression tests.

D-set from `reports/02_laneB_datafetch.md`:
  * D1 = F17-1: ingest `mt5` source called ``get_rate_history(start=...)``
    which does not exist -> TypeError before any fetch (source unusable).
  * D2 = F17-3: broker-history sync advanced its watermark on a fetch that
    returned ``[]`` because of transport/broker failure.
  * D3 = F12-4: unknown timeframe silently fetched M1 data while keeping the
    requested timeframe label on the normalized record.

Every test below FAILS on the pre-fix code (RED evidence in
``status/fix-2.md``).
"""

from __future__ import annotations

import inspect
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

# REAL adapter signature must be resolvable before the ingest module import
# below (it imports the module that D1 fixes, from this source tree).
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

# The REAL adapter signature — the contract D1's call must satisfy.
from pydantic import ValidationError

import scripts.data.ingest_historical_candles as ingest_mod
from nexus_scalp.adapters.database.broker_history_sync import BrokerHistorySyncWorker
from nexus_scalp.adapters.mt5.mt5_adapter import DirectMT5Adapter
from nexus_scalp.market_data.bar_aggregator import BarData

# =============================================================================
# D1 — F17-1: ingest `mt5` source must call the real get_rate_history signature
# =============================================================================


class _RateRecord:
    """Minimal record shaped like providers.RateBarSnapshot (has __dict__)."""

    def __init__(self, ts: datetime, i: int) -> None:
        self.time = int(ts.timestamp())
        self.time_utc = ts
        self.open = 3300.0 + i * 0.01
        self.high = 3301.0 + i * 0.01
        self.low = 3299.0 + i * 0.01
        self.close = 3300.5 + i * 0.01
        self.tick_volume = 100 + i
        self.spread = 20
        self.real_volume = 0


class _RealSignatureAdapter:
    """Adapter exposing ONLY the real ``get_rate_history`` signature.

    Deliberately has no ``start`` parameter and no ``copy_rates_from_pos``
    fallback: pre-fix code raises ``TypeError: unexpected keyword argument
    'start'`` here, exactly as it does against ``DirectMT5Adapter``.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.connect_calls = 0
        self.disconnect_calls = 0
        self._connected = False

    def is_connected(self) -> bool:  # method, like every shipped adapter
        return self._connected

    def connect(self) -> bool:
        self.connect_calls += 1
        self._connected = True
        return True

    def disconnect(self) -> None:
        self.disconnect_calls += 1
        self._connected = False

    def get_rate_history(self, symbol: str, timeframe: str = "M1", count: int = 500, from_utc=None):
        self.calls.append(
            {"symbol": symbol, "timeframe": timeframe, "count": count, "from_utc": from_utc}
        )
        if not self._connected:
            raise RuntimeError("MT5 not connected")
        base = datetime(2026, 9, 1, 0, 0, tzinfo=UTC)
        n = min(int(count), 1200)
        return [_RateRecord(base + timedelta(minutes=i), i) for i in range(n)]


def test_d1_rate_history_call_binds_to_real_adapter_signature() -> None:
    """The kwargs the ingest passes MUST bind to DirectMT5Adapter's signature.

    This is the report's stated TEST for F17-1 (unit-run the ``mt5`` source
    against the adapter signature). Pre-fix the kwargs contain ``start`` and
    ``inspect.Signature.bind`` raises TypeError.
    """
    adapter = _RealSignatureAdapter()
    ingest_mod.read_bars_mt5(
        symbol="XAUUSD", timeframe="M1", count=1500, days=2, adapter=adapter, own_connection=True
    )

    assert adapter.calls, "get_rate_history was never called"
    real_sig = inspect.signature(DirectMT5Adapter.get_rate_history)
    # bind() raises TypeError when the call does not match the real signature
    # (dropping `self`); pre-fix code passes start= and fails here.
    real_sig.bind(object(), **adapter.calls[0])


def test_d1_days_window_is_passed_as_from_utc() -> None:
    """``--days`` must reach the adapter as the real ``from_utc`` window."""
    adapter = _RealSignatureAdapter()
    before = datetime.now(UTC)
    ingest_mod.read_bars_mt5(
        symbol="XAUUSD", timeframe="M1", count=1500, days=3, adapter=adapter, own_connection=True
    )
    after = datetime.now(UTC)

    call = adapter.calls[0]
    assert call["from_utc"] is not None, "from_utc (the --days window) was not passed"
    assert isinstance(call["from_utc"], datetime)
    # window start = now - 3 days (bracket the clock for the assertion)
    earliest = before - timedelta(days=3) - timedelta(seconds=5)
    latest = after - timedelta(days=3) + timedelta(seconds=5)
    assert earliest <= call["from_utc"] <= latest, call["from_utc"]
    # --days widens the bar count request (2880 min * 1.1 for 2 days)
    assert call["count"] >= 1500


def test_d1_disconnects_after_fetch_when_it_owns_the_connection() -> None:
    adapter = _RealSignatureAdapter()
    ingest_mod.read_bars_mt5(
        symbol="XAUUSD", timeframe="M1", count=1200, days=1, adapter=adapter, own_connection=True
    )
    assert adapter.disconnect_calls == 1


def test_d1_connects_when_adapter_reports_disconnected() -> None:
    """``is_connected`` is a METHOD: a fresh adapter must be connected.

    Pre-fix ``manage_conn`` was computed from the truthy *bound method*, so it
    was always False, the adapter was never connected, every fetch returned
    ``[]`` and the source died with "MT5 returned 0 bars".
    """
    adapter = _RealSignatureAdapter()
    assert adapter.is_connected() is False  # fresh, not yet connected

    frame = ingest_mod.read_bars_mt5(
        symbol="XAUUSD", timeframe="M1", count=1200, days=1, adapter=adapter
    )  # own_connection=None -> the probe decides

    assert adapter.connect_calls == 1, "fresh adapter was never connected"
    assert frame.height > 0


def test_d1_connect_failure_is_loud_not_empty() -> None:
    """A refused connection must raise IngestError, never yield 0 rows quietly."""

    class _Refused(_RealSignatureAdapter):
        def connect(self) -> bool:
            self.connect_calls += 1
            return False

    with pytest.raises(ingest_mod.IngestError, match="connect failed"):
        ingest_mod.read_bars_mt5(
            symbol="XAUUSD", timeframe="M1", count=1200, days=1, adapter=_Refused()
        )


def test_d1_real_adapter_signature_has_no_start_parameter() -> None:
    """Pin the fact the bug rested on (report PROBE, no live terminal)."""
    params = inspect.signature(DirectMT5Adapter.get_rate_history).parameters
    assert "start" not in params
    assert "from_utc" in params
    assert "count" in params


# =============================================================================
# D3 — F12-4: unknown timeframe silently fell back to M1 while KEEPING the
# requested label in the normalized record.
# =============================================================================


def test_d3_unknown_timeframe_label_is_refused_in_the_record() -> None:
    """The normalized bar cannot carry a label nothing can serve (F12-4).

    The report's failure mode: a typo'd/unsupported TF produced M1 bars
    labelled with the requested string, so the record lied about its own
    granularity and the mismatch was undetectable downstream. Failing here
    makes ``get_historical_bars(sym, "M7")`` raise instead of returning M1
    rows under an "M7" label (report's TEST line for F12-4).
    """
    base = dict(
        symbol="XAUUSD",
        timestamp=datetime(2026, 9, 23, 12, 0, tzinfo=UTC),
        open=4000.0,
        high=4001.0,
        low=3999.0,
        close=4000.5,
        tick_volume=10,
        is_complete=True,
    )
    # A bar genuinely served at M1 is labelled M1: admitted.
    assert BarData(timeframe="M1", **base).timeframe == "M1"
    # The broker-native extended set is admitted too.
    assert BarData(timeframe="H4", **base).timeframe == "H4"
    assert BarData(timeframe="D1", **base).timeframe == "D1"
    # A request the broker cannot serve must not leave a labelled record.
    with pytest.raises(ValidationError):
        BarData(timeframe="M7", **base)
    with pytest.raises(ValidationError):
        BarData(timeframe="M99", **base)


def test_d3_fallback_does_not_smuggle_the_requested_label() -> None:
    """If the adapter degrades to M1, the record must say M1, not 'M7'."""
    base = dict(
        symbol="XAUUSD",
        timestamp=datetime(2026, 9, 23, 12, 0, tzinfo=UTC),
        open=4000.0,
        high=4001.0,
        low=3999.0,
        close=4000.5,
        tick_volume=10,
        is_complete=True,
    )
    forged = dict(base)
    forged["timeframe"] = "M7"
    with pytest.raises(ValidationError):
        BarData(**forged)
    # The same data at the ACTUAL granularity is admitted — the fix refuses
    # the label, not the data.
    assert BarData(timeframe="M1", **base).timeframe == "M1"
    # And the label the adapter would have stamped from the request string
    # (str(timeframe).upper()) is equally refused: the degrading tf_map at
    # mt5_adapter.py:867 cannot leak the requested label past this guard.
    forged_upper = dict(base)
    forged_upper["timeframe"] = "M7"
    with pytest.raises(ValidationError):
        BarData(**forged_upper)
    assert DirectMT5Adapter.get_rate_history.__doc__ is not None


# =============================================================================
# D2 — F17-3: watermark must not advance on a fetch empty because of failure
# =============================================================================


class _RecordingAudit:
    """Audit double that records whether the watermark was persisted."""

    def __init__(self, meta: dict) -> None:
        self._meta = meta
        self.captured: dict = {}
        self.sync_calls = 0

    def get_broker_history_meta(self, symbol: str) -> dict:
        return self._meta

    def sync_broker_history(self, orders, deals, symbol, sync_from, sync_to):
        self.sync_calls += 1
        self.captured["from"] = sync_from.isoformat()
        self.captured["to"] = sync_to.isoformat()
        return {
            "orders_total": len(orders or []),
            "orders_inserted": 0,
            "orders_duplicates": 0,
            "deals_total": len(deals or []),
            "deals_inserted": 0,
            "deals_duplicates": 0,
            "trades_total": 0,
            "trades_inserted": 0,
            "trades_duplicates": 0,
            "duration_ms": 0.0,
        }


class _BrokerDownAdapter:
    """Transport/broker failure: history readers answer [] (F17-4), and the
    connectivity probe reports the terminal is unreachable."""

    def is_connected(self) -> bool:
        return False

    def get_history_orders(self, from_dt, to_dt, symbol=None):
        return []

    def get_history_deals(self, from_dt, to_dt, symbol=None):
        return []


class _QuietAdapter(_BrokerDownAdapter):
    """A genuinely empty window: [] but the broker IS reachable."""

    def is_connected(self) -> bool:
        return True


class _RowsAdapter(_BrokerDownAdapter):
    """Normal sync: history readers return rows and the broker is reachable."""

    def __init__(self, rows: int = 3) -> None:
        self.rows = rows

    def is_connected(self) -> bool:
        return True

    def get_history_orders(self, from_dt, to_dt, symbol=None):
        return [{"ticket": 1000 + i, "time_setup": 0, "state": 0} for i in range(self.rows)]

    def get_history_deals(self, from_dt, to_dt, symbol=None):
        return []


class _NoProbeAdapter:
    """Rows, but the adapter publishes NO connectivity signal (like the
    BUG-133 test doubles) — window arithmetic must be unaffected."""

    def __init__(self, rows: int = 2) -> None:
        self.rows = rows

    def get_history_orders(self, from_dt, to_dt, symbol=None):
        return [{"ticket": 2000 + i, "time_setup": 0, "state": 0} for i in range(self.rows)]

    def get_history_deals(self, from_dt, to_dt, symbol=None):
        return []


def _worker(audit) -> BrokerHistorySyncWorker:
    w = BrokerHistorySyncWorker(
        audit=audit,
        adapter=_BrokerDownAdapter(),
        symbol="XAUUSD",
        interval_sec=0.0,
        overlap_days=1,
    )
    w.start()
    w._last_run_ts = 0.0
    return w


def test_d2_down_broker_does_not_persist_sync_window() -> None:
    """A zero-row fetch from an unreachable broker must not reach persistence.

    Pre-fix the worker called sync_broker_history with [] every cycle, which
    unconditionally advanced last_sync_to (broker_history.py:772-781) — past
    an outage longer than OVERLAP_DAYS that becomes a permanent hole.
    """
    audit = _RecordingAudit(
        {"last_sync_from": "2026-05-08T17:03:44+00:00", "last_sync_to": "2026-08-20T17:45:00+00:00"}
    )
    worker = _worker(audit)
    assert worker.tick() is False
    assert audit.sync_calls == 0, "watermark was persisted for a failed fetch"


def test_d2_down_broker_reports_failure_not_success() -> None:
    """The cycle must surface as SYNC_FAILED so the next tick retries in full."""
    worker = _worker(_RecordingAudit({}))
    assert worker.tick() is False
    assert worker.last_error
    assert "unavailable" in worker.last_error


def test_d2_quiet_window_still_persists() -> None:
    """[] from a REACHABLE broker is genuinely empty: it must persist.

    This pins the half of F17-3 the fix must NOT break — an account with no
    activity in the window still advances its watermark.
    """
    audit = _RecordingAudit(
        {"last_sync_from": "2026-05-08T17:03:44+00:00", "last_sync_to": "2026-08-20T17:45:00+00:00"}
    )
    worker = BrokerHistorySyncWorker(
        audit=audit,
        adapter=_QuietAdapter(),
        symbol="XAUUSD",
        interval_sec=0.0,
        overlap_days=1,
    )
    worker.start()
    worker._last_run_ts = 0.0
    assert worker.tick() is True
    assert audit.sync_calls == 1
    expected_to = datetime.now(UTC).isoformat()
    assert audit.captured["to"].startswith(expected_to[:13]), audit.captured["to"]


def test_d2_rows_persist_unchanged() -> None:
    """Non-empty fetch behaviour (and window anchoring) is untouched."""
    audit = _RecordingAudit(
        {"last_sync_from": "2026-05-08T17:03:44+00:00", "last_sync_to": "2026-08-20T17:45:00+00:00"}
    )
    worker = BrokerHistorySyncWorker(
        audit=audit,
        adapter=_RowsAdapter(3),
        symbol="XAUUSD",
        interval_sec=0.0,
        overlap_days=1,
    )
    worker.start()
    worker._last_run_ts = 0.0
    assert worker.tick() is True
    assert audit.sync_calls == 1
    expected_from = (
        datetime.fromisoformat("2026-08-20T17:45:00+00:00") - timedelta(days=1)
    ).isoformat()
    assert audit.captured["from"] == expected_from, audit.captured["from"]


def test_d2_window_arithmetic_preserved_without_connectivity_signal() -> None:
    """BUG-133 regression guard: window anchoring is independent of the probe.

    Adapters that publish no connectivity signal still compute the SAME window
    (anchored on last_sync_to - overlap); only the empty-window verdict is
    fail-closed. Pre-fix fakes with no probe exercised exactly this.
    """
    audit = _RecordingAudit(
        {"last_sync_from": "2026-05-08T17:03:44+00:00", "last_sync_to": "2026-08-20T17:45:00+00:00"}
    )

    worker = BrokerHistorySyncWorker(
        audit=audit,
        adapter=_NoProbeAdapter(2),
        symbol="XAUUSD",
        interval_sec=0.0,
        overlap_days=1,
    )
    worker.start()
    worker._last_run_ts = 0.0
    assert worker.tick() is True
    expected_from = (
        datetime.fromisoformat("2026-08-20T17:45:00+00:00") - timedelta(days=1)
    ).isoformat()
    assert audit.captured["from"] == expected_from, audit.captured["from"]
