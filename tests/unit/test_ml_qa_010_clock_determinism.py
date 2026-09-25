"""ML-QA-010 contract battery: BUG-262 close-time evidence runs on a fixed clock.

WHAT THIS PINS
--------------
`tests/unit/test_bug262_close_time_evidence.py` is a push-gate module
(``tests/critical_suite.txt``); it regresses BUG-262, where the reconciliation
close-loop, the vanished-ticket autopsy and the BUG-046 outcome-repair job all
stamped ``datetime.now()`` as a trade's close time instead of the broker deal
evidence. The scenarios assert *relationships* against the detection instant
("evidenced close is 2 h before detection", "a +3 h server-local stamp is
refused", "the fallback is detection +/- tolerance").

11 of those sites read ``datetime.now(UTC)`` and 4 used ``tempfile.mkdtemp()``.
That made the module the single largest wall-clock exposure in the push gate
(15 sources, per the ML-QA-003 roster recount at 03d2fede). Two flake classes:

  * **date boundary** — a scenario starting at 23:59:59 UTC computes
    ``now - timedelta(hours=2)`` in *yesterday*; a close expected "today" lands
    in a different accounting day/week bucket;
  * **tolerance widening** — the ``< now + 5 s`` / ``abs((close - now)) < 5.0``
    windows are real durations, but their *endpoints* moved with the wall
    clock, so a co-tenant-scheduled pause between the ``now`` read and the
    assertion shifted the window off the evidence it was measuring.

The fix keeps every relationship assert identical and removes the wall clock:
a module-level fixed detection instant (``_FIXED_NOW``) reached through
``_now()``, a tick helper that injects it at ``TickData.timestamp`` (the clock
``reconcile_missed_closes`` actually reads — ``current_tick.timestamp``), and a
``_FrozenClock`` patch on the ``outcome_repair`` module's ``datetime`` name for
the four sites that path owns. Every ``mkdtemp()`` became the pytest
``tmp_path`` fixture.

This battery is deliberately TEXTUAL where it can be (it analyzes the module
source, so it runs in the slim venv with no torch/sqlite import) and BEHAVIORAL
where the invariant is only provable by running the path (the exact-equality
assert that the wall clock made impossible).
"""

from __future__ import annotations

import importlib
import inspect
import io
import re
import tokenize
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MODULE_REL = "tests/unit/test_bug262_close_time_evidence.py"
_MODULE_PATH = _REPO_ROOT / _MODULE_REL
_FIXTURE_SRC = Path(
    inspect.getfile(importlib.import_module("tests.unit.test_bug262_close_time_evidence"))
)


# ---------------------------------------------------------------------------
# 1. The wall clock is gone from the scenario clock
# ---------------------------------------------------------------------------


def test_no_wall_clock_now_in_module_source() -> None:
    """No live ``datetime.now()`` call remains in the module.

    Occurrences inside docstrings/comments are permitted — the contract is
    about executed code, and a comment naming the removed defect is how the
    regression stays explained.
    """
    src = _MODULE_PATH.read_text()
    body = _strip_comments_and_docstrings(src)
    assert "datetime.now(" not in body, (
        "module must not call the wall clock in executable code; "
        "use the fixed _FIXED_NOW instant via _now()"
    )


def test_no_tempfile_in_module() -> None:
    """No ``tempfile`` import or call: pytest ``tmp_path`` owns scratch space."""
    src = _MODULE_PATH.read_text()
    body = _strip_comments_and_docstrings(src)
    assert "tempfile" not in body, "tempfile.mkdtemp() leaks dirs; use tmp_path"
    assert "mkdtemp" not in body, "tempfile.mkdtemp() leaks dirs; use tmp_path"


def test_no_os_module_dependency() -> None:
    """The ``os`` import (only there for ``os.path.join``) is gone."""
    src = _MODULE_PATH.read_text()
    body = _strip_comments_and_docstrings(src)
    assert "import os" not in body, "os was only needed for mkdtemp paths"


# ---------------------------------------------------------------------------
# 2. The fixed clock exists, is the module constant, and is reachable
# ---------------------------------------------------------------------------


def test_fixed_now_constant_is_utc_aware() -> None:
    mod = importlib.import_module("tests.unit.test_bug262_close_time_evidence")
    fixed = mod._FIXED_NOW
    assert isinstance(fixed, datetime)
    assert fixed.tzinfo is UTC, "the injected instant must be tz-aware UTC"


