"""ML-QA-013 contract battery: the BUG-285 overflow-drain suite exercises the
production drain throttle through an injected monotonic clock.

WHAT THIS PINS
--------------
``tests/unit/test_bug285_overflow_drain.py`` is a push-gate module
(``tests/critical_suite.txt`` entry at line 323). The ML-QA-003 determinism
census (``docs/ml-system/test_determinism_roster.md`` section 6, recount) listed
it as 3 live wall-clock sources: two ``time.monotonic()`` reads used to *drive*
the cadence tests (``repo._last_overflow_drain = time.monotonic()`` and
``= time.monotonic() - (INTERVAL + 1)``) plus the prose reference in the
BUG-273 docstring.

The defect class
----------------
The production throttle compares ``time.monotonic()`` against the stored stamp
over ``OVERFLOW_RECOVERY_INTERVAL_SEC`` (60s, audit_repository.py:3272). The
tests simulated the passage of that interval by *writing their own stamps* from
the same clock. Two independent wall-clock reads and one hand-subtracted stamp
meant the suite's throttle coverage depended on the host's monotonic *domain*
rather than on the comparison under test: whether ``(now - stamp)`` landed above
or below the interval was a property of the host clock domain, not of the code.
Nothing about the contract needed the real clock — the throttle compares two
caller-domain values.

The remediation
---------------
One deterministic monotonic-domain clock, injected via the production method's
optional argument (``now: float | None = None``, the same idiom the repo already
uses in ``storage/runtime.py::is_due``). Production behaviour is unchanged: with
``now is None`` the method still reads ``time.monotonic()``. The cadence tests
advance the injected clock instead, so BOTH edges of the strict ``<`` boundary
are reproducible to the nanosecond, and the boundary can be exercised without
waiting out a real 60-second interval.

This battery is deliberately TEXTUAL where the rule is about the source shape
and BEHAVIORAL where the invariant is only provable by executing the path (the
boundary arithmetic on the real repository).
"""

from __future__ import annotations

import io
import sqlite3
import tokenize
from pathlib import Path

import pytest

# Path resolution note (the ML-QA-011/012 trap): ``Path(__file__).resolve()``,
# ``inspect.getfile(module)`` and an imported module's ``__file__`` all
# canonicalise to the SHARED checkout on this repo, so a textual rule reading
# the analysed module through them inspects the un-patched original and fails
# while the module under test is correct. THIS battery file's own un-resolved
# parent directory is the only anchor that survives pytest's rootdir-relative
# import, so the analysed module is a sibling join.
_BATTERY_DIR = Path(__file__).parent
_REPO_ROOT = _BATTERY_DIR.parents[1]
_MODULE_PATH = _BATTERY_DIR / "test_bug285_overflow_drain.py"
_PRODUCTION = _REPO_ROOT / "src/nexus_scalp/adapters/database/audit_repository.py"

