"""ML-QA-015 contract battery: the BUG-275 hygiene-cadence suite exercises the
cadence gates through an injected wall clock.

WHAT THIS PINS
--------------
``tests/unit/test_bug275_hygiene_cadence_clock.py`` is a push-gate module
(``tests/critical_suite.txt`` entry at line 314). The ML-QA-003 determinism
census (``docs/ml-system/test_determinism_roster.md`` section 6, recount)
listed it as 3 live wall-clock sources: ``time.time()`` in
``test_cadence_stamps_land_in_wall_domain``, ``test_deep_gate_respects_interval_after_fix``
and ``test_telegram_cooldown_default_stamp_is_wall``.

The defect class
----------------
The production cadence gates compare two caller-domain values:
``(now - stamp) >= interval`` where the caller (``MaintenanceCycle.run_cycle``,
``maintenance.py:115``) supplies a wall ``now_t = time.time()`` and
``run_cycle`` stamps ``_last_light`` / ``_last_deep`` / ``_last_telegram`` from
``time.time()``. The tests *drove* that same clock with live reads, so the
suite's coverage of the cadence boundary was a property of when the run
happened rather than of the code: every stamp and every probe was a fresh
real-clock read, and the deep interval (6h) could only be crossed by waiting
6 hours — so only the NOT-due side of the boundary was ever exercised.

The remediation
---------------
One deterministic wall clock, injected through the production module's own
``time`` reference (a ``monkeypatch`` fixture swapping
``hygiene_runtime.time`` for a stand-in whose ``time`` reads a controllable
epoch). Production behaviour is unchanged: every ``time.time()`` call site in
``hygiene_runtime`` is exercised through the swapped reference, and the cadence
arithmetic compares caller-domain values either way. The fake reads a
realistic EPOCH (1.9e9) rather than 0.0 — the production domain is wall, so a
domain regression (a monotonic stamp, ~1e6) must still show up as an
out-of-range stamp instead of coincidentally passing.

The boundary tests then advance the clock instead of waiting, so BOTH edges of
the inclusive ``>=`` comparison are reproducible to the nanosecond: one tick
short of the interval stays NOT due; exactly AT the interval is DUE.

This battery is deliberately TEXTUAL where the rule is about the source shape
and BEHAVIORAL where the invariant is only provable by executing the path
(the cadence arithmetic on the real scheduler).
"""

from __future__ import annotations

import io
import time as _stdlib_time
import tokenize
from pathlib import Path

import pytest

_REAL_MONOTONIC = _stdlib_time.monotonic
_REAL_PERF_COUNTER = _stdlib_time.perf_counter
_REAL_SLEEP = _stdlib_time.sleep

# Path resolution note (the ML-QA-011/012/013 trap): ``Path(__file__).resolve()``,
# ``inspect.getfile(module)`` and an imported module's ``__file__`` all
# canonicalise to the SHARED checkout on this repo, so a textual rule reading
# the analysed module through them inspects the un-patched original and fails
# while the module under test is correct. THIS battery file's own un-resolved
# parent directory is the only anchor that survives pytest's rootdir-relative
# import, so the analysed module is a sibling join.
_BATTERY_DIR = Path(__file__).parent
_REPO_ROOT = _BATTERY_DIR.parents[1]
_MODULE_PATH = _BATTERY_DIR / "test_bug275_hygiene_cadence_clock.py"
_PRODUCTION = _REPO_ROOT / "src/nexus_scalp/hygiene/hygiene_runtime.py"

# The durable contract tests of the BUG-275 suite, by name. A remediation that
# removed or renamed any of them would silently delete a regression proof.
_DURABLE_TEST_NAMES = (
    "test_cadence_stamps_land_in_wall_domain",
    "test_deep_gate_respects_interval_after_fix",
    "test_telegram_cooldown_default_stamp_is_wall",
    "test_next_light_in_and_status_are_sane",
    "test_maintenance_caller_uses_wall_now_for_hygiene_gates",
)