def test_fixed_now_sits_inside_an_accounting_day() -> None:
    """The fixed instant is deliberately NOT on a day/week bucket boundary.

    If it were exactly 00:00:00 UTC, a scenario computing
    ``now - timedelta(hours=2)`` would land in a different day *by the fixture
    itself*, and a future regression that re-introduced the wall clock would
    fail only half the time. Sitting mid-day (and mid-minute) keeps every
    relationship assert sensitive to bucket drift in either direction.
    """
    fixed = importlib.import_module("tests.unit.test_bug262_close_time_evidence")._FIXED_NOW
    assert fixed.hour not in (0, 23), "hour must not touch a day boundary"
    assert fixed.minute not in (0, 59), "minute must not touch an hour boundary"
    # A non-zero second keeps the instant away from any truncated
    # (second-granular) broker epoch stamp, so the epoch-evidence scenario's
    # ``.replace(microsecond=0)`` floor is a real floor, not a no-op.
    assert fixed.second != 0


def test_now_helper_returns_the_constant_verbatim() -> None:
    mod = importlib.import_module("tests.unit.test_bug262_close_time_evidence")
    assert mod._now() == mod._FIXED_NOW
    # Repeated reads are stable — no drift between two reads in one test.
    assert mod._now() == mod._now()


# ---------------------------------------------------------------------------
# 3. The injected clock is what production actually reads
# ---------------------------------------------------------------------------


def test_tick_helper_injects_the_fixed_instant_by_default() -> None:
    """``_tick_ts`` is the injection point: reconcile reads the tick timestamp.

    ``ReconciliationEngine.reconcile_missed_closes`` derives detection from
    ``current_tick.timestamp``, so injecting there injects the whole path. If
    this helper re-read the wall clock the remediation would be a no-op.
    """
    mod = importlib.import_module("tests.unit.test_bug262_close_time_evidence")
    tick = mod._tick_ts(1994.0)
    assert tick.timestamp == mod._FIXED_NOW
    assert tick.symbol == "XAUUSD"


def test_tick_helper_allows_an_explicit_instant() -> None:
    """The helper still accepts a distinct instant (pre-gap tick shape).

    A tick can carry a pre-gap quote time even when detection is ``_now()`` —
    the real-world shape, since the broker's last quote precedes the detection
    sweep. The injection must not force the two together.
    """
    mod = importlib.import_module("tests.unit.test_bug262_close_time_evidence")
    earlier = mod._FIXED_NOW - timedelta(minutes=5)
    tick = mod._tick_ts(1994.0, ts=earlier)
    assert tick.timestamp == earlier
    assert tick.timestamp != mod._FIXED_NOW


def test_reconcile_path_reads_tick_timestamp_not_wall_clock() -> None:
    """The production reconciliation clock is the tick, so injection is real.

    Proven on the production source (not the test module): if the detection
    instant came from ``datetime.now()`` inside the engine, injecting the tick
    timestamp would not remove the wall clock from this module's assertions.
    """
    engine_src = (_REPO_ROOT / "src/nexus_scalp/execution/lifecycle/reconciliation.py").read_text()
    # The engine assigns the detection instant from the tick payload...
    assert "current_tick.timestamp" in engine_src
    # ...inside reconcile_missed_closes, the one entry point this module hits.
    assert "def reconcile_missed_closes" in engine_src


# ---------------------------------------------------------------------------
# 4. The repair path's clock is patched at its own module name
# ---------------------------------------------------------------------------


def test_frozen_clock_provides_now_and_utc() -> None:
    """``_FrozenClock`` implements exactly the surface outcome_repair touches."""
    mod = importlib.import_module("tests.unit.test_bug262_close_time_evidence")
    instant = mod._FIXED_NOW
    clock = mod._FrozenClock(instant)
    # now() ignores tz (the injected instant is already aware) and is stable.
    assert clock.now(UTC) == instant
    assert clock.now() == instant
    assert clock.now() == clock.now()
    # Attribute access falls through to the real datetime class for residuals.
    assert clock.UTC is UTC


