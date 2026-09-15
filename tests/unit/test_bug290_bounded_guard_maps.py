"""BUG-290/291 (perf-wave R7+R8) — the engine's in-process guard maps were
unbounded dicts that never evicted (long-lived client growth) (2026-09-15,
NSE-Swarm role 1 Architect).

Pre-fix evidence (HEAD 2e53fd04):
  * order_manager.py:510  `self._processed_orders: dict[str, bool] = {}` —
    written at dispatch.py:144/512/586, NEVER pruned anywhere in src/.
  * order_manager.py:513  `_ai_flip_warn_times` — the docstring claimed the
    stamp is "cleaned up with the ticket"; grep proved no pop/clear site
    ever existed (dead cleanup claim, same class as the BUG-285 write-only
    overflow).
  * accounting/core.py:90 `_report_cache: dict[str, PeriodReport] = {}` —
    every distinct `{kind}:{period-key}` a caller touched stayed resident
    for the process life (historical browsing grows it forever).

Fix: one shared seam — nexus_scalp.bounded_map.BoundedLRUMap (stdlib-only,
O(1), MutableMapping surface so dict-shaped tests/stubs keep working), wired
into all three sites with test-pinned caps, + evictions metric surfaced on
the debug snapshot.

Pins:
  BM-1..5   map semantics (cap, LRU order, contains-does-not-touch, get
            moves MRU, MutableMapping surface + rejects bad maxsize)
  OM-1/2    the two order-manager guards ARE bounded instances with the
            canonical caps (RED-BEFORE at 2e53fd04: no bounded_map module)
  AC-1      the accounting cache IS a bounded instance with REPORT_CACHE_MAX
  AC-2      functional: period_report round-trip still serves from the LRU
            cache (behavior-preserving swap, money truth unchanged)
  SRC-1/2   class guard: the three fixed sites never regress to a plain-dict
            annotation + bare-{} init on those attribute names
  SNAP-1    debug snapshot exposes processed_orders_evictions (None-safe on
            plain-dict stubs)
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from nexus_scalp.bounded_map import BoundedLRUMap


# ---------------------------------------------------------------------------
# BM: the shared seam itself
# ---------------------------------------------------------------------------
class TestBoundedLRUMap:
    def test_bm1_caps_size_and_counts_evictions(self) -> None:
        m: BoundedLRUMap[str, bool] = BoundedLRUMap("t", maxsize=3)
        for i in range(10):
            m[f"k{i}"] = True
        assert len(m) == 3
        assert m.evictions == 7
        # newest 3 survive, oldest evicted first
        assert "k0" not in m and "k9" in m

    def test_bm2_evicts_least_recently_used_not_oldest_insertion(self) -> None:
        m: BoundedLRUMap[str, int] = BoundedLRUMap("t", maxsize=2)
        m["a"] = 1
        m["b"] = 2
        assert m["a"] == 1  # read touches a -> b becomes LRU
        m["c"] = 3
        assert "a" in m and "c" in m and "b" not in m

    def test_bm3_contains_does_not_touch_order(self) -> None:
        # INV-001 hot-path rule: the dispatch guard looks up with `in` on
        # every entry attempt; a lookup must NEVER reorder (no write cost).
        m: BoundedLRUMap[str, int] = BoundedLRUMap("t", maxsize=2)
        m["a"] = 1
        m["b"] = 2
        assert "a" in m  # contains must NOT rescue 'a' from eviction
        m["c"] = 3
        assert "a" not in m

    def test_bm4_mutable_mapping_surface(self) -> None:
        m: BoundedLRUMap[str, int] = BoundedLRUMap("t", maxsize=4)
        m["a"] = 1
        assert m.get("a") == 1 and m.get("zz") is None
        assert m.pop("a") == 1 and "a" not in m
        m["x"] = 9
        assert list(m) == ["x"]
        del m["x"]
        assert len(m) == 0 and not m
        m.clear()

    def test_bm5_rejects_bad_maxsize(self) -> None:
        with pytest.raises(ValueError):
            BoundedLRUMap("t", maxsize=0)


# ---------------------------------------------------------------------------
# OM: order-manager guard maps are bounded instances
# ---------------------------------------------------------------------------
def _manager() -> object:
    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.execution.order_manager import OrderLifecycleManager

    om = OrderLifecycleManager(
        adapter=MagicMock(),
        audit_repo=AuditRepository(db_url="sqlite:///:memory:"),
        experience_engine=None,
    )
    om.notifier = None
    return om


class TestOrderManagerGuards:
    def test_om1_processed_orders_is_bounded_with_canonical_cap(self) -> None:
        from nexus_scalp.execution.order_manager import PROCESSED_ORDERS_GUARD_MAX

        om = _manager()
        guard = om._processed_orders  # type: ignore[attr-defined]
        assert isinstance(guard, BoundedLRUMap)
        assert guard.maxsize == PROCESSED_ORDERS_GUARD_MAX == 50_000
        assert guard.evictions == 0

    def test_om2_growth_beyond_cap_is_clamped(self) -> None:
        # The pre-fix shape accumulated 1 insert/dispatch forever (measured
        # driver: a 24/7 client dispatches thousands of request_ids/day).
        om = _manager()
        guard = om._processed_orders  # type: ignore[attr-defined]
        shrink = BoundedLRUMap("probe", maxsize=10)
        for i in range(25):
            guard[f"req-{i}"] = True
            shrink[f"req-{i}"] = True
        # canonical cap can't be overflowed cheaply; prove the SAME instance
        # type enforces its bound under the small-cap probe:
        assert len(shrink) == 10 and shrink.evictions == 15
        # and the real guard accepts dict-shaped writes + `in` reads:
        assert "req-24" in guard and guard["req-0"] is True
        assert len(guard) == 25

    def test_om3_ai_flip_stamps_bounded_and_throttle_intact(self) -> None:
        from nexus_scalp.execution.order_manager import AI_FLIP_WARN_STAMPS_MAX

        om = _manager()
        stamps = om._ai_flip_warn_times  # type: ignore[attr-defined]
        assert isinstance(stamps, BoundedLRUMap)
        assert stamps.maxsize == AI_FLIP_WARN_STAMPS_MAX == 10_000
        # Throttle logic reads via .get(ticket, 0.0): a miss must return the
        # caller default, never None/KeyError (dict-compat surface).
        assert stamps.get(4242, 0.0) == 0.0
        stamps[4242] = 1.5
        assert stamps.get(4242, 0.0) == 1.5


# ---------------------------------------------------------------------------
# AC: accounting derived-report cache
# ---------------------------------------------------------------------------
class TestAccountingCache:
    def _core(self):
        from nexus_scalp.accounting.core import AccountingCore
        from nexus_scalp.adapters.database.audit_repository import AuditRepository

        audit = AuditRepository(db_url="sqlite:///:memory:")
        return AccountingCore(audit_repo=audit)

    def test_ac1_report_cache_is_bounded_with_canonical_cap(self) -> None:
        from nexus_scalp.accounting.core import REPORT_CACHE_MAX

        core = self._core()
        assert isinstance(core._report_cache, BoundedLRUMap)
        assert core._report_cache.maxsize == REPORT_CACHE_MAX == 512

    def test_ac2_period_report_serves_from_lru_unchanged(self) -> None:
        # Behavior-preserving swap: the caching path (period_series stores,
        # period_report reads) must keep working through the LRU map.
        from datetime import UTC, datetime

        from nexus_scalp.accounting.models import PeriodReport
        from nexus_scalp.accounting.periods import PeriodBounds, PeriodKind

        core = self._core()
        start = datetime(2026, 8, 15, tzinfo=UTC)
        report = PeriodReport(
            kind=PeriodKind.DAY,
            key="2026-08-15",
            label="Aug 15",
            period_start=start,
            period_end=datetime(2026, 8, 16, tzinfo=UTC),
            total_trades=2,
            win_count=1,
            loss_count=1,
            gross_profit=100.0,
            gross_loss=-40.0,
            net_pnl=60.0,
            has_data=True,
        )
        core._report_cache["DAY:2026-08-15"] = report
        got = core.period_report(PeriodKind.DAY, at=start, use_cache=True)
        assert got is report  # served from cache, identity preserved

        # Empty DB cold path still computes (no cache entry, no crash).
        cold = core.period_report(
            PeriodKind.MONTH, at=datetime(2026, 8, 20, tzinfo=UTC), use_cache=True
        )
        assert cold.has_data is False
        assert isinstance(PeriodBounds, type)  # import smoke for bounds shape


# ---------------------------------------------------------------------------
# SRC: class guard — the three sites may never regress to bare dicts
# ---------------------------------------------------------------------------
_REPO = Path(__file__).resolve().parents[2]

_BARE_INIT_RE = re.compile(
    r"self\._(processed_orders|ai_flip_warn_times|report_cache)\s*:\s*dict\[[^\]]*\]\s*=\s*\{\}"
)


def _src(rel: str) -> str:
    return (_REPO / rel).read_text(encoding="utf-8")


class TestSourcePins:
    def test_src1_no_bare_dict_init_for_guard_sites(self) -> None:
        for rel in (
            "src/nexus_scalp/execution/order_manager.py",
            "src/nexus_scalp/accounting/core.py",
        ):
            assert not _BARE_INIT_RE.search(_src(rel)), (
                f"BUG-290/291 REGRESSION: {rel} re-initializes a guard map as a bare dict"
            )

    def test_src2_sites_construct_bounded_maps(self) -> None:
        om_src = _src("src/nexus_scalp/execution/order_manager.py")
        ac_src = _src("src/nexus_scalp/accounting/core.py")
        assert "BoundedLRUMap(" in om_src and "BoundedLRUMap(" in ac_src
        # both modules must import the seam (not define local copies)
        for src in (om_src, ac_src):
            assert "from nexus_scalp.bounded_map import BoundedLRUMap" in src

    def test_bm6_bounded_map_stays_stdlib_only(self) -> None:
        # The seam sits below every consumer (hot execution path + accounting
        # + web): any app import here would create cycles/tick-path weight.
        tree = ast.parse(_src("src/nexus_scalp/bounded_map.py"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert node.module is not None and node.module.split(".")[0] in {
                    "collections",
                    "__future__",
                    "typing",
                }, f"bounded_map must stay stdlib-only, imports {node.module}"


# ---------------------------------------------------------------------------
# SNAP: debug-surface for the eviction counter
# ---------------------------------------------------------------------------
class TestSnapshotSurface:
    def test_snap1_evictions_exposed_and_none_safe_on_plain_dicts(self) -> None:
        from nexus_scalp.web.debug_snapshot import _execution_section

        guard = BoundedLRUMap("processed_orders", maxsize=2)
        guard["a"] = True
        guard["b"] = True
        guard["c"] = True  # evicts a
        om = MagicMock(spec=["global_state", "_consecutive_failures", "_processed_orders"])
        om.global_state = "NORMAL"
        om._consecutive_failures = 0
        om._processed_orders = guard
        engine = MagicMock(spec=["order_manager", "adapter"])
        engine.order_manager = om
        out = _execution_section(engine)
        assert out["processed_orders_count"] == 2
        assert out["processed_orders_evictions"] == 1

        # Foreign stubs/tests hand over a plain dict: getattr fallback -> None
        plain = MagicMock(spec=["global_state", "_consecutive_failures", "_processed_orders"])
        plain.global_state = "NORMAL"
        plain._consecutive_failures = 0
        plain._processed_orders = {"o1": True}
        engine2 = MagicMock(spec=["order_manager", "adapter"])
        engine2.order_manager = plain
        out2 = _execution_section(engine2)
        assert out2["processed_orders_count"] == 1
        assert out2["processed_orders_evictions"] is None
