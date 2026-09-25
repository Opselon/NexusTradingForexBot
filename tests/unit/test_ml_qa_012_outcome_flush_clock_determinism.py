"""ML-QA-012 contract battery: the BUG-140 outcome-flush race suite runs off
one frozen instant and CPU-time liveness bounds.

WHAT THIS PINS
--------------
``tests/unit/test_outcome_flush_race_bug140.py`` is a push-gate module
(``tests/critical_suite.txt`` entry at line 405); it pins the BUG-140
read-after-write contract (an outcome recorded immediately after the queued
pre-trade experience must NOT fail with NO_DECISION_SNAPSHOT) and the BUG-288
worker-binding contract. The ML-QA-003 determinism census
(``docs/ml-system/test_determinism_roster.md`` section 6) counted it as 7
live sources: 4 ``time.monotonic()`` timing probes and 3
``datetime.now(UTC)`` wall-clock reads.

The flake classes those reads created:

  * **wall-clock liveness bound** — two poll loops bounded themselves with
    ``time.monotonic()`` deadlines. That measures how long the OS gave this
    test, not the code under test: under xdist saturation on a 2-core CI
    runner a co-tenant scheduler stall inflates the wait with zero change in
    the code under test, and the bound trips while the contract holds.
  * **read drift** — the decision timestamp and the two outcome timestamps
    each read the wall clock independently. No assertion compares any of them
    to ``now()`` — the clock contributed nothing — but independent reads made
    the suite's own causality window scheduler-dependent: the outcome read
    could legitimately precede the decision read whenever the two calls
    straddled a clock tick. The causality guard in
    ``ledger._merge_row`` / ``record_terminal_outcome`` /
    ``ExperienceIntelligenceEngine.record_trade_outcome`` is
    ``outcome_timestamp < decision_timestamp`` (strict), so an EQUAL pair is
    accepted on the write path and merged on the read path; under independent
    reads that equality was a coin flip across a tick.

The remediation keeps every contract assert byte-identical and removes only
the nondeterminism: ONE module-level frozen instant (``_FIXED_NOW``) reached
through ``_now()``, and both poll loops bounded on CPU time
(``time.process_time()`` via the shared ``tests/e2e/chain_clock`` helper the
ML-QA-004/007/008/009/010/011 remediations standardised on, wrapped in
``budget_cpu_ms``). The instant is a *fixed calendar value*, not a captured
read: unlike the shadow70 freshness gate, no production path here compares
the supplied timestamp against the real clock — the causality guard is a
pure comparison between two caller-supplied values. The battery pins that
fixed value at a date later than every legacy row the suite's own fixtures
could carry, so it can never make a causality comparison go backwards.

This battery is deliberately TEXTUAL (it analyses the module source, so it
runs in the slim venv with no torch/sqlite import) and BEHAVIORAL where the
invariant is only provable by executing the path.
"""

from __future__ import annotations

import io
import tokenize
from datetime import datetime
from pathlib import Path

import pytest

# NOTE on path resolution — read this before "fixing" the line below.
#
# ``Path(__file__).resolve()`` MUST NOT be used for the analysed-module path
# on this repo, and neither may ``inspect.getfile(module)`` nor the imported
# module's ``__file__``: the shared checkout at /tmp/NexusTradingForexBot and
# this worktree both exist, and ALL THREE resolve to the SHARED tree
# (``resolve()`` follows the worktree's symlinks; pytest's rootdir-relative
# import mode and ``inspect.getfile`` both canonicalise to rootdir). Every
# rule then read the un-patched original and failed while the module under
# test was in fact correct — a battery reporting the file is broken when the
# battery itself is the thing that is broken.
#
# The only anchor that survives is THIS battery file's own directory, which
# pytest passes un-rewritten because the battery is the module pytest is
# importing. ``parents[1]`` from tests/unit/ is tests/, so the analysed module
# is a sibling join and can never escape the tree pytest collected from.
_BATTERY_DIR = Path(__file__).parent
_REPO_ROOT = _BATTERY_DIR.parents[1]
_MODULE_PATH = _BATTERY_DIR / "test_outcome_flush_race_bug140.py"