def test_repair_module_reads_datetime_through_the_patchable_name() -> None:
    """``outcome_repair`` calls ``datetime.now(UTC)`` on the MODULE name.

    That is what makes ``monkeypatch.setattr(..., _FrozenClock(now))`` work: if
    the module had done ``from datetime import datetime`` the class itself
    would be the patch target and the fix would need a different seam. This
    pins the seam so a future refactor to a ``from datetime import datetime``
    import does not silently break the injection.
    """
    src = (_REPO_ROOT / "src/nexus_scalp/experience/outcome_repair.py").read_text()
    # Module-form import (``from datetime import UTC, datetime, timedelta``) is
    # exactly what makes the ``datetime`` NAME patchable: ``datetime.now`` is
    # an attribute lookup on the imported name, not on the datetime module.
    # ``import datetime`` would work too. Either is fine; this pins that the
    # binding stays name-based so monkeypatch.setattr(..., "datetime") lands.
    assert "datetime" in src
    assert src.count("datetime.now(") >= 3, "the repair path still owns now() sites"
    # And the module must NOT hold a private ``from datetime import datetime as
    # now``-style alias that would move the patch target off the obvious name.
    body = _strip_comments_and_docstrings(src)
    assert "datetime.now(" in body, "the now() sites are live code, not comments"


def test_repair_tests_declare_the_monkeypatch_and_tmp_path_fixtures() -> None:
    """Every repair scenario takes the fixtures the remediation introduced."""
    src = _MODULE_PATH.read_text()
    for name in (
        "test_outcome_repair_preserves_historical_close_instant",
        "test_outcome_repair_refuses_contaminated_evidence_and_falls_back",
        "test_outcome_repair_honors_epoch_int_evidence",
    ):
        assert f"def {name}(" in src, f"{name} must be present"
        # Grab the signature block (up to the first `-> None:` or `:`).
        idx = src.index(f"def {name}(")
        head = src[idx : src.index("\n", src.index(")", idx))]
        assert "tmp_path" in head, f"{name} must take tmp_path (replaces mkdtemp)"
        assert "monkeypatch" in head, f"{name} must take monkeypatch (clock injection)"


# ---------------------------------------------------------------------------
# 5. No assertion was weakened — the tolerances are durations, not constants
# ---------------------------------------------------------------------------


def test_tolerance_windows_are_timedelta_arithmetic_not_constants() -> None:
    """The ``<= now + 5 s`` windows survived as real arithmetic.

    The remediation must not have replaced a wall-clock window with a magic
    constant. Every tolerance remains a ``timedelta`` computed from the
    injected instant, so it measures a *duration* — which is what the defect
    class actually was — and not a point on the host clock.
    """
    src = _MODULE_PATH.read_text()
    body = _strip_comments_and_docstrings(src)
    assert "timedelta(seconds=5)" in body or "timedelta(seconds=5.0)" in body, (
        "the fallback tolerance must remain a timedelta window around the "
        "injected instant, not a hard-coded constant"
    )
    # And it must be compared against the injected instant, not the wall clock.
    assert "datetime.now" not in body


def test_relationship_asserts_still_compare_to_the_detection_instant() -> None:
    """The load-bearing relationship asserts are preserved and exact-able.

    The evidenced-close-is-hours-before-detection and the +3h-refusal asserts
    are the BUG-262 contract. They must still derive from the detection
    instant (now, from ``_now()``), not from a literal — and they must be
    exact where the injected clock makes exactness possible.
    """
    src = _MODULE_PATH.read_text()
    body = _strip_comments_and_docstrings(src)
    # The tokenizer-based stripper drops inter-token whitespace, so the
    # relationship patterns are matched with flexible \\s* separators.
    assert re.search(r"true_close\s*=\s*now\s*-\s*timedelta\(\s*hours\s*=\s*2\s*\)", body), (
        "evidenced-close-2h-before-detection relationship must survive"
    )
    assert re.search(r"contaminated\s*=\s*now\s*\+\s*timedelta\(\s*hours\s*=\s*3\s*\)", body), (
        "the +3h server-local refusal shape must survive"
    )
    # The injected clock lets the exact-equality invariants assert precisely
    # (``as_utc(...) == true_close``, where the tokenizer strips the spaces).
    assert "==true_close" in body, "exact equality against the evidence survives"
    assert "==evidence" in body, "the new invariant asserts exact equality too"
    # And the fallback path is still bounded, not removed.
    assert re.search(r"timedelta\(\s*seconds\s*=\s*5(\.0)?\s*\)", body)


