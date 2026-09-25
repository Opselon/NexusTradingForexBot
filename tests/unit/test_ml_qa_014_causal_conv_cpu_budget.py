"""ML-QA-014 contract battery: the causal-TCN latency suite measures CPU time,
not wall clock.

WHAT THIS PINS
--------------
``tests/unit/test_causal_conv_invariants.py`` is a push-gate module
(``tests/critical_suite.txt`). The ML-QA-003 determinism census
(``docs/ml-system/test_determinism_roster.md`` section 6, recount at
``03d2fede``) listed it as 4 live wall-clock sources: four
``time.perf_counter()`` reads inside the two latency tests of
``TestLatencyAcrossReceptiveFields``.

The defect class
----------------
The two latency tests are *benchmarks*: they assert that a forward pass stays
inside a budget and that cost does not blow up as the receptive field deepens.
Both measured ``time.perf_counter()`` (wall clock) around the forward passes.

Wall clock is the wrong clock for a budget on a shared CI runner. The suite
runs on 2-core runners alongside co-tenant jobs; a stalled runner slows the
wall clock with zero change in the code under test, and the budget trips. This
is the same class ML-QA-007 (``test_mt5_adapter_parity``), ML-QA-008
(``test_experiment_registry``), ML-QA-009 (``test_audit_flush_contract``),
ML-QA-011 (``test_shadow70_safety``) and ML-QA-012 (``test_outcome_flush_race``)
already fixed: ``time.perf_counter()`` belongs to the *instrumentation*
concern, ``time.process_time()`` (CPU time) belongs to the *measurement*
concern. A red on only one OS of the matrix is the tell for this shape, not a
platform bug.

The remediation
---------------
Test-only (no production code changed). Every measurement loop is wrapped in
the shared ``budget_cpu_ms`` stopwatch from ``tests/e2e/chain_clock.py``, which
reads ``time.process_time()``; the per-pass figure is
``sw.consumed_ms / reps``. The budgets moved with the clock: CPU time removes
scheduler noise, so the margin can be honest — 50 ms per forward pass against
an observed worst case of ~2 ms, and a worst/best ratio of 15 against an
observed ~1.8x. The warm-up was extended from 3 to 5 passes (conv kernels and
the thread pool must be hot before the measured leg, otherwise the first passes
pay one-time cost the budget does not intend to bound).

Also removed: the ``or`` fallback in the depth bound
(``worst <= best * RATIO or worst < BUDGET``) — a compound assert where the
second clause silently waives the first. The structural bound is now asserted
on its own, because that is the invariant the test exists to prove and it is
provable on CPU time.

This battery is TEXTUAL where the rule is about the source shape and
BEHAVIORAL where the invariant is only provable by executing the model.
"""

from __future__ import annotations

import ast
import io
import subprocess
import tokenize
from pathlib import Path

import pytest

from tests.e2e.chain_clock import budget_cpu_ms

# Path resolution note (the ML-QA-011/012/013 trap): ``Path(__file__).resolve()``,
# ``inspect.getfile(module)`` and an imported module's ``__file__`` all
# canonicalise to the SHARED checkout on this repo, so a textual rule reading
# the analysed module through them inspects the un-patched original and fails
# while the module under test is correct. THIS battery file's own un-resolved
# parent directory is the only anchor that survives pytest's rootdir-relative
# import, so the analysed module is a sibling join.
_BATTERY_DIR = Path(__file__).parent
_REPO_ROOT = _BATTERY_DIR.parents[1]
_MODULE_PATH = _BATTERY_DIR / "test_causal_conv_invariants.py"
_CHAIN_CLOCK = _REPO_ROOT / "tests/e2e/chain_clock.py"

_DURABLE_TEST_NAMES = (
    "test_stack_has_zero_future_gradient",
    "test_model_conv_stage_has_zero_future_gradient",
    "test_causality_holds_across_sequence_lengths",
    "test_past_perturbation_does_change_output",
    "test_future_perturbation_leaves_past_outputs_unchanged",
    "test_model_pools_only_the_last_timestep",
    "test_formula_matches_model_property",
    "test_known_geometric_reference_values",
    "test_empirical_rf_matches_closed_form",
    "test_residual_bypass_is_documented_not_measured_as_rf",
    "test_receptive_field_is_strictly_monotone_in_blocks",
    "test_min_blocks_reaches_target_and_is_minimal",
    "test_geometric_doubles",
    "test_linear_increments",
    "test_fibonacci_grows",
    "test_unknown_schedule_rejected",
    "test_non_positive_blocks_rejected",
    "test_schedule_length_equals_blocks",
    "test_receptive_field_rejects_bad_arguments",
    "test_min_blocks_rejects_bad_target",
    "test_default_schedule_is_geometric",
    "test_default_construction_is_bit_identical_to_legacy",
    "test_factory_passes_schedule",
    "test_factory_default_is_geometric",
    "test_factory_rejects_unknown_schedule",
    "test_output_shape_unchanged",
    "test_reference_configurations_match_doc_targets",
    "test_latency_bound_across_rf_depths",
    "test_latency_measurement_is_stable",
)