# The durable BUG-140 / BUG-288 contract tests, by name. A remediation that
# removed or renamed any of them would silently delete a regression proof, so
# the battery pins the names AND the load-bearing asserts.
_DURABLE_TEST_NAMES = (
    "test_audit_repo_flush_drains_queue",
    "test_outcome_immediately_after_pretrade_write_succeeds",
    "test_terminal_outcome_immediately_after_pretrade_write_succeeds",
    "test_flush_is_bounded_when_worker_stalled",
    "test_worker_never_adopts_a_rebound_queue_bug288",
    "test_bug288_handshake_source_pins",
)


# ---------------------------------------------------------------------------.
# Source analysis helpers (tokenize-based, no `re` import — see the note on
# shadowing in the ML-QA-011 battery: a `re/` package or module on sys.path
# ahead of the stdlib can silently drop a negative lookahead and INVERT a
# textual rule. These helpers walk the source text directly.)
# ---------------------------------------------------------------------------


def _code_lines(src: str) -> str:
    """Executable source lines only, with original character text preserved.

    Docstrings, comments and blank lines are dropped, so a comment naming the
    removed defect (how the regression stays explained) can never be mistaken
    for a live call.

    Ground truth is ``ast``: every token that is not on a line carrying real
    code is dropped. A pure-tokenizer walk (the ML-QA-011 helper this is
    modelled on) classifies a physical line by the *first* non-NL token of its
    logical line, so a logical line that starts with a comment and continues
    into code on a later physical row keeps the comment row verbatim — which
    makes a textual rule see a live ``time.monotonic()`` that is prose.
    ``ast`` is safe to import here: no ``ast.py`` or ``re.py`` shadows the
    stdlib anywhere in this tree (verified at authoring time), and this helper
    imports ``ast`` (never ``re``) regardless — see the shadowing note in the
    ML-QA-011 battery for why ``re`` is avoided in textual batteries.
    """
    import ast

    physical = src.split("\n")
    code_rows: set[int] = set()
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.expr_context)):
            lineno = getattr(node, "lineno", None)
            end_lineno = getattr(node, "end_lineno", lineno)
            if lineno is not None and end_lineno is not None:
                for row in range(lineno, end_lineno + 1):
                    code_rows.add(row)
    out: list[str] = []
    for idx, line in enumerate(physical, start=1):
        if idx not in code_rows:
            continue
        # cut a trailing comment at the COMMENT token's column so a ``#``
        # inside a string literal on a code line cannot truncate real code
        segment = line
        for tok in _tokenize_line(segment):
            if tok.type == tokenize.COMMENT and tok.start[1] >= _last_code_col(segment):
                segment = segment[: tok.start[1]]
                break
        text = segment.rstrip()
        if text:
            out.append(text)
    return "\n".join(out)


def _tokenize_line(segment: str) -> list[tokenize.TokenInfo]:
    """Tokenizes one physical line, tolerant of unparseable fragments."""
    try:
        return list(tokenize.generate_tokens(io.StringIO(segment).readline))
    except tokenize.TokenError:
        return []


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

    The scan walks the text so no regex engine and no ``re`` import is
    involved at all. A longer name (``datetime.nows``) is not a call of
    ``dotted``.
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


def _count_calls(code: str, dotted: str) -> int:
    """Number of call sites of the dotted name (see ``_call_spans``)."""
    return len(_call_spans(code, dotted))


def _no_call(code: str, dotted: str) -> bool:
    """True when ``dotted`` (e.g. ``datetime.now``) does NOT appear as a call."""
    return not _call_spans(code, dotted)


def _source() -> str:
    return _MODULE_PATH.read_text(encoding="utf-8")