# The tests that exercise cadence arithmetic and must therefore take the
# deterministic clock fixture.
_CLOCKED_TEST_NAMES = (
    "test_cadence_stamps_land_in_wall_domain",
    "test_deep_gate_respects_interval_after_fix",
    "test_telegram_cooldown_default_stamp_is_wall",
    "test_next_light_in_and_status_are_sane",
    "test_light_gate_both_edges_of_the_inclusive_boundary",
    "test_deep_gate_both_edges_of_the_inclusive_boundary",
    "test_telegram_gate_both_edges_of_the_inclusive_boundary",
    "test_next_light_in_counts_down_to_the_exact_boundary",
    "test_a_light_cycle_advances_only_the_light_stamp",
    "test_deep_cycle_refreshes_both_stamps",
    "test_initial_zero_stamps_make_every_gate_due_immediately",
)

_WALL_EPOCH = 1_900_000_000.0


class _FakeModuleTime:
    """Minimal stand-in exposing the attributes ``hygiene_runtime`` actually
    uses: ``time`` (the cadence domain, swapped) and ``monotonic`` (the
    duration timer, kept real so the behavioural leg measures something)."""

    def __init__(self, time_fn) -> None:
        self.time = time_fn
        self.monotonic = _REAL_MONOTONIC
        self.perf_counter = _REAL_PERF_COUNTER
        self.sleep = _REAL_SLEEP


class _WallClock:
    """The battery's own copy of the deterministic clock (see the behavioural
    leg: it must not import the analysed module's clock, or the leg would
    tautologically assert the module's state against itself)."""

    def __init__(self) -> None:
        self._t = _WALL_EPOCH

    def time(self) -> float:
        return self._t

    def read(self) -> float:
        return self._t

    def advance(self, seconds: float) -> float:
        self._t += seconds
        return self._t

    def rewind(self, seconds: float) -> float:
        self._t -= seconds
        return self._t


# ---------------------------------------------------------------------------.
# Source analysis helpers (tokenize-based, no `re` import — a `re/` package or
# module ahead of stdlib on sys.path can drop a negative lookahead and INVERT a
# textual rule; these helpers walk the source text directly.)
# ---------------------------------------------------------------------------.


def _code_lines(src: str) -> str:
    """Executable source lines only, with original character text preserved.

    A row counts as code only when it carries at least one token that is
    neither a comment nor part of a string literal. This is deliberately
    token-based rather than AST-node-span-based: an AST node spans its whole
    docstring, so a ``def`` whose docstring names ``time.time()`` (exactly
    how these regressions stay explained) counts as a live call under a span
    extractor. The modules here document the removed defect in their
    docstrings, so string-literal interiors must be excluded explicitly.
    """
    rows = src.splitlines(keepends=True)
    code_rows: set[int] = set()
    non_code: set[int] = set()
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except tokenize.TokenError:
        return src
    for tok in tokens:
        kind = tok.type
        if kind in (
            tokenize.COMMENT,
            tokenize.NL,
            tokenize.NEWLINE,
            tokenize.INDENT,
            tokenize.DEDENT,
            tokenize.ENDMARKER,
            tokenize.ENCODING,
        ):
            continue
        if kind == tokenize.STRING:
            continue
        if kind == getattr(tokenize, "FSTRING_MIDDLE", -1):
            continue
        first, last = tok.start[0], max(tok.start[0], tok.end[0])
        if (
            kind == tokenize.NAME
            or kind == getattr(tokenize, "OP", -1)
            or kind == getattr(tokenize, "AT", -1)
        ):
            for row in range(first, last + 1):
                code_rows.add(row)
        else:
            for row in range(first, last + 1):
                non_code.add(row)
    final = code_rows - non_code
    out: list[str] = []
    for idx, line in enumerate(rows, start=1):
        if idx not in final:
            continue
        text = _strip_trailing_comment(line).rstrip()
        if text.strip():
            out.append(text)
    return "\n".join(out)


def _tokenize_line(segment: str) -> list[tokenize.TokenInfo]:
    """Tokenizes one physical line, tolerant of unparseable fragments."""
    try:
        return list(tokenize.generate_tokens(io.StringIO(segment).readline))
    except tokenize.TokenError:
        return []