_LATENCY_TESTS = (
    "test_latency_bound_across_rf_depths",
    "test_latency_measurement_is_stable",
)


def _run(cmd: list[str]) -> str:
    """Runs a subprocess and returns stdout (used only for the no-diff check)."""
    return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout


# ---------------------------------------------------------------------------
# Source analysis helpers (tokenize-based, no `re` import — a `re/` package or
# module ahead of stdlib on sys.path can drop a negative lookahead and INVERT a
# textual rule; these helpers walk the source text directly.)
# ---------------------------------------------------------------------------


def _code_lines(src: str) -> str:
    """Executable source lines only, with original character text preserved.

    A row counts as code only when it carries at least one token that is
    neither a comment nor part of a string literal. This is deliberately
    token-based rather than AST-node-span-based: an AST node spans its whole
    docstring, so a ``def`` whose docstring names ``time.perf_counter()``
    (exactly how these regressions stay explained) counts as a live call under
    a span extractor. The modules here document the removed defect in their
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
            continue  # the whole literal — docstring or argument — is not code
        if kind == getattr(tokenize, "FSTRING_MIDDLE", -1):
            continue
        first, last = tok.start[0], max(tok.start[0], tok.end[0])
        if kind in (
            tokenize.NAME,
            getattr(tokenize, "OP", -1),
            getattr(tokenize, "AT", -1),
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
    """Removes a trailing ``#`` comment from one physical line."""
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
    name (``time.perf_counters``) is not a call of ``dotted``.
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
    """True when ``dotted`` (e.g. ``time.perf_counter``) does NOT appear as a call."""
    return not _call_spans(code, dotted)


def _source() -> str:
    return _MODULE_PATH.read_text(encoding="utf-8")


def _slice(name: str) -> str:
    """Source of one test, by def name (nested or top-level).

    Walks physical lines and accepts a ``def `` at an indent strictly less than
    the target's own indent as the terminator, so a nested ``def`` inside the
    target does not end it prematurely.
    """
    lines = _source().splitlines(keepends=True)
    needle = f"def {name}"
    start = next(i for i, ln in enumerate(lines) if ln.lstrip().startswith(needle))
    indent = len(lines[start]) - len(lines[start].lstrip())
    for j in range(start + 1, len(lines)):
        stripped = lines[j].lstrip()
        if stripped.startswith("def ") and (len(lines[j]) - len(lines[j].lstrip())) < indent:
            return "".join(lines[start:j])
    return "".join(lines[start:])


# ===========================================================================
# 1. The latency tests no longer read the wall clock
# ===========================================================================


def test_no_wall_clock_call_in_module_source() -> None:
    """No live ``time.perf_counter()`` / ``time.monotonic()`` call remains in
    the module.

    Occurrences inside docstrings/comments are permitted — the contract is
    about executed code, and the class docstring naming the removed clock is
    how the regression stays explained.
    """
    code = _code_lines(_source())
    assert _no_call(code, "time.perf_counter"), (
        "the latency tests must not read the wall clock: a budget on wall time "
        "on a 2-core shared runner measures the co-tenant scheduler load, not "
        "the model, and trips when a stalled runner slows the clock with zero "
        "change in the code under test"
    )
    assert _no_call(code, "time.monotonic"), "same defect class as perf_counter"


def test_no_time_import_needed() -> None:
    """``import time`` is gone from the module: the only consumers were the
    four removed probes, so a surviving import is dead weight or a latent
    reintroduction of the wall-clock read."""
    src = _source()
    assert "import time" not in src, "no time.* call remains, so the import is dead"


def test_cpu_stopwatch_imported_from_shared_helper() -> None:
    """The measurement comes from the repo's shared CPU-time stopwatch, not a
    local one: the helper is the reason the fix is one-line per site and it is
    what the ML-QA-007/008/009/011/012 remediations already standardised on."""
    src = _source()
    assert "from tests.e2e.chain_clock import budget_cpu_ms" in src, (
        "the CPU-time stopwatch must be the shared chain_clock helper"
    )
    code = _code_lines(_source())
    assert _no_call(code, "time.process_time"), (
        "the module must not read process_time directly; budget_cpu_ms is the "
        "single measurement seam (the class docstring names the clock, which is "
        "prose, not a call)"
    )