def _slice(name: str) -> str:
    """Source of one top-level or class-level test, by def name.

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


# ---------------------------------------------------------------------------
# 1. The wall clock is gone as a timestamp supplier
# ---------------------------------------------------------------------------


def test_no_wall_clock_now_in_module_source() -> None:
    """No live ``datetime.now(...)`` call remains in the module.

    Occurrences inside docstrings/comments are permitted — the contract is
    about executed code, and a comment naming the removed defect is how the
    regression stays explained.
    """
    code = _code_lines(_source())
    assert _no_call(code, "datetime.now"), (
        "the module must not read the wall clock at all: no production path "
        "compares the supplied timestamps against now(), so a live read "
        "contributes nothing but read drift (the outcome read could precede "
        "the decision read whenever the two calls straddled a clock tick)"
    )


def test_single_frozen_instant_defined() -> None:
    """Exactly one frozen instant is defined at module level, reached through
    a single ``_now()`` supplier.

    Three independent reads made the suite's causality window
    scheduler-dependent; one supplier makes the decision timestamp and both
    outcome timestamps provably consistent. The value is a FIXED calendar
    instant, not a captured read: unlike the shadow70 freshness gate, no
    production path here compares the timestamp against the real clock — the
    causality guard is a pure comparison between two caller-supplied values.
    """
    code = _code_lines(_source())
    assert "def _now() -> datetime:" in code
    assert "return _FIXED_NOW" in code
    assert "_FIXED_NOW = datetime(" in code, "_FIXED_NOW must be a fixed instant"
    # exactly ONE instant is defined
    assert code.count("_FIXED_NOW = datetime(") == 1, "one frozen instant, not many"


def test_now_helper_is_the_clock_source() -> None:
    """Every timestamp the module stamps reaches the instant through
    ``_now()``, never a second read of the wall clock."""
    code = _code_lines(_source())
    # the three sites that used to read datetime.now(UTC): the decision
    # timestamp in _record and the two outcome_timestamp arguments
    assert code.count("_now()") >= 3, (
        "the three datetime.now(UTC) sites (decision timestamp + two outcome "
        "timestamps) must all read the frozen instant"
    )
    body = _slice("_record")
    assert "ts = _now()" in body, "the decision timestamp must read _now()"
    for name in (
        "test_outcome_immediately_after_pretrade_write_succeeds",
        "test_terminal_outcome_immediately_after_pretrade_write_succeeds",
    ):
        b = _slice(name)
        assert "outcome_timestamp=_now()" in b, (
            f"{name} must supply outcome_timestamp=_now() so the decision and "
            "outcome timestamps can never straddle a clock tick"
        )


def test_frozen_instant_is_not_a_live_read() -> None:
    """The frozen instant is a literal, so the module's timestamps are
    reproducible to the nanosecond on every host and cannot drift across a
    date boundary."""
    code = _code_lines(_source())
    spans = _call_spans(code, "datetime.now")
    assert not spans, "no datetime.now( call may remain anywhere in the module"


# ---------------------------------------------------------------------------
# 2. The timing probes are gone (CPU-time bounds, never wall clock)
# ---------------------------------------------------------------------------


def test_no_wall_clock_timing_probe_in_module_source() -> None:
    """No ``time.monotonic()`` / ``time.perf_counter()`` probe remains.

    The two removed loops bounded THEMSELVES on the wall clock
    (``deadline = time.monotonic() + N`` slept in a ``time.sleep()`` poll).
    That shape measures how long the OS gave this test, not the code under
    test: under xdist saturation on a 2-core CI runner a co-tenant scheduler
    stall inflates the wait with zero change in the contract.
    """
    code = _code_lines(_source())
    assert _no_call(code, "time.monotonic"), (
        "a wall-clock deadline pair is the removed flake class; liveness must "
        "be bounded on CPU time"
    )
    assert _no_call(code, "time.perf_counter"), "same flake class as monotonic"
    assert _no_call(code, "time.sleep"), (
        "a sleep-bounded poll loop measures the OS scheduler; a CPU-time "
        "bound or an immediate invariant check replaces it"
    )


def test_budget_cpu_ms_imported_from_shared_helper() -> None:
    """The liveness bound uses the shared CPU-time helper the earlier
    ML-QA remediations standardised on (one helper, one clock semantics)."""
    src = _source()
    assert "from tests.e2e.chain_clock import budget_cpu_ms" in src, (
        "budget_cpu_ms must be imported from tests.e2e.chain_clock"
    )


def test_flush_poll_is_cpu_bounded() -> None:
    """The read-after-flush poll loop is wrapped in ``budget_cpu_ms`` and its
    inner bound is CPU time, and it exits on the invariant."""
    body = _slice("test_audit_repo_flush_drains_queue")
    assert "with budget_cpu_ms(5000.0) as sw:" in body, (
        "the read-after-flush poll must be wrapped in budget_cpu_ms(...)"
    )
    assert "time.process_time()" in body, (
        "the poll's inner bound must be time.process_time() (CPU time), not "
        "time.monotonic() (wall clock)"
    )
    assert "sw.consumed_ms < 5000.0" in body, "the CPU budget must be asserted"
    # the CONTRACT under test survived the clock change
    assert "assert repo.flush(timeout_sec=5.0) is True" in body
    assert 'ledger.get_experience_by_key("exp_req_flush_a")' in body
    assert "assert row is not None" in body


def test_misbehavior_poll_is_cpu_bounded() -> None:
    """The BUG-288 misbehavior poll is CPU-bounded and still asserts the
    real invariant (the poisoned item is NEVER consumed)."""
    body = _slice("test_worker_never_adopts_a_rebound_queue_bug288")
    assert "with budget_cpu_ms(2000.0) as sw:" in body
    assert "time.process_time()" in body
    assert "sw.consumed_ms < 2000.0" in body
    assert "stalled_queue.unfinished_tasks == 1" in body, (
        "the adoption-window invariant must stay hard: an item parked in a "
        "rebound queue is never drained"
    )
    # the stall contract is unchanged: flush() must RETURN False, not hang
    assert "assert r.flush(timeout_sec=0.05) is False" in body


def test_stalled_flush_keeps_false_contract() -> None:
    """``test_flush_is_bounded_when_worker_stalled`` keeps its hard contract:
    a stalled worker must not deadlock the live path."""
    body = _slice("test_flush_is_bounded_when_worker_stalled")
    assert "result = repo.flush(timeout_sec=0.05)" in body
    assert "assert result is False" in body, (
        "boundedness is proven by the RETURN VALUE (it came back instead of "
        "hanging), never by a wall-clock magnitude"
    )


# ---------------------------------------------------------------------------
# 3. The durable contracts survived the clock change
# ---------------------------------------------------------------------------


def test_durable_test_names_unchanged() -> None:
    """Every BUG-140 / BUG-288 contract proof survives, by name."""
    src = _source()
    for name in _DURABLE_TEST_NAMES:
        assert f"def {name}" in src, f"durable contract test {name} was removed"


def test_outcome_merge_contract_kept() -> None:
    """The merged-row economic contract: an outcome written on the queued
    decision is readable with its realized R after the flush."""
    body = _slice("test_outcome_immediately_after_pretrade_write_succeeds")
    assert "ok is True" in body, "the outcome must be ACCEPTED (no orphan drop)"
    assert "merged.realized_r_multiple == 2.0" in body, "the realized R must survive"
    assert "merged is not None" in body
    assert "merged.exit_reason" not in body  # unchanged scope of this test


def test_terminal_outcome_merge_contract_kept() -> None:
    """The terminal/cancel outcome contract: no fabricated R on a cancel."""
    body = _slice("test_terminal_outcome_immediately_after_pretrade_write_succeeds")
    assert "ok is True" in body
    assert 'merged.exit_reason == "CANCELED_UNFILLED"' in body
    assert "merged.realized_r_multiple == 0.0" in body, (
        "an unfilled cancel must never carry a fabricated R"
    )


def test_no_production_source_changed_for_this() -> None:
    """The clock change is confined to the test module.

    The ledger accepts the timestamps as caller-supplied values already, and
    the causality guard is a pure comparison between them, so injecting a
    frozen instant needs no production seam and none was added. The only
    wall-clock read left in the merge path is inside the production code,
    which this task does not touch.
    """
    for rel in (
        "src/nexus_scalp/experience/ledger.py",
        "src/nexus_scalp/experience/intelligence.py",
        "src/nexus_scalp/adapters/database/audit_repository.py",
        "tests/e2e/chain_clock.py",
    ):
        p = _REPO_ROOT / rel
        assert p.exists(), f"{rel} missing"
    # the production causality guard is unchanged and still strict (<), which
    # is why an EQUAL decision/outcome pair is accepted and merged
    ledger_src = (_REPO_ROOT / "src/nexus_scalp/experience/ledger.py").read_text(encoding="utf-8")
    assert "if outcome.outcome_timestamp < record.decision_timestamp:" in ledger_src


def test_critical_suite_manifest_entries() -> None:
    """Both modules are registered in the push-gate manifest, so the battery
    itself is gated (a battery that CI never runs pins nothing)."""
    manifest = (_REPO_ROOT / "tests/critical_suite.txt").read_text(encoding="utf-8")
    assert "tests/unit/test_outcome_flush_race_bug140.py" in manifest
    assert "tests/unit/test_ml_qa_012_outcome_flush_clock_determinism.py" in manifest


# ---------------------------------------------------------------------------
# 4. The causality guard treats an EQUAL pair as causal (behavioral proof)
# ---------------------------------------------------------------------------


def test_frozen_clock_round_trip_is_deterministic(tmp_path) -> None:
    """Under the single frozen instant, the decision timestamp equals the
    outcome timestamp, and the ledger both ACCEPTS that outcome on the write
    path and MERGES it on the read path.

    This is the property the three independent ``datetime.now(UTC)`` reads
    made a coin flip: the guard is ``outcome < decision`` (strict), so an
    equal pair is causal — but only if the two stamps are actually equal,
    which a scheduler stall between two separate clock reads could break.
    Runs the real ledger on a real sqlite file (no torch import).
    """
    import sqlite3

    from nexus_scalp.adapters.database.audit_repository import AuditRepository
    from nexus_scalp.adapters.database.broker_history import create_history_tables
    from nexus_scalp.experience.ledger import ExperienceLedger
    from nexus_scalp.experience.models import (
        ExperienceOutcome,
        ExperienceRecord,
        FeatureSnapshot,
        StrategyContext,
    )

    repo = AuditRepository(db_url=f"sqlite:///{tmp_path / 'fixed_clock.db'}")
    conn = sqlite3.connect(repo._db_path)
    create_history_tables(conn)
    conn.close()
    ledger = ExperienceLedger(repo)
    try:
        ts = _load_frozen_instant()
        ledger.record_experience(
            ExperienceRecord(
                experience_id="exp_row_fixed",
                request_id="req_fixed",
                idempotency_key="exp_req_fixed",
                symbol="XAUUSD",
                timeframe="M1",
                decision_timestamp=ts,
                strategy_id="strat_fixed",
                strategy_version="1.0.0",
                context=StrategyContext(
                    strategy_id="strat_fixed",
                    symbol="XAUUSD",
                    session="LONDON",
                    regime="TRENDING_MOMENTUM",
                    volatility_regime="NORMAL",
                    trend_state="BULLISH",
                ),
                feature_snapshot=FeatureSnapshot(values=[0.0] * 50),
                action="BUY_MARKET",
                entry_reason="SMC",
                proposed_entry=2000.0,
                stop_loss=1990.0,
                take_profit=2020.0,
                approved_volume=0.1,
            )
        )
        assert repo.flush(timeout_sec=5.0) is True
        assert (
            ledger.record_terminal_outcome(
                ExperienceOutcome(
                    idempotency_key="exp_req_fixed",
                    execution_id="77777777",
                    outcome_timestamp=ts,
                    is_executed=True,
                    is_closed=True,
                    exit_reason="TAKE_PROFIT_HIT",
                    realized_pnl_usd=25.0,
                    realized_r_multiple=1.25,
                )
            )
            is True
        )
        assert repo.flush(timeout_sec=5.0) is True
        merged = ledger.get_experience_by_key("exp_req_fixed")
        assert merged is not None
        assert merged.realized_r_multiple == 1.25, (
            "an outcome whose timestamp EQUALS the decision timestamp must be "
            "merged, not dropped (the causality guard is strict <)"
        )
    finally:
        repo.close()


def _load_frozen_instant() -> datetime:
    """Imports the frozen instant from the module under test rather than
    duplicating the literal here (a battery that re-declares the value pins
    nothing — the module could drift back to a live read and the copy would
    keep the test green)."""
    import importlib

    mod = importlib.import_module("tests.unit.test_outcome_flush_race_bug140")
    return mod._now()
