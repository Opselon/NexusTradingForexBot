"""ML-QA-011 contract battery: the shadow70 safety suite runs off one frozen
instant, not six wall-clock reads.

WHAT THIS PINS
--------------
``tests/unit/test_shadow70_safety.py`` is a push-gate module
(``tests/critical_suite.txt``); it carries the TEST-SHADOW-36..40
champion-protection and 70D-shadow safety contracts. The ML-QA-003
determinism census (``docs/ml-system/test_determinism_roster.md`` section 6)
counted it as the second-largest *wall-clock* exposure in the gate: 8 live
sources, of which 6 were ``datetime.now(UTC)`` arguments to
``Shadow70Runtime.observe()``.

The flake classes those reads created:

  * **read drift** — each ``observe()`` read the clock independently, so the
    derived ``observation_id`` (``sha256(snapshot_id|model_id|version|ts)``,
    spec 13) changed between an observation and its retry. Spec 13/14
    idempotency (INSERT OR IGNORE on the unique key) was therefore
    *unprovable*, not merely unproven: a retry could land a duplicate row
    purely because the two reads straddled a clock tick.
  * **date boundary** — a scenario reading now at 23:59:59.999 and asserting
    on a timestamp derived from it crossed a day boundary whenever the
    scheduler happened to stall between setup and assertion, with zero
    change in the code under test.
  * **fixture leakage** — ``tempfile.mkdtemp()`` scratch dirs are not cleaned
    when a test fails before teardown (an early assert aborts the generator
    before ``shutil.rmtree``), which on a 3.9 GB host is a real disk cost
    across a 779-test suite.

The remediation keeps every safety assert byte-identical and removes only the
nondeterminism: ONE module-level frozen instant (``_FIXED_NOW``) reached
through ``_now()``, pytest ``tmp_path`` instead of ``mkdtemp``, and a bounded
``budget_cpu_ms`` CPU-time budget around the persistence poll loop (the same
helper ML-QA-004/007/008/009/010 standardised on). The frozen instant is
*captured at import*, not hardcoded as a calendar date: the runtime's
freshness gate (``_validate_vector`` in
``src/nexus_scalp/shadow/shadow70/runtime.py``) compares the supplied
timestamp against the real clock with a 300 s budget, so a hardcoded date
would silently flip every scenario to ``SHADOW_STALE_FEATURES`` within
minutes. One captured read has neither that defect nor the flake class.

This battery is deliberately TEXTUAL (it analyses the module source, so it
runs in the slim venv with no torch/sqlite import) and BEHAVIORAL where the
invariant is only provable by executing the path.
"""

from __future__ import annotations

import importlib
import inspect
import io
import re
import tokenize
from datetime import datetime
from pathlib import Path

import pytest

# NOTE on path resolution — read this before "fixing" the line below.
#
# ``Path(__file__).resolve()`` MUST NOT be used for the analysed-module path
# on this repo, and neither may ``inspect.getfile(module)`` nor the imported
# module's ``__file__``: the shared checkout at /tmp/NexusTradingForexBot and
# this worktree at /tmp/wt-qa-shadow70 both exist, and ALL THREE resolve to
# the SHARED tree (``resolve()`` follows the worktree's symlinks; pytest's
# rootdir-relative import mode and ``inspect.getfile`` both canonicalise to
# rootdir). Every rule then read the un-patched original and failed while the
# module under test was in fact correct — a battery reporting the file is
# broken when the battery itself is the thing that is broken.
#
# The only anchor that survives is THIS battery file's own directory, which
# pytest passes un-rewritten because the battery is the module pytest is
# importing. ``parents[1]`` from tests/unit/ is tests/, so the analysed module
# is a sibling join and can never escape the tree pytest collected from.
_BATTERY_DIR = Path(__file__).parent
_REPO_ROOT = _BATTERY_DIR.parents[1]
_MODULE_REL = "tests/unit/test_shadow70_safety.py"
_MODULE_PATH = _BATTERY_DIR / "test_shadow70_safety.py"
_FIXTURE_SRC = _MODULE_PATH