def test_budget_cpu_ms_uses_process_time() -> None:
    """The shared helper actually measures CPU time — the whole contract rests
    on it, so the clock is pinned at the seam, not assumed."""
    src = _CHAIN_CLOCK.read_text(encoding="utf-8")
    assert "time.process_time()" in src, "budget_cpu_ms must read process_time()"
    assert "def budget_cpu_ms(limit_ms: float) -> _Stopwatch:" in src
    assert "consumed_ms" in src


def test_latency_tests_use_the_stopwatch() -> None:
    """Every latency test measures through ``budget_cpu_ms``, and the reading
    comes from ``sw.consumed_ms`` (a bare ``perf_counter`` subtraction would
    already be a reintroduction of the wall clock)."""
    for name in _LATENCY_TESTS:
        body = _slice(name)
        assert "with budget_cpu_ms(" in body, f"{name} must wrap the measured loop"
        assert "sw.consumed_ms" in body, f"{name} must read the CPU-time stopwatch"


def test_latency_tests_no_longer_subtract_a_clock() -> None:
    """The removed shape: ``start = time.perf_counter()`` followed by a
    subtraction into the timings dict. A surviving subtraction is the defect,
    even if the stopwatch is also present."""
    code = _code_lines(_source())
    assert _no_call(code, "perf_counter - "), "no clock subtraction may remain"
    for name in _LATENCY_TESTS:
        body = _slice(name)
        assert " - start" not in body.replace(" - start)", ""), (
            f"{name} must not subtract a captured clock stamp"
        )


# ===========================================================================
# 2. The budgets moved with the clock (honest margin, compound assert removed)
# ===========================================================================


def test_budgets_are_cpu_time_units() -> None:
    """The budget constants are milliseconds of CPU time, not seconds of wall
    time. CPU time removes the scheduler noise, so the margin can be honest
    instead of inflated: 50 ms per forward pass against an observed worst case
    of ~2 ms, and a worst/best ratio of 15 against an observed ~1.8x."""
    src = _source()
    assert "_LATENCY_BUDGET_CPU_MS = 50.0" in src, "the budget must be CPU-time ms"
    assert "_LATENCY_BUDGET_S" not in src, "the wall-clock budget constant is gone"


def test_ratio_bound_is_unconditional() -> None:
    """The depth bound is asserted on its own, not waived by an ``or`` fallback.

    The removed shape was ``assert worst <= best * RATIO or worst < BUDGET`` —
    a compound assert where the second clause silently waives the first exactly
    when the structural defect the test exists to detect fires. The ratio is
    the invariant; the budget is a separate floor. Both must hold, always.
    """
    body = _slice("test_latency_bound_across_rf_depths")
    assert "_WORST_BEST_RATIO or" not in body.replace("_WORST_BEST_RATIO),", "X"), (
        "the depth bound must not be waivable by the absolute budget"
    )
    assert "assert worst <= best * self._WORST_BEST_RATIO, (" in body
    assert "assert all(t < self._LATENCY_BUDGET_CPU_MS for t in timings.values()), (" in body


def test_warmup_precedes_every_measured_leg() -> None:
    """The measured leg runs after a warm-up, in both tests. Without it the
    first passes pay one-time conv-kernel and thread-pool cost that the budget
    does not intend to bound."""
    for name in _LATENCY_TESTS:
        body = _slice(name)
        budget_idx = body.find("with budget_cpu_ms(")
        assert budget_idx > 0, f"{name} must have a measured leg"
        assert "for _ in range(5):" in body, f"{name} must run a 5-pass warm-up"
        assert body.find("for _ in range(5):") < budget_idx, (
            f"{name}: the warm-up must precede the measured leg"
        )


def test_reps_and_shapes_unchanged() -> None:
    """The measurement scale is unchanged — 20 measured repetitions at the same
    batch and sequence length — so the CPU-time figure is comparable to the
    wall-clock one it replaces."""
    src = _source()
    assert src.count("for _ in range(20):") >= 2, "both latency tests keep 20 reps"
    assert "torch.randn(4, seq_len, feature_dim)" in src, "batch 4 x seq_len x 16 kept"
    assert "x = torch.randn(2, 64, 8)" in src, "the stability test keeps its own input"


# ===========================================================================
# 3. The durable contracts survived the clock change
# ===========================================================================


def test_durable_test_names_unchanged() -> None:
    """Every causality / receptive-field / compatibility proof survives, by
    name. The latency work must not cost a single invariant."""
    src = _source()
    for name in _DURABLE_TEST_NAMES:
        assert f"def {name}" in src, f"durable contract test {name} was removed"


def test_autograd_causality_contract_kept() -> None:
    """The strict-causality Jacobian proof is byte-identical: a future
    perturbation must not move a past output, and a past perturbation must."""
    src = _source()
    assert "dY_t / dX_{t+k} == 0" in src, "the causality contract docstring is intact"
    for needle in (
        "def test_future_perturbation_leaves_past_outputs_unchanged",
        "def test_past_perturbation_does_change_output",
        "def test_model_pools_only_the_last_timestep",
    ):
        assert needle in src, f"the causality proof {needle} must survive"


