"""PERF-02: AccountInfo refresh throttling on the live run loop.

Guards the 5s account-info cache: get_account_info() must NOT be called on
every tick. Behavior preserved:
  * The first call (cache empty) fetches from the adapter.
  * A call within 5s of the last refresh reuses the cached AccountInfo.
  * After 5s the adapter is queried again.
  * An adapter failure falls back to the last known AccountInfo (never raises).
"""

from __future__ import annotations


class _CountingAdapter:
    """Adapter double counting get_account_info calls."""

    def __init__(self) -> None:
        self.calls = 0
        self.fail = False
        self._info = {"login": 1, "balance": 10000.0}

    def get_account_info(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("adapter down")
        return dict(self._info)


class _FakeEngine:
    """run_loop-shaped refresh block (mirrors live_engine lines 1321-1345)."""

    def __init__(self, adapter) -> None:
        self.adapter = adapter
        self._last_account_info = None
        self._last_account_refresh = 0.0
        self._skipped_sleep = 0
        self._ticks = 0

    def _refresh_block(self, now: float) -> bool:
        """Returns False when the loop would `continue` (no account/tick)."""
        _now = now
        if getattr(self, "_last_account_refresh", 0.0) + 5.0 < _now:
            try:
                live_account = self.adapter.get_account_info()
            except Exception:
                live_account = getattr(self, "_last_account_info", None)
            self._last_account_info = live_account
            self._last_account_refresh = _now
        else:
            live_account = getattr(self, "_last_account_info", None)
        # tick: fake a tick that always exists
        tick = object()
        if live_account is None or tick is None:
            self._skipped_sleep += 1
            return False
        self._ticks += 1
        return True


def test_perf02_first_call_fetches() -> None:
    ad = _CountingAdapter()
    eng = _FakeEngine(ad)
    ok = eng._refresh_block(100.0)
    assert ok is True
    assert ad.calls == 1
    assert eng._ticks == 1


def test_perf02_within_5s_reuses_cache() -> None:
    ad = _CountingAdapter()
    eng = _FakeEngine(ad)
    eng._refresh_block(100.0)  # fetch
    ok = eng._refresh_block(100.5)  # 0.5s later
    assert ok is True
    assert ad.calls == 1  # no second adapter call
    assert eng._ticks == 2


def test_perf02_after_5s_refetches() -> None:
    ad = _CountingAdapter()
    eng = _FakeEngine(ad)
    eng._refresh_block(100.0)
    ok = eng._refresh_block(105.1)  # > 5s later
    assert ok is True
    assert ad.calls == 2
    assert eng._ticks == 2


def test_perf02_adapter_failure_falls_back() -> None:
    ad = _CountingAdapter()
    eng = _FakeEngine(ad)
    eng._refresh_block(100.0)  # success -> cache populated
    ad.fail = True
    ok = eng._refresh_block(106.0)  # refresh attempt fails
    assert ok is True  # still has last known account
    assert eng._ticks == 2
    assert eng._last_account_info is not None


def test_perf02_no_cached_account_and_failure_skips() -> None:
    ad = _CountingAdapter()
    ad.fail = True
    eng = _FakeEngine(ad)
    ok = eng._refresh_block(100.0)  # first call fails, no cache
    assert ok is False  # loop would `continue` (sleep + skip tick)
    assert eng._skipped_sleep == 1
    assert eng._ticks == 0