def _strip_trailing_comment(line: str) -> str:
    """Removes a trailing ``#`` comment from one physical line (a ``#`` inside a
    string literal cannot truncate real code: the comment token must start at
    or beyond the last real token's end column)."""
    for tok in _tokenize_line(line):
        if tok.type == tokenize.COMMENT and tok.start[1] >= _last_code_col(line):
            return line[: tok.start[1]]
    return line


def _last_code_col(segment: str) -> int:
    """The end column of the last non-comment token on the line."""
    last = 0
    for tok in _tokenize_line(segment):
        if tok.type in (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT):
            continue
        last = max(last, tok.end[1])
    return last


def _call_spans(code: str, dotted: str) -> list[tuple[int, int]]:
    """All spans of ``dotted(`` in ``code``, ignoring longer names.

    Walks the text so no regex engine and no ``re`` import is involved. A longer
    name (``time.times``) is not a call of ``dotted``.
    """
    out: list[tuple[int, int]] = []
    pos = 0
    n = len(dotted)
    while True:
        idx = code.find(dotted, pos)
        if idx < 0:
            break
        after = idx + n
        if after < len(code) and code[after] == "(":
            out.append((idx, after + 1))
        elif after < len(code) and (code[after].isalnum() or code[after] == "_"):
            pass  # longer name, not a call of dotted
        pos = after
    return out


def _no_call(code: str, dotted: str) -> bool:
    """True when ``dotted`` (e.g. ``time.time``) does NOT appear as a call."""
    return not _call_spans(code, dotted)


def _source() -> str:
    return _MODULE_PATH.read_text(encoding="utf-8")


def _slice(name: str) -> str:
    """Source of one top-level test, by def name.

    Walks physical lines and accepts only a ``def `` at column 0 as the
    terminator (the next sibling), which correctly skips nested closures.
    """
    lines = _source().splitlines(keepends=True)
    needle = f"def {name}"
    try:
        start = next(i for i, ln in enumerate(lines) if ln.lstrip().startswith(needle))
    except StopIteration:
        # a missing test must fail its rules on the ASSERTION, not by crashing
        # the battery (a crash is still a failure, but its output is opaque)
        return ""
    indent = len(lines[start]) - len(lines[start].lstrip())
    for j in range(start + 1, len(lines)):
        stripped = lines[j].lstrip()
        if stripped.startswith("def ") and (len(lines[j]) - len(lines[j].lstrip())) <= indent:
            return "".join(lines[start:j])
    return "".join(lines[start:])


# ===========================================================================
# 1. The cadence tests no longer read the wall clock
# ===========================================================================


def test_no_wall_clock_call_in_cadence_tests() -> None:
    """No live ``time.time()`` / ``time.monotonic()`` / ``time.perf_counter()``
    call remains in any cadence test's executable lines.

    Occurrences inside docstrings/comments are permitted — the contract is
    about executed code, and the BUG-275 docstring naming the removed defect
    is how the regression stays explained. The wiring pin
    (``test_maintenance_caller_uses_wall_now_for_hygiene_gates``) is out of
    scope here BY DESIGN: its string-literal assertions legitimately quote
    ``time.time()``/``time.monotonic()`` while pinning the production wiring,
    and its own purity is pinned by ``test_wiring_pin_kept_verbatim``.
    """
    for name in _CLOCKED_TEST_NAMES:
        body_code = _code_lines(_slice(name))
        for dotted in ("time.time", "time.monotonic", "time.perf_counter"):
            assert _no_call(body_code, dotted), (
                f"{name} must not call {dotted}(): the cadence gates compare "
                "two caller-domain values, so a live read contributes nothing "
                "but makes which side of the boundary the test sits on a "
                "property of when the run happens instead of the code under test"
            )


def test_no_time_import_needed() -> None:
    """``import time`` is gone from the module: the only consumer was the
    removed wall-clock read, so a surviving import is dead weight or a latent
    reintroduction of it."""
    src = _source()
    assert "import time" not in src, "no time.* call remains, so the import is dead"