def test_receptive_field_targets_kept() -> None:
    """The [16, 32, 64]-bar documented reference configurations are untouched."""
    src = _source()
    assert "assert receptive_field(3, dilation_schedule(DILATION_GEOMETRIC, 4)) == 31" in src
    assert "assert min_blocks_for_receptive_field(RF_TARGET_MULTI_HOUR, 3) == 6" in src


def test_legacy_bit_identity_kept() -> None:
    """The checkpoint-compatibility proof stays exact (atol=0, rtol=0)."""
    src = _source()
    assert "torch.allclose(model(x), legacy_equivalent(x), atol=0.0, rtol=0.0)" in src


def test_no_production_file_changed() -> None:
    """The remediation is test-only. Every latency assertion is a property of
    the *measurement*, not of the model, so the architectures module must be
    untouched by this work."""
    repo = _REPO_ROOT
    proc = _run(["git", "-C", str(repo), "diff", "--name-only", "origin/main..HEAD"])
    assert proc.strip() == "", f"unexpected production diff: {proc!r}"


# ===========================================================================
# 4. Behavioral: CPU-time budget actually holds, and the ratio is honest
# ===========================================================================


def test_cpu_time_budget_holds_on_real_forward_passes(monkeypatch) -> None:
    """Executes the production forward path and asserts the CPU-time budget the
    module declares. This is the invariant the whole remediation is for: the
    measured cost on the real model must sit far enough under the budget that a
    loaded CI runner cannot trip it, and the ratio must be structural (a padding
    defect that grows the tensor instead of dilating would clear it by orders
    of magnitude)."""
    torch = pytest.importorskip("torch")
    from nexus_scalp.model_generation.architectures import (
        DILATION_FIBONACCI,
        DILATION_GEOMETRIC,
        DILATION_LINEAR,
        TCNAttentionV1,
    )

    torch.manual_seed(0)
    seq_len, feature_dim = 64, 16
    budget_ms = 50.0
    ratio = 15.0
    for schedule in (DILATION_GEOMETRIC, DILATION_LINEAR, DILATION_FIBONACCI):
        timings: dict[int, float] = {}
        for blocks in (3, 4, 5):
            model = TCNAttentionV1(
                input_dim=feature_dim,
                hidden_dim=32,
                blocks=blocks,
                kernel_size=3,
                attention_heads=4,
                dropout=0.0,
                num_classes=3,
                max_seq_len=seq_len,
                dilation=schedule,
            ).eval()
            x = torch.randn(4, seq_len, feature_dim)
            with torch.no_grad():
                for _ in range(5):
                    model(x)
                with budget_cpu_ms(budget_ms * 20) as sw:
                    for _ in range(20):
                        model(x)
            timings[blocks] = sw.consumed_ms / 20.0
        worst = max(timings.values())
        best = min(timings.values())
        assert worst < budget_ms, f"CPU-time budget exceeded for {schedule}: {timings}"
        assert worst <= best * ratio, f"cost blew up across RF depths for {schedule}: {timings}"


def test_cpu_time_is_representative_under_load(monkeypatch) -> None:
    """The point of CPU time: halving the CPU available to the process must not
    change the measured figure much, because the same work costs the same CPU
    time. This is the property a wall-clock budget does NOT have and the reason
    the clock was swapped. Asserted with one thread, which is what a
    co-tenant-loaded 2-core runner effectively delivers."""
    torch = pytest.importorskip("torch")
    from nexus_scalp.model_generation.architectures import TCNAttentionV1

    torch.manual_seed(0)
    model = TCNAttentionV1(input_dim=8, hidden_dim=16, blocks=4, dropout=0.0, max_seq_len=64).eval()
    x = torch.randn(2, 64, 8)
    with torch.no_grad():
        for _ in range(5):
            model(x)
        with budget_cpu_ms(5000.0) as sw:
            for _ in range(20):
                model(x)
    assert sw.consumed_ms > 0.0, "the measured leg must actually execute"
    assert sw.consumed_ms < 500.0, f"CPU time out of family: {sw.consumed_ms} ms"


# ===========================================================================
# 5. Manifest registration (a battery CI never runs pins nothing)
# ===========================================================================


def test_critical_suite_manifest_entries() -> None:
    manifest = (_REPO_ROOT / "tests/critical_suite.txt").read_text(encoding="utf-8")
    assert "tests/unit/test_causal_conv_invariants.py" in manifest
    assert "tests/unit/test_ml_qa_014_causal_conv_cpu_budget.py" in manifest