# The durable contract tests of the BUG-285 suite, by name. A remediation that
# removed or renamed any of them would silently delete a regression proof.
_DURABLE_TEST_NAMES = (
    "test_overflow_row_written_by_producer_is_replayed_into_the_ledger",
    "test_replay_of_a_row_that_already_landed_is_a_noop",
    "test_first_drain_is_always_due_none_sentinel",
    "test_drain_throttled_to_one_pass_per_interval",
    "test_drain_batch_is_bounded",
    "test_unreplayable_overflow_is_dead_lettered_not_retried",
    "test_replay_sql_error_dead_letters_and_preserves_other_rows",
    "test_overflow_writer_cap_routes_row_to_dead_letter",
    "test_drain_is_wired_into_the_audit_worker_idle_pass",
    "test_writer_and_drainer_share_one_path_resolution",
    "test_debug_snapshot_surfaces_recovery_counters",
    "test_overflow_counter_semantics_are_exact",
)


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
    docstring, so a ``def`` whose docstring names ``time.monotonic()`` (exactly
    how these regressions stay explained) counts as a live call under a span
    extractor. The ML-QA-012 battery's node-span helper has that latent shape;
    it never tripped there only because that module's docstrings happened not
    to name a removed call. The modules here document the removed defect in
    their docstrings, so string-literal interiors must be excluded explicitly.

    ``ast`` is not needed at all with this approach, and ``re`` is still never
    imported (a shadowing ``re`` module ahead of the stdlib on ``sys.path`` can
    drop a negative lookahead and invert a rule; ``tokenize`` over the real
    text has no such failure mode).
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
            # the whole literal — docstring or argument — is not code
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
    # a row carrying only non-code tokens (numbers, strings, dotted names inside
    # a literal) must not be reported as code
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
    name (``time.monotonics``) is not a call of ``dotted``.
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
    """True when ``dotted`` (e.g. ``time.monotonic``) does NOT appear as a call."""
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
    start = next(i for i, ln in enumerate(lines) if ln.lstrip().startswith(needle))
    indent = len(lines[start]) - len(lines[start].lstrip())
    for j in range(start + 1, len(lines)):
        stripped = lines[j].lstrip()
        if stripped.startswith("def ") and (len(lines[j]) - len(lines[j].lstrip())) <= indent:
            return "".join(lines[start:j])
    return "".join(lines[start:])


# ===========================================================================
# 1. The cadence tests no longer read the wall clock
# ===========================================================================


def test_no_wall_clock_call_in_module_source() -> None:
    """No live ``time.monotonic()`` / ``time.perf_counter()`` call remains in
    the module.

    Occurrences inside docstrings/comments are permitted — the contract is
    about executed code, and the BUG-273 comment naming the removed defect is
    how the regression stays explained.
    """
    code = _code_lines(_source())
    assert _no_call(code, "time.monotonic"), (
        "the cadence tests must not read the wall clock: the throttle compares "
        "two caller-domain values, so a live read contributes nothing but makes "
        "which side of the boundary the test sits on a property of the host "
        "clock domain instead of the code under test"
    )
    assert _no_call(code, "time.perf_counter"), "same defect class as monotonic"


def test_no_time_import_needed() -> None:
    """``import time`` is gone from the module: the only consumers were the two
    removed probes, so a surviving import would be dead weight or a latent
    reintroduction of the wall-clock read."""
    src = _source()
    assert "import time" not in src, "no time.* call remains, so the import is dead"


def test_deterministic_clock_defined_and_used() -> None:
    """One module-level deterministic clock drives the cadence arithmetic."""
    src = _source()
    assert "class _MonotonicClock:" in src
    assert "_MONO_CLOCK = _MonotonicClock()" in src
    assert "def drain_clock():" in src, (
        "a fixture must reset the module-level clock so one test's advance "
        "cannot leak into the next one's boundary arithmetic"
    )
    code = _code_lines(_source())
    assert "now=drain_clock.read()" in code or "now=drain_clock.advance(" in code, (
        "the cadence tests must pass the clock value to the production method"
    )


def test_cadence_tests_take_the_injected_clock() -> None:
    """Both cadence tests accept the ``drain_clock`` fixture (a test that keeps
    calling the method without it silently falls back to the real clock)."""
    for name in (
        "test_drain_throttled_to_one_pass_per_interval",
        "test_drain_throttle_arithmetic_is_the_production_comparison",
    ):
        body = _slice(name)
        assert "drain_clock" in body, f"{name} must take the drain_clock fixture"


def test_cadence_tests_never_write_the_stamp_themselves() -> None:
    """The removed defect: the tests *assigned* ``repo._last_overflow_drain``
    from a wall-clock read to simulate the interval passing. The production
    method must own that write; a test that hand-stamps it is testing its own
    assignment."""
    for name in (
        "test_drain_throttled_to_one_pass_per_interval",
        "test_drain_throttle_arithmetic_is_the_production_comparison",
    ):
        body = _slice(name)
        assert "_last_overflow_drain =" not in body, (
            f"{name} must not hand-write the drain stamp; the production method "
            "stores it from the injected clock value"
        )