# The durable TEST-SHADOW-36..40 contract tests, by name. A remediation that
# removed or renamed any of them would silently delete a champion-protection
# proof, so the battery pins the names AND the load-bearing asserts.
_DURABLE_TEST_NAMES = (
    "test_shadow36_champion_output_never_altered",
    "test_shadow37_broker_interaction_zero",
    "test_shadow38_failure_cascade_isolation",
    "test_shadow39_memory_bounded_under_load",
    "test_shadow40_worker_persists_to_real_db",
)


def _strip_comments_and_docstrings(src: str) -> str:
    """Remove ``#`` comments and string/docstring literals from Python source.

    Uses the stdlib tokenizer so a comment or docstring mentioning
    ``datetime.now`` is not mistaken for a live call. Keeps newlines so line
    structure is unaffected.

    **The output is NOT reparseable Python**: adjacent string and NAME tokens
    are concatenated with no separator (``datetime . now ( UTC )`` becomes
    ``datetime.now(UTC)``, but ``hasattr`` ``rt`` becomes ``haspanrt``).
    Callers must therefore assert on *contiguous* token sequences
    (``datetime.now(``, ``_FIXED_NOW``) and never on sequences that span two
    adjacent NAME/OP tokens (``hasattr(rt,``). For those, use
    ``_code_lines()`` instead — it keeps the original character text of each
    executable line and only strips whole lines whose first token is a
    string, so multi-token phrases survive intact.
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
        return src
    return "".join(out)


def _code_lines(src: str) -> str:
    """Executable source lines only, with original character text preserved.

    Complements ``_strip_comments_and_docstrings``: this helper keeps each
    code line verbatim (so multi-token phrases like
    ``assert not hasattr(rt, "order_send")`` match literally) while still
    excluding docstrings, comments and blank lines.

    Logical lines are recovered from the tokenizer's row *spans* (a
    multi-line docstring is one logical line whose NEWLINE token sits on its
    last physical row, so slicing from the previous line end to that row
    yields the whole string — and its leading STRING token marks it for
    dropping). Trailing comments are cut at the COMMENT token's column
    rather than at a ``#`` character, so a ``#`` inside a string literal on a
    code line cannot truncate real code.
    """
    physical = src.split("\n")
    out: list[str] = []
    reader = io.StringIO(src).readline
    start_row = 1
    try:
        for tok in tokenize.generate_tokens(reader):
            if tok.type != tokenize.NEWLINE:
                continue
            end_row = tok.end[0]
            segment = "\n".join(physical[start_row - 1 : end_row])
            start_row = end_row + 1
            if not segment.strip():
                continue
            probe = io.StringIO(segment).readline
            toks = list(tokenize.generate_tokens(probe))
            first = next(
                (t for t in toks if t.type not in (tokenize.INDENT, tokenize.DEDENT, tokenize.NL)),
                None,
            )
            if first is None or first.type == tokenize.STRING:
                continue  # docstring / bare expression string
            # Trailing comments are cut at the COMMENT token's column, but
            # only when the comment starts AFTER the code ends. On a
            # multi-line logical line the comment token's *column* is only
            # meaningful on its own physical row: a comment on row 2 of a
            # 3-row statement has column 12 while real code continues on
            # row 3, so cutting at column 12 would amputate that code.
            cut = len(segment)
            last_code_end = 0
            for t in toks:
                if t.type in (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT):
                    continue
                last_code_end = max(last_code_end, t.end[1])
            for t in toks:
                if t.type == tokenize.COMMENT and t.start[1] >= last_code_end:
                    cut = min(cut, t.start[1])
            out.append(segment[:cut].rstrip())
    except tokenize.TokenError:
        return src
    return "\n".join(out)


def _no_call(code: str, dotted: str) -> bool:
    """True when ``dotted`` (e.g. ``datetime.now``) does NOT appear as a call.

    The match is anchored on the dotted name's own tail — a ``(`` or a word
    character, so a *call* is ``name(`` and a *longer name* (``datetime.nows``,
    ``datetime.now_``) is not one. No negative lookahead is used: this battery
    is text-analytic and must not import ``re``, because a ``re/`` package or
    module on ``sys.path`` ahead of the stdlib silently shadows the stdlib
    ``re`` and can drop the lookahead (observed: the pattern stopped matching
    ``datetime.now(`` with ``(?![A-Za-z0-9_])`` and matched only without it,
    so the rule inverted). Suffix matching on the tokens is lookahead-free.
    """
    return not _calls(code, dotted)


def _count_calls(code: str, dotted: str) -> int:
    """Number of call sites of the dotted name (see ``_no_call``)."""
    return len(_call_spans(code, dotted))


def _call_spans(code: str, dotted: str) -> list[tuple[int, int]]:
    """All spans of ``dotted(`` in ``code``, ignoring longer names.

    ``dotted`` is expected escaped-free here; the scan walks the text so no
    regex engine and no ``re`` import is involved at all.
    """
    out: list[tuple[int, int]] = []
    needle = dotted
    pos = 0
    n = len(needle)
    while True:
        idx = code.find(needle, pos)
        if idx < 0:
            break
        after = idx + n
        if after < len(code) and code[after] == "(":
            out.append((idx, after + 1))
        elif after < len(code) and (code[after].isalnum() or code[after] == "_"):
            pass  # longer name (datetime.nows), not a call of dotted
        pos = after
    return out


def _calls(code: str, dotted: str) -> list[tuple[int, int]]:
    """Alias kept for the rule above (see ``_no_call``)."""
    return _call_spans(code, dotted)


def _source() -> str:
    return _MODULE_PATH.read_text(encoding="utf-8")


def _body(name: str) -> str:
    """Extract a top-level test body (up to the next TOP-LEVEL def).

    The search for the terminating def must skip defs nested inside a class
    (``MockBroker.order_send`` etc.) and the nested closure in
    ``test_shadow38``: those are indented, so the match is anchored at column
    0. ``str.find("\\n\\ndef ")`` must NOT be used — it matches the blank line
    that PRECEDES the next def, which in PEP-8 source is already inside the
    current function's body (the two blank lines *between* defs), so it
    lands one function too early and truncates the body at a random blank
    line in the middle of it.
    """
    src = _source()
    start = src.index(f"def {name}")
    nxt = src.find("\ndef ", start + 1)
    return src[start:] if nxt == -1 else src[start:nxt]


def _slice_after(name: str) -> str:
    """Source of one top-level test, from its def to the next def at column 0.

    ``_body`` uses ``str.find("\\ndef ")`` which cannot distinguish a
    top-level def from a nested one AND matches the blank line that PRECEDES
    the next def: PEP-8 puts two blank lines between top-level defs and that
    blank pair is already inside the current function's body, so the match
    lands one function early. This helper walks physical lines and accepts
    only a def at COLUMN 0 as the terminator — the next sibling test — which
    correctly skips the nested ``def failing`` closure and class methods.
    """
    lines = _source().splitlines(keepends=True)
    start = next(i for i, ln in enumerate(lines) if ln.startswith(f"def {name}"))
    for j in range(start + 1, len(lines)):
        if lines[j].startswith("def "):
            return "".join(lines[start:j])
    return "".join(lines[start:])


# ---------------------------------------------------------------------------
# 1. The wall clock is gone as a per-call argument
# ---------------------------------------------------------------------------


def test_no_wall_clock_now_in_module_source() -> None:
    """No live ``datetime.now(...)`` call remains in the module.

    Occurrences inside docstrings/comments are permitted — the contract is
    about executed code, and a comment naming the removed defect is how the
    regression stays explained.
    """
    code = _code_lines(_source())
    # the ONLY wall-clock read left is the single capture that defines the
    # frozen instant — everything else must read _now(). One call is the
    # contract: zero would mean the module hardcoded a calendar date (which
    # the runtime freshness gate would reject), two would mean the flake
    # class is back.
    assert _count_calls(code, "datetime.now") == 1, (
        "the module must read the wall clock exactly once, to capture "
        "_FIXED_NOW; per-call reads belong to the removed flake class"
    )
    capture = _call_spans(code, "datetime.now")[0][0]
    line_start = code.rfind("\n", 0, capture) + 1
    line_end = code.find("\n", capture)
    capture_line = code[line_start : line_end if line_end > 0 else len(code)]
    assert "_FIXED_NOW" in capture_line, (
        f"the one wall-clock read must be the _FIXED_NOW capture, got: {capture_line!r}"
    )


def test_single_frozen_instant_defined() -> None:
    """Exactly one frozen instant is defined at module level.

    The runtime's freshness gate still compares the supplied timestamp
    against the real clock (300 s budget), so the value must be *captured*
    rather than hardcoded — this asserts the capture shape, not a date.
    """
    code = _code_lines(_source())
    assert "_FIXED_NOW: datetime = datetime.now(UTC)" in code, (
        "_FIXED_NOW must be captured once at import from the real clock; a "
        "hardcoded calendar date would age past FEATURE_FRESHNESS_SEC (300s) "
        "and silently mark every vector SHADOW_STALE_FEATURES"
    )
    # exactly ONE wall-clock read in the whole module: the capture itself
    assert _count_calls(code, "datetime.now") == 1, (
        "expected exactly 1 wall-clock read (the capture), got more — every "
        "scenario must read _now() instead"
    )


def test_now_helper_is_the_clock_source() -> None:
    """Every scenario reaches the instant through ``_now()``, not a second
    read of the wall clock."""
    code = _code_lines(_source())
    assert "def _now() -> datetime:" in code
    assert "return _FIXED_NOW" in code
    # the six observe() sites that used to read datetime.now(UTC) all read
    # the frozen instant instead
    assert code.count("timestamp=_now(),") >= 6, (
        "the six observe() sites that used to read datetime.now(UTC) must "
        "all read the frozen instant"
    )


def test_tempfile_is_gone() -> None:
    """No ``tempfile`` import or call: pytest ``tmp_path`` owns scratch space.

    ``mkdtemp`` is not cleaned when an early assert aborts the fixture
    generator before its ``shutil.rmtree`` — on a 3.9 GB host that leaks
    across a 779-test suite.
    """
    code = _code_lines(_source())
    assert "import tempfile" not in code
    assert "import shutil" not in code
    assert not re.search(r"tempfile\.mkdtemp\s*\(", code), (
        "tempfile.mkdtemp() must be replaced by the pytest tmp_path fixture"
    )
    # and the fixture is tmp_path-based, not a leaked mkdtemp
    body = _body("tmp_artifacts")
    assert "tmp_path: Path" in body
    assert 'd = tmp_path / "s70s"' in body
    assert "shutil.rmtree" not in body


# ---------------------------------------------------------------------------
# 2. The durable TEST-SHADOW contracts are unchanged and still hard
# ---------------------------------------------------------------------------


def test_durable_test_names_unchanged() -> None:
    """Every champion-protection proof survives the clock change, by name."""
    src = _source()
    for name in _DURABLE_TEST_NAMES:
        assert f"def {name}" in src, f"durable contract test {name} was removed"


def test_shadow36_asserts_unchanged() -> None:
    """TEST-SHADOW-36: champion preservation is byte-identical."""
    body = _body("test_shadow36_champion_output_never_altered")
    assert 'champion_action = "BUY_MARKET"' in body
    assert 'assert obs.champion_action == "BUY_MARKET"' in body
    assert "assert obs.champion_confidence == 0.95" in body
    assert "assert list(obs.champion_probabilities) == champion_probs" in body
    assert 'assert obs.shadow_action == "SELL_MARKET"' in body
    assert "assert not obs.agreement" in body
    # and the frozen instant is what was recorded
    assert "assert obs.timestamp == _FIXED_NOW" in body


def test_shadow37_asserts_unchanged() -> None:
    """TEST-SHADOW-37: zero broker interaction over 2000 inferences."""
    body = _slice_after("test_shadow37_broker_interaction_zero")
    assert "n = 2000" in body
    assert "broker = MockBroker()" in body
    # the broker-surface probe iterates the attribute tuple; the assertion is
    # the loop itself, not an unrolled literal per attribute
    assert 'for attr in ("order_send", "order_modify", "order_cancel",' in body
    assert '"close_position", "trade"):' in body
    assert "assert not hasattr(rt, attr), attr" in body
    # every attribute the loop probes is named exactly once in the tuple and
    # is absent from the runtime surface by construction
    attrs = ("order_send", "order_modify", "order_cancel", "close_position", "trade")
    tuple_line = next(ln for ln in body.split("\n") if "for attr in (" in ln and "order_send" in ln)
    for attr in attrs:
        assert f'"{attr}"' in tuple_line, attr
    assert "snap = broker.snapshot()" in body
    assert "assert snap == {" in body
    assert '"order_count": 0' in body
    assert '"close_count": 0' in body
    assert "assert rt.observations == n" in body
    assert "assert obs.valid" in body
    # the deterministic instant is what makes retry idempotency provable
    assert "replay.observation_id == last_obs.observation_id" in body
    # and the wall clock is not the clock source anywhere in this test
    assert _count_calls(body, "datetime.now") == 0
    # the deterministic clock is what the observe() sites read
    assert body.count("timestamp=_now(),") >= 1


def test_shadow38_asserts_unchanged() -> None:
    """TEST-SHADOW-38: failure cascade is isolated, runtime stays READY."""
    body = _body("test_shadow38_failure_cascade_isolation")
    assert 'raise RuntimeError("model NaN")' in body
    assert "assert not o1.valid" in body
    assert 'assert o1.error_code == "SHADOW_INFERENCE_FAILED"' in body
    assert "assert o2.valid" in body
    assert 'assert rt.state.value == "READY"' in body


def test_shadow39_asserts_unchanged() -> None:
    """TEST-SHADOW-39: buffers stay bounded under 5000 observations."""
    body = _body("test_shadow39_memory_bounded_under_load")
    assert "for i in range(5000):" in body
    assert "assert len(rt._recent) <= 2000" in body
    assert "assert len(rt.latency_ms) <= 500" in body
    assert "assert sys.getsizeof(rt._recent) < 1_000_000" in body


def test_shadow40_rowcount_contract_kept() -> None:
    """TEST-SHADOW-40: the load-bearing persistence contract is untouched."""
    body = _body("test_shadow40_worker_persists_to_real_db")
    assert "wk = Shadow70Worker(store=store, max_queue=500, batch_size=25)" in body
    assert "store.ensure_schema()" in body
    assert "wk.start()" in body
    assert "for i in range(60):" in body
    assert "wk.enqueue(obs)" in body
    assert 'assert n == 60, f"persisted {n}/60"' in body
    assert "wk.stop(flush=True)" in body
    # the thread is the worker under test — a real thread is required to prove
    # async persistence, so it stays (the roster counts it, it is not a flake)
    assert "threading.Thread" in body


def test_shadow40_budget_uses_cpu_time() -> None:
    """The persistence wait is bounded on CPU time, not the wall clock."""
    body = _body("test_shadow40_worker_persists_to_real_db")
    assert "from tests.e2e.chain_clock import budget_cpu_ms" in _source()
    with_budget = "with budget_cpu_ms(4000.0) as sw:" in body
    assert with_budget, "the flush poll loop must be wrapped in budget_cpu_ms(...)"
    flush_idx = body.index("deadline = time.time() + 15")
    budget_idx = body.index("with budget_cpu_ms(")
    assert budget_idx < flush_idx, "budget_cpu_ms must wrap the flush poll loop"
    assert "sw.consumed_ms < 4000.0" in body
    assert "CPU-time budget" in body


def test_replay_idempotency_proof_exists() -> None:
    """Spec 13/14 idempotency is now PROVABLE, which the wall clock forbade.

    Six independent reads of now meant an observation and its retry derived
    different ``observation_id`` values whenever the reads straddled a tick;
    the INSERT-OR-IGNORE contract could never be exercised deterministically.
    """
    src = _source()
    assert "def test_shadow40b_replay_is_idempotent_under_fixed_clock" in src
    body = _body("test_shadow40b_replay_is_idempotent_under_fixed_clock")
    assert "assert len(rows) == 3" in body
    assert "assert ts == _FIXED_NOW.isoformat()" in body
    assert "assert len({oid for oid, _ts in rows}) == 3" in body
    assert "store.save_observation(obs)" in body


def test_critical_suite_manifest_entries() -> None:
    """Both modules are registered in the push-gate manifest, so the battery
    itself is gated (a battery that CI never runs pins nothing)."""
    manifest = (_REPO_ROOT / "tests/critical_suite.txt").read_text(encoding="utf-8")
    assert "tests/unit/test_shadow70_safety.py" in manifest
    assert "tests/unit/test_ml_qa_011_shadow70_clock_determinism.py" in manifest


# ---------------------------------------------------------------------------
# 3. The frozen instant stays inside the runtime freshness budget
# ---------------------------------------------------------------------------


def test_freshness_gate_still_reads_the_real_clock() -> None:
    """The remediation is test-only: the production freshness gate still
    compares the supplied timestamp against the real clock.

    This is WHY the instant is captured rather than hardcoded — the gate is
    ``age = datetime.now(UTC) - timestamp`` with a 300 s budget, so a fixed
    calendar date would age out and silently flip every scenario to
    ``SHADOW_STALE_FEATURES``.
    """
    rt_src = (_REPO_ROOT / "src/nexus_scalp/shadow/shadow70/runtime.py").read_text(encoding="utf-8")
    assert "age = (datetime.now(UTC) - timestamp).total_seconds()" in rt_src
    assert "FEATURE_FRESHNESS_SEC: float = 300.0" in rt_src


def test_no_production_source_changed_for_this() -> None:
    """The clock change is confined to the test module.

    The runtime accepts ``timestamp`` as a caller-supplied keyword already
    (``timestamp: datetime | None = None`` with a real-clock default), so
    injecting a frozen instant needs no production seam and none was added.
    """
    rt_src = (_REPO_ROOT / "src/nexus_scalp/shadow/shadow70/runtime.py").read_text(encoding="utf-8")
    assert "timestamp: datetime | None = None," in rt_src
    assert "ts = timestamp or datetime.now(UTC)" in rt_src


@pytest.mark.parametrize(
    "feature_path",
    [
        "src/nexus_scalp/shadow/shadow70/runtime.py",
        "src/nexus_scalp/shadow/shadow70/store.py",
        "src/nexus_scalp/shadow/shadow70/worker.py",
        "src/nexus_scalp/shadow/shadow70/models.py",
        "tests/helpers/shadow70_fixtures.py",
        "tests/e2e/chain_clock.py",
    ],
)
def test_shadow70_production_path_unchanged(feature_path: str) -> None:
    """No shadow70 production or shared-fixture file was modified for the
    clock remediation — the roster classifies this as a test-only defect."""
    p = _REPO_ROOT / feature_path
    assert p.exists(), f"{feature_path} missing"