def test_deterministic_wall_clock_defined() -> None:
    """One module-level deterministic wall clock drives the cadence
    arithmetic, and it reads a realistic EPOCH (the production domain is
    wall/epoch — a 0.0-based fake would let a monotonic-domain regression pass
    by coincidence)."""
    src = _source()
    assert "class _WallClock:" in src
    assert "_WALL_CLOCK = _WallClock()" in src, "one shared clock, not many"
    assert "_WALL_EPOCH = 1_900_000_000.0" in src, (
        "the fake must read a realistic epoch: the BUG-275 defect is only "
        "distinguishable from correct behaviour in the wall domain"
    )
    code = _code_lines(_source())
    assert code.count("_WALL_CLOCK = _WallClock()") == 1, "one clock, not many"


def test_clock_is_applied_through_the_production_module_reference() -> None:
    """The clock is swapped on the PRODUCTION MODULE's ``time`` reference
    (``monkeypatch.setattr(hygiene_module, "time", ...)``), not by stubbing an
    instance attribute nothing reads.

    ``run_cycle`` calls the module-global ``time.time()``, so an instance-level
    ``sched._time`` injection is silently inert (this exact authoring mistake
    was caught by the first run of this task). The rule is textual because the
    failure mode is silent: the suite passes while exercising nothing.
    """
    src = _source()
    assert 'monkeypatch.setattr(_hygiene_module, "time"' in src, (
        "the clock must replace the module's time reference — that is what "
        "run_cycle's time.time() resolves to"
    )
    assert "hygiene_runtime as _hygiene_module" in src, (
        "the module object must be imported for the monkeypatch target"
    )


def test_clock_is_a_fixture_that_resets_between_tests() -> None:
    """A fixture must reset the module-level clock so one test's advance cannot
    leak into the next test's boundary arithmetic (tests that share a mutable
    clock and never reset are order-dependent)."""
    src = _source()
    assert "def hygiene_clock(monkeypatch):" in src, (
        "the clock must be a fixture: a module-level clock advanced by tests "
        "without a reset makes the suite order-dependent"
    )
    assert "@pytest.fixture" in src, "the clock must be a pytest fixture"


def test_cadence_tests_take_the_clock_fixture() -> None:
    """Every cadence test accepts the ``hygiene_clock`` fixture (a test that
    keeps calling the scheduler without it silently runs on the real clock)."""
    for name in _CLOCKED_TEST_NAMES:
        body = _slice(name)
        assert "hygiene_clock" in body, f"{name} must take the hygiene_clock fixture"


def test_cadence_tests_never_read_the_clock_directly() -> None:
    """The cadence tests must reach the clock through the fixture (the
    ``hygiene_clock`` name), never by calling the wall clock themselves.

    The fixture's own body legitimately mentions ``time.time`` while wiring the
    stand-in, and the module docstring documents the removed defect, so the
    rule pins EXECUTABLE lines of the cadence tests only."""
    for name in _CLOCKED_TEST_NAMES:
        body = _slice(name)
        assert "hygiene_clock" in body, (
            f"{name} must take the hygiene_clock fixture, not read a clock"
        )
        body_code = _code_lines(body)
        assert _no_call(body_code, "time.time"), f"{name} must not call the wall clock directly"


def test_no_production_code_changed() -> None:
    """ML-QA-015 is test-only: the scheduler is clock-INJECTED, not patched in
    place. The production cadence arithmetic is byte-for-byte unchanged."""
    prod = _PRODUCTION.read_text(encoding="utf-8")
    for construct in (
        "def is_light_due(self, now: float) -> bool:",
        "def is_deep_due(self, now: float) -> bool:",
        "def is_telegram_due(self, now: float) -> bool:",
        "self._last_light = time.time()",
        "self._last_deep = time.time()",
        "self._last_telegram = now or time.time()",
    ):
        assert construct in prod, (
            f"production contract unchanged: {construct!r} must still be present"
        )
    # the BUG-275 fix itself must stay in place
    assert "self._last_deep = time.monotonic()" not in prod
    assert "self._last_light = time.monotonic()" not in prod


def test_wiring_pin_kept_verbatim() -> None:
    """The md7-class wiring pin (the engine caller supplies time.time() and
    the scheduler never stamps monotonic) is a durable regression guard and
    must survive the determinism pass byte-for-byte."""
    src = _source()
    assert "def test_maintenance_caller_uses_wall_now_for_hygiene_gates(" in src
    body = _slice("test_maintenance_caller_uses_wall_now_for_hygiene_gates")
    for needle in (
        "now_t = time.time()",
        "is_deep_due(now_t)",
        "self._last_deep = time.monotonic()",
        "self._last_light = time.monotonic()",
    ):
        assert needle in body, f"the wiring pin must still assert {needle!r}"