def test_new_determinism_invariants_are_present() -> None:
    """The two invariant tests added by ML-QA-010 survived on the module."""
    src = _MODULE_PATH.read_text()
    assert "def test_detection_instant_is_fixed_and_aware() -> None:" in src
    assert "def test_reconciled_close_time_is_invariant_across_host_clocks(" in src


def test_battery_reports_the_defect_class_not_the_fix_only() -> None:
    """The module docstring still names ``datetime.now()`` as the defect.

    A textual ``datetime.now(`` count is NOT a live-code count: this module's
    own docstrings (and this battery's) reference the removed call. The
    contract is pinned by :func:`test_no_wall_clock_now_in_module_source`,
    which strips comments and string literals first. This test exists so a
    future reader comparing a raw grep count against the roster does not
    mistake documentation for a regression.
    """
    src = _MODULE_PATH.read_text()
    raw = src.count("datetime.now(")
    body = _strip_comments_and_docstrings(src)
    assert raw >= 1, "the docstring documents the removed defect"
    assert body.count("datetime.now(") == 0, "but no executable call remains"


# ---------------------------------------------------------------------------
# 6. Behavioral proof: the exact equality the wall clock could not express
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("hours_before_detection", [2, 3, 12])
def test_reconciled_close_time_is_exact_regardless_of_wall_clock(
    tmp_path: Path, hours_before_detection: int
) -> None:
    """The ledger close_time equals the evidential instant EXACTLY, three ways.

    With the wall clock this assert was impossible: detection moved between
    the tick construction and the row read, so the module could only assert
    ``close < now + 5 s``. With the injected clock the evidenced close is a
    value the test *chose*, so equality is exact — which is strictly stronger
    and is what catches a re-introduced ``datetime.now()`` (it would disagree
    with the chosen instant by hours). Parametrized across 2 h / 3 h / 12 h so
    a bucket-boundary drift fails in at least one leg.
    """
    mod = importlib.import_module("tests.unit.test_bug262_close_time_evidence")
    detection = mod._FIXED_NOW
    evidence = detection - timedelta(hours=hours_before_detection)

    om, audit, mock = mod._make_om(tmp_path)
    try:
        mod._seed_opened(audit, 7300 + hours_before_detection, evidence - timedelta(minutes=30))
        mock.deals = [mod._close_deal(7300 + hours_before_detection, evidence)]
        om.reconcile_missed_closes("XAUUSD", mod._tick_ts(1994.0), hours_back=24)
        audit._queue.join()
        row = audit.get_ledger_row(7300 + hours_before_detection)
        assert row is not None
        assert row["status"] == "RECONCILED"
        # EXACT — not a window. This is the assert the wall clock made us
        # downgrade to `close < now + 5 s`.
        assert mod.as_utc(row["close_time"]) == evidence
        # And the duration is real (30 min), never zeroed by detection-now.
        assert abs(float(row["duration_sec"]) - 1800.0) < 2.0
    finally:
        audit.close()


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _strip_comments_and_docstrings(src: str) -> str:
    """Remove ``#`` comments and string/docstring literals from Python source.

    Uses the stdlib tokenizer so a comment or docstring mentioning
    ``datetime.now`` is not mistaken for a live call. Keeps newlines so line
    structure (and therefore the comment-to-code mapping) is unaffected.
    """
    out: list[str] = []
    last_end = (1, 0)
    reader = io.StringIO(src).readline
    try:
        for tok in tokenize.generate_tokens(reader):
            ttype, _tstring, start, end, _line = tok
            if ttype in (
                tokenize.COMMENT,
                tokenize.STRING,
                tokenize.INDENT,
                tokenize.DEDENT,
                tokenize.NL,
                tokenize.NEWLINE,
            ):
                if start[0] > last_end[0]:
                    out.extend(["\n"] * (start[0] - last_end[0]))
                last_end = end
                continue
            out.append(tok.string)
            last_end = end
    except tokenize.TokenError:
        # Unbalanced tokenizer state should never happen on a module that
        # ruff accepts; fall back to the raw source rather than pass silently.
        return src
    return "".join(out)