# ===========================================================================
# 2. The production seam (minimal, and it preserves the default behaviour)
# ===========================================================================


def test_production_method_takes_an_optional_now() -> None:
    """The drain accepts an explicit monotonic-domain value, defaulting to the
    real clock so production behaviour is byte-for-byte unchanged."""
    src = _PRODUCTION.read_text(encoding="utf-8")
    assert (
        "def _drain_financial_overflow_due(\n        self, conn: sqlite3.Connection, now: float | None = None\n    ) -> None:"
        in src
    ), (
        "the seam is an optional ``now`` argument (the same idiom the repo "
        "already uses in storage/runtime.py::is_due), not a test-only attribute"
    )
    assert "now = time.monotonic() if now is None else now" in src, (
        "with no argument the method must still read the real monotonic clock"
    )


def test_production_caller_is_unchanged() -> None:
    """The one production caller still invokes the drain with no clock argument,
    so the runtime path is untouched by the test-side determinism work."""
    src = _PRODUCTION.read_text(encoding="utf-8")
    assert "self._drain_financial_overflow_due(conn)" in src


def test_no_production_attribute_injection() -> None:
    """No test-only ``_monotonic_now``/clock attribute was added to the
    production class: an instance attribute a test overwrites is a wider seam
    than a method argument and can mask a real drift bug."""
    src = _PRODUCTION.read_text(encoding="utf-8")
    assert "_monotonic_now" not in src


# ===========================================================================
# 3. The durable contracts survived the clock change
# ===========================================================================


def test_durable_test_names_unchanged() -> None:
    """Every BUG-285 contract proof survives, by name."""
    src = _source()
    for name in _DURABLE_TEST_NAMES:
        assert f"def {name}" in src, f"durable contract test {name} was removed"


def test_throttle_contract_kept() -> None:
    """The original cadence contract is unchanged: a pass within the interval
    must NOT touch the directory, and a pass past it must consume."""
    body = _slice("test_drain_throttled_to_one_pass_per_interval")
    assert "assert repo.overflow_pending_count() == 1" in body, (
        "the within-interval pass must leave the file pending"
    )
    assert "assert repo.overflow_pending_count() == 0" in body, (
        "the past-interval pass must consume it"
    )


def test_none_sentinel_contract_kept() -> None:
    """The BUG-273 first-pass proof is unchanged: a None stamp means the first
    pass is always due, and it must still recover a real row."""
    body = _slice("test_first_drain_is_always_due_none_sentinel")
    assert "assert repo._last_overflow_drain is None" in body
    assert "assert repo.financial_overflow_recovered == 1" in body


def test_batch_and_writer_contracts_untouched_by_clock() -> None:
    """The batch bound, the writer cap and the poison-dead-letter contracts do
    not read a clock at all, so the clock change cannot have touched them —
    they must still call the drain with no clock argument (the first pass is
    always due under the None sentinel)."""
    for name in (
        "test_drain_batch_is_bounded",
        "test_unreplayable_overflow_is_dead_lettered_not_retried",
        "test_replay_sql_error_dead_letters_and_preserves_other_rows",
        "test_overflow_counter_semantics_are_exact",
    ):
        body = _slice(name)
        assert "_drain_financial_overflow_due(conn)" in body, (
            f"{name} must exercise the production default path (no injected clock)"
        )


def test_wiring_contract_kept() -> None:
    """The #1 failure shape — a shipped class with no caller — stays pinned."""
    body = _slice("test_drain_is_wired_into_the_audit_worker_idle_pass")
    assert "_drain_financial_overflow_due(conn)" in body


# ===========================================================================
# 4. The boundary arithmetic, proven on the real repository (behavioral)
# ===========================================================================