def test_durable_test_names_unchanged() -> None:
    """The five durable contract tests of the BUG-275 suite, by name. A
    remediation that removed or renamed any of them would silently delete a
    regression proof."""
    for name in _DURABLE_TEST_NAMES:
        assert f"def {name}(" in _source(), (
            f"durable contract test {name!r} must remain in the module"
        )


def test_boundary_tests_exist() -> None:
    """The new ML-QA-015 boundary tests: both edges of the inclusive ``>=``
    cadence comparison for each of the three gates, plus the countdown and
    stamp-separation invariants."""
    for name in (
        "test_light_gate_both_edges_of_the_inclusive_boundary",
        "test_deep_gate_both_edges_of_the_inclusive_boundary",
        "test_telegram_gate_both_edges_of_the_inclusive_boundary",
        "test_next_light_in_counts_down_to_the_exact_boundary",
        "test_a_light_cycle_advances_only_the_light_stamp",
        "test_deep_cycle_refreshes_both_stamps",
        "test_initial_zero_stamps_make_every_gate_due_immediately",
    ):
        assert f"def {name}(" in _source(), f"the boundary invariant {name!r} must be pinned"


def test_boundary_tests_assert_both_edges() -> None:
    """Each boundary test must assert BOTH edges (the NOT-due side one tick
    short AND the due side exactly at the interval). A test that only asserts
    one side reproduces the original coverage hole."""
    for name, gate in (
        ("test_light_gate_both_edges_of_the_inclusive_boundary", "is_light_due"),
        ("test_deep_gate_both_edges_of_the_inclusive_boundary", "is_deep_due"),
        ("test_telegram_gate_both_edges_of_the_inclusive_boundary", "is_telegram_due"),
    ):
        body = _slice(name)
        assert gate in body, f"{name} must exercise {gate}"
        assert "is False" in body, (
            f"{name} must assert the NOT-due edge (one tick short of interval)"
        )
        assert "is True" in body, f"{name} must assert the due edge (exactly at the interval)"
        assert "- 0.001" in body, f"{name} must probe one tick short, not a rounded second"


def test_critical_suite_manifest_entries() -> None:
    manifest = (_REPO_ROOT / "tests/critical_suite.txt").read_text(encoding="utf-8")
    assert "tests/unit/test_bug275_hygiene_cadence_clock.py" in manifest
    assert "tests/unit/test_ml_qa_015_hygiene_cadence_clock_determinism.py" in manifest


# ===========================================================================
# 3. Executed behaviour: the boundary arithmetic on the real scheduler
# ===========================================================================


def test_clock_fixture_makes_run_cycle_stamps_deterministic(tmp_path, monkeypatch) -> None:
    """Executed proof: ``run_cycle`` stamps land on the injected clock value,
    not on the real epoch (the injection is what makes the boundary tests
    deterministic)."""
    from nexus_scalp.hygiene import hygiene_runtime as hy

    clock = _WallClock()
    monkeypatch.setattr(hy, "time", _FakeModuleTime(clock.time))
    from nexus_scalp.hygiene.hygiene_runtime import (
        RuntimeCleanupScheduler,
        RuntimeHygieneSettings,
    )

    sched = RuntimeCleanupScheduler(
        repo_root=tmp_path,
        settings=RuntimeHygieneSettings(enabled=True, dry_run=True),
    )

    class _StubWorker:
        mode = type("M", (), {"value": "AUDIT_ONLY"})()

        def run_cycle(self, _dbs):
            return {"databases": {}, "mode": "AUDIT_ONLY", "verification": "OK", "run_id": "t"}

        def status(self):
            return {"state": "IDLE"}

    sched._ensure_worker = _StubWorker  # type: ignore[method-assign]
    sched._run_initial_audit = lambda: {}
    sched.run_cycle(deep=True)
    assert sched._last_light == clock.read() == _WALL_EPOCH
    assert sched._last_deep == clock.read()