def test_throttle_boundary_is_the_comparison_not_the_clock(tmp_path, monkeypatch) -> None:
    """Both edges of the strict ``<`` throttle, executed against the real
    repository on a real sqlite file:

      * one tick SHORT of the interval  -> still throttled (pending stays);
      * exactly AT the interval          -> DUE (equality is not ``< interval``).

    Under the old wall-clock reads these two cases were indistinguishable — the
    outcome depended on where the host's monotonic domain happened to land.
    """
    import json

    from nexus_scalp.adapters.database.audit_repository import AuditRepository

    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'bug285_boundary.db'}")
    conn = sqlite3.connect(repo._db_path)
    d = tmp_path / "ovf"
    monkeypatch.setattr(repo, "_FINANCIAL_OVERFLOW_DIR", str(d))
    d.mkdir()
    try:
        _write = d / "overflow_20260101_000000_00000050.json"
        _write.write_text(
            json.dumps({"query": _ORDERS_SQL_BATTERY, "args": json.dumps(list(range(13)))}),
            encoding="utf-8",
        )
        interval = repo.OVERFLOW_RECOVERY_INTERVAL_SEC
        assert interval > 0.0

        # first pass under the None sentinel: always due
        repo._drain_financial_overflow_due(conn, now=0.0)
        assert repo.financial_overflow_recovered == 1
        assert repo.overflow_pending_count() == 0

        # a second file, one tick SHORT of the interval: must stay pending
        (d / "overflow_20260101_000001_00000051.json").write_text(
            json.dumps({"query": _ORDERS_SQL_BATTERY, "args": json.dumps(list(range(20, 33)))}),
            encoding="utf-8",
        )
        repo._drain_financial_overflow_due(conn, now=interval - 0.001)
        assert repo.overflow_pending_count() == 1, (
            "one tick short of the interval the pass must NOT drain (strict <)"
        )
        assert repo.financial_overflow_recovered == 1

        # exactly AT the interval: equality is not < interval, so the pass is due
        repo._drain_financial_overflow_due(conn, now=interval)
        assert repo.overflow_pending_count() == 0
        assert repo.financial_overflow_recovered == 2
    finally:
        conn.close()
        repo.close()


def test_default_argument_still_reads_the_real_clock(tmp_path, monkeypatch) -> None:
    """The seam changes nothing for production: with no ``now`` argument the
    drain runs on ``time.monotonic()`` and the first pass is still always due
    under the None sentinel (the BUG-273 invariant, executed)."""
    from nexus_scalp.adapters.database.audit_repository import AuditRepository

    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'bug285_default.db'}")
    conn = sqlite3.connect(repo._db_path)
    # the workspace-relative default overflow dir is NOT the test's; point the
    # drain at an empty temp dir so the default path is exercised deterministically
    monkeypatch.setattr(repo, "_FINANCIAL_OVERFLOW_DIR", str(tmp_path / "ovf_absent"))
    try:
        assert repo._last_overflow_drain is None
        # an empty pass on the default path stamps the sentinel and returns
        # cleanly (it must not raise and must not require an injected clock)
        repo._drain_financial_overflow_due(conn)
        assert repo._last_overflow_drain is not None
        # an immediate second call is within the interval and must be a no-op
        stamp = repo._last_overflow_drain
        repo._drain_financial_overflow_due(conn)
        assert repo._last_overflow_drain == stamp
    finally:
        conn.close()
        repo.close()


_ORDERS_SQL_BATTERY = """
            INSERT INTO audit_orders
            (ticket, order_id, symbol, action, price, stop_loss, take_profit, volume, reason, latency, execution_mode, execution_id, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(execution_id) WHERE execution_id IS NOT NULL AND execution_id != ''
            DO NOTHING
        """


# ===========================================================================
# 5. Manifest registration (a battery CI never runs pins nothing)
# ===========================================================================


def test_critical_suite_manifest_entries() -> None:
    manifest = (_REPO_ROOT / "tests/critical_suite.txt").read_text(encoding="utf-8")
    assert "tests/unit/test_bug285_overflow_drain.py" in manifest
    assert "tests/unit/test_ml_qa_013_overflow_drain_clock_determinism.py" in manifest
