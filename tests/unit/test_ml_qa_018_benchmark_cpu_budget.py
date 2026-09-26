"""ML-QA-018 contract battery: the two remaining benchmark tests measure CPU
time, not wall clock.

WHAT THIS PINS
--------------
``tests/unit/test_sample_weights.py`` and
``tests/unit/test_research_edge_hardening_20260909.py`` are push-gate modules
(``tests/critical_suite.txt``). They were the last two OPEN rows in the ML-QA-003
determinism census recount (``docs/ml-system/test_determinism_roster.md``
section 6): two ``time.perf_counter()`` reads each, one per benchmark.

The defect class
----------------
Both are *benchmarks*: one asserts a 50,000-sample uniqueness computation stays
inside a budget, the other asserts the sized economic re-valuation stays linear
in the sample count. Both measured ``time.perf_counter()`` (wall clock) around
the measured body.

Wall clock is the wrong clock for a budget on a shared CI runner. The suite runs
on 2-core runners alongside co-tenant jobs; a stalled runner slows the wall
clock with zero change in the code under test, and the budget trips. This is the
same class ML-QA-007 (``test_mt5_adapter_parity``), ML-QA-008
(``test_experiment_registry``), ML-QA-009 (``test_audit_flush_contract``),
ML-QA-011 (``test_shadow70_safety``), ML-QA-012 (``test_outcome_flush_race``)
and ML-QA-014 (``test_causal_conv_invariants``) already fixed:
``time.perf_counter()`` belongs to the *instrumentation* concern,
``time.process_time()`` (CPU time) belongs to the *measurement* concern. A red
on only one OS of the matrix is the tell for this shape, not a platform bug.

The remediation
---------------
Test-only (no production code changed, pinned by an executed git-diff rule).
Both measured bodies are wrapped in the shared ``budget_cpu_ms`` stopwatch from
``tests/e2e/chain_clock.py``, which reads ``time.process_time()``; the figure
comes from ``sw.consumed_ms``.

The budgets moved WITH THE CLOCK, recalibrated against measured cost on a
2-core CPU-only host rather than ported from the wall-clock figures
(benchmark_margin_calibration_recipe):

* uniqueness 50k rows: measured **0.87 ms CPU** -> budget **500 ms** (~570x
  margin). The old bound was 2000 ms of WALL clock, ~2300x margin, so the new
  bound is honest in the same order of magnitude while being a different clock.
* sized path 4000 vs 1000 samples: measured ratio **4.20x** (per-sample cost
  0.0108 ms — constant, i.e. genuinely linear) -> ratio bound **6.0**. The old
  bound was ``big < small * 8 + 1.0`` of WALL clock.

Also removed: the ``+ 1.0`` additive pad on the small leg of the linearity
assert. That pad was the same waiver class as the ML-QA-014 compound assert
(``assert A or B``): at ``n=1000`` the whole measured leg costs ~10 ms CPU, so a
one-second pad made the ratio term irrelevant exactly where the regression it
guards for (per-trade scan over the growing history) would show up first. The
ratio is now asserted on its own, because linearity is the invariant the test
exists to prove and it is provable on CPU time.

This battery is TEXTUAL where the rule is about the source shape and
BEHAVIORAL where the invariant is only provable by executing the production
path. Both analysed modules are read from THIS battery file's own un-resolved
parent directory (the ML-QA-011/012/013/014 path-resolution trap:
``Path(__file__).resolve()``, ``inspect.getfile`` and an imported module's
``__file__`` all canonicalise to the SHARED checkout, so a rule reading the
analysed module through them inspects the un-patched original and fails while
the module under test is correct).
"""

from __future__ import annotations

import ast
import io
import subprocess
import tokenize
from pathlib import Path

import pytest

from tests.e2e.chain_clock import budget_cpu_ms

_BATTERY_DIR = Path(__file__).parent
_REPO_ROOT = _BATTERY_DIR.parents[1]
_WEIGHTS_PATH = _BATTERY_DIR / "test_sample_weights.py"
_RESEARCH_PATH = _BATTERY_DIR / "test_research_edge_hardening_20260909.py"
_CHAIN_CLOCK = _REPO_ROOT / "tests/e2e/chain_clock.py"

_DURABLE_WEIGHTS_TESTS = (
    "test_concurrency_empty",
    "test_concurrency_length_mismatch",
    "test_concurrency_non_overlapping",
    "test_concurrency_overlapping_stepped",
    "test_uniqueness_strictly_non_overlapping",
    "test_uniqueness_identical_overlap",
    "test_uniqueness_hand_calculated_fractional",
    "test_uniqueness_bounds_invariant",
    "test_uniqueness_polars_with_holding_bars",
    "test_uniqueness_polars_evaluated_mask",
    "test_add_sample_weights_to_dataframe",
    "test_normalize_sample_weights",
    "test_return_attributed_weights",
    "test_time_decay",
    "test_uniqueness_metrics_report",
    "test_export_sample_weights_artifact",
    "test_pytorch_weighted_dataset",
    "test_pytorch_create_weighted_dataloader",
    "test_pytorch_sample_weighted_cross_entropy",
    "test_pytorch_sample_weighted_focal_loss",
    "test_triple_barrier_sample_weights_e2e",
)

_DURABLE_RESEARCH_TESTS = (
    "test_oos_gate_attaches_significance",
    "test_oos_significance_decisive_only_when_ci_above_zero",
    "test_scoring_rejects_indecisive_oos_but_keeps_legacy_behavior",
    "test_purge_embargo_semantics_untouched_by_significance",
    "test_sized_equity_curve_matches_running_sum_reference",
    "test_shared_risk_engine_produces_identical_volumes",
    "test_audit_0008_applies_and_reaches_v8",
    "test_audit_0008_idempotent_restart_is_not_required",
    "test_audit_0008_rollback_drops_only_new_indexes",
    "test_research_hot_queries_use_the_new_indexes",
)

_BENCHMARK_TESTS = (
    "test_benchmark_50k_rows_sla",
    "test_sized_path_performance_is_linear",
)


# ---------------------------------------------------------------------------
# Source analysis helpers (tokenize-based; no `re` import — a `re/` package or
# module ahead of stdlib on sys.path can drop a negative lookahead and INVERT a
# textual rule). These are the ML-QA-014 helpers, unchanged.
# ---------------------------------------------------------------------------


def _code_lines(src: str) -> str:
    """Executable source lines only, with original character text preserved.

    A row counts as code only when it carries at least one token that is
    neither a comment nor part of a string literal. Token-based rather than
    AST-node-span-based because an AST node spans its whole docstring, so a
    ``def`` whose docstring names ``time.perf_counter()`` (exactly how these
    regressions stay explained) would count as a live call under a span
    extractor.
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
    try:
        return list(tokenize.generate_tokens(io.StringIO(segment).readline))
    except tokenize.TokenError:
        return []


def _strip_trailing_comment(line: str) -> str:
    for tok in _tokenize_line(line):
        if tok.type == tokenize.COMMENT and tok.start[1] >= _last_code_col(line):
            return line[: tok.start[1]]
    return line


def _last_code_col(segment: str) -> int:
    last = 0
    for tok in _tokenize_line(segment):
        if tok.type in (tokenize.NL, tokenize.NEWLINE, tokenize.COMMENT):
            continue
        last = max(last, tok.end[1])
    return last


def _call_spans(code: str, dotted: str) -> list[tuple[int, int]]:
    """All spans of ``dotted(`` in ``code``, ignoring longer names.

    TOKEN-based (the ML-QA-017 refinement): a real ``NAME . NAME`` chain
    followed directly by ``(``. This matters because a fixture builder can
    put the call text inside a STRING LITERAL ON A CODE ROW — the row carries
    a real token so it survives ``_code_lines``, and only token typing
    distinguishes the literal from the call.
    """
    out: list[tuple[int, int]] = []
    toks = _tokenize_line(code)
    n = len(toks)
    parts = dotted.split(".")
    for i in range(n):
        if toks[i].type != tokenize.NAME or toks[i].string != parts[0]:
            continue
        j = i
        ok = True
        for part in parts[1:]:
            if j + 2 >= n:
                ok = False
                break
            if toks[j + 1].type != tokenize.OP or toks[j + 1].string != ".":
                ok = False
                break
            if toks[j + 2].type != tokenize.NAME or toks[j + 2].string != part:
                ok = False
                break
            j += 2
        if not ok:
            continue
        if j + 1 < n and toks[j + 1].type == tokenize.OP and toks[j + 1].string == "(":
            out.append((toks[i].start[1], toks[j + 1].end[1]))
    return out


def _no_call(code: str, dotted: str) -> bool:
    """True when ``dotted`` (e.g. ``time.perf_counter``) does NOT appear as a call."""
    for line in code.splitlines():
        if _call_spans(line, dotted):
            return False
    return True


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _slice(path: Path, name: str) -> str:
    """Source of one top-level test, by def name. Returns '' when absent so a
    missing test fails its rules legibly instead of crashing (StopIteration
    output is opaque — slice_crash_vs_assertion)."""
    lines = _read(path).splitlines(keepends=True)
    needle = f"def {name}"
    start = next((i for i, ln in enumerate(lines) if ln.lstrip().startswith(needle)), -1)
    if start < 0:
        return ""
    for j in range(start + 1, len(lines)):
        stripped = lines[j].lstrip()
        if stripped.startswith("def ") and lines[j].startswith("def "):
            return "".join(lines[start:j])
    return "".join(lines[start:])


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, capture_output=True, text=True, check=False).stdout


# ===========================================================================
# 1. Neither module reads the wall clock for a measurement
# ===========================================================================


@pytest.mark.parametrize("path", [_WEIGHTS_PATH, _RESEARCH_PATH], ids=["weights", "research"])
def test_no_wall_clock_measurement_call(path: Path) -> None:
    """No live ``time.perf_counter()`` / ``time.monotonic()`` call remains in a
    measured body. Occurrences in docstrings/comments are permitted — the
    contract is about executed code, and these docstrings name the removed
    clock to explain the fix."""
    code = _code_lines(_read(path))
    assert _no_call(code, "time.perf_counter"), (
        f"{path.name}: a benchmark must not read the wall clock: on a 2-core "
        "shared CI runner the bound measures co-tenant scheduler load, not the "
        "code under test, and trips when a stalled runner slows the clock with "
        "zero change in the work"
    )
    assert _no_call(code, "time.monotonic"), f"{path.name}: same defect class"


@pytest.mark.parametrize("path", [_WEIGHTS_PATH, _RESEARCH_PATH], ids=["weights", "research"])
def test_no_captured_clock_subtraction(path: Path) -> None:
    """The removed shape: ``t0 = time.perf_counter()`` then a subtraction into
    the assert. A surviving subtraction is the defect even if the stopwatch is
    also present."""
    code = _code_lines(_read(path))
    assert _no_call(code, "perf_counter - "), f"{path.name}: no clock subtraction may remain"


@pytest.mark.parametrize("path", [_WEIGHTS_PATH, _RESEARCH_PATH], ids=["weights", "research"])
def test_no_direct_process_time_read(path: Path) -> None:
    """``budget_cpu_ms`` is the single measurement seam: the module must not
    read ``time.process_time()`` directly (the docstrings name the clock, which
    is prose, not a call)."""
    code = _code_lines(_read(path))
    assert _no_call(code, "time.process_time"), (
        f"{path.name}: read the CPU clock through the shared stopwatch, not directly"
    )


@pytest.mark.parametrize("path", [_WEIGHTS_PATH, _RESEARCH_PATH], ids=["weights", "research"])
def test_shared_stopwatch_imported(path: Path) -> None:
    """The measurement comes from the repo's shared CPU-time stopwatch, not a
    local one — the helper is what makes the fix one line per site and what the
    ML-QA-007..014 remediations already standardised on."""
    src = _read(path)
    assert "from tests.e2e.chain_clock import budget_cpu_ms" in src, (
        f"{path.name}: the CPU-time stopwatch must be the shared chain_clock helper"
    )


@pytest.mark.parametrize("path", [_WEIGHTS_PATH, _RESEARCH_PATH], ids=["weights", "research"])
def test_no_dead_time_import(path: Path) -> None:
    """An ``import time`` whose only consumers were the removed probes is dead
    weight and a latent reintroduction of the wall-clock read."""
    src = _read(path)
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert alias.name != "time", (
                    f"{path.name}: no time.* call remains, so 'import time' is dead"
                )
        elif isinstance(node, ast.ImportFrom) and node.module == "time":
            raise AssertionError(f"{path.name}: no 'from time import' may remain")


def test_the_stopwatch_reads_process_time() -> None:
    """The whole contract rests on the helper actually measuring CPU time, so
    the clock is pinned at the seam, not assumed."""
    src = _read(_CHAIN_CLOCK)
    assert "time.process_time()" in src, "budget_cpu_ms must read process_time()"
    assert "def budget_cpu_ms(limit_ms: float) -> _Stopwatch:" in src
    assert "consumed_ms" in src


# ===========================================================================
# 2. The benchmarks measure through the stopwatch
# ===========================================================================


def test_weights_benchmark_uses_the_stopwatch() -> None:
    body = _slice(_WEIGHTS_PATH, "test_benchmark_50k_rows_sla")
    assert body, "test_benchmark_50k_rows_sla must exist"
    assert "with budget_cpu_ms(" in body, "the measured body must be wrapped"
    assert "sw.consumed_ms" in body, "the reading must come from the stopwatch"
    assert "50_000" in body, "the 50k-row scale is unchanged"


def test_research_benchmark_uses_the_stopwatch() -> None:
    body = _slice(_RESEARCH_PATH, "test_sized_path_performance_is_linear")
    assert body, "test_sized_path_performance_is_linear must exist"
    assert "with budget_cpu_ms(" in body, "the measured body must be wrapped"
    assert "sw.consumed_ms" in body, "the reading must come from the stopwatch"
    assert "1000" in body and "4000" in body, "the 1k/4k legs are unchanged"


def test_weights_budget_is_cpu_time_units() -> None:
    """The budget is a CPU-time constant. CPU time removes scheduler noise, so
    the margin is honest rather than inflated: 500 ms against a measured 0.87
    ms of real work."""
    src = _read(_WEIGHTS_PATH)
    assert "_UNIQUENESS_50K_BUDGET_CPU_MS = 500.0" in src
    assert "elapsed < 2.0" not in src, "the wall-clock budget constant is gone"


def test_research_ratio_bound_is_unconditional() -> None:
    """The linearity bound is asserted on its own. The removed shape was
    ``big < small * 8 + 1.0`` — the additive pad is a waiver: at n=1000 the
    whole measured leg costs ~10 ms CPU, so a one-second pad swamps the ratio
    term exactly where the O(n^2) regression it guards for would show up.
    Asserting the ratio alone is the honest form of the invariant."""
    src = _read(_RESEARCH_PATH)
    assert "_SIZED_PATH_LINEARITY_RATIO = 6.0" in src
    body = _slice(_RESEARCH_PATH, "test_sized_path_performance_is_linear")
    # Scope to EXECUTABLE lines: the docstring names the removed ``+ 1.0`` pad
    # to explain the fix, so a raw-substring rule over the whole body matches
    # prose and fails on the correct source.
    code = _code_lines(body)
    assert "+ 1.0" not in code, "the additive waiver on the small leg is gone"
    assert "small * _SIZED_PATH_LINEARITY_RATIO" in code


# ===========================================================================
# 3. The durable contracts survived the clock change
# ===========================================================================


@pytest.mark.parametrize("name", _DURABLE_WEIGHTS_TESTS)
def test_weights_durable_test_survives(name: str) -> None:
    """Every uniqueness / concurrency / PyTorch weighting proof survives by
    name — the benchmark work must not cost a single invariant."""
    assert f"def {name}" in _read(_WEIGHTS_PATH), f"durable test {name} was removed"


@pytest.mark.parametrize("name", _DURABLE_RESEARCH_TESTS)
def test_research_durable_test_survives(name: str) -> None:
    """Every OOS-significance / sized-parity / AUDIT-0008 migration proof
    survives by name."""
    assert f"def {name}" in _read(_RESEARCH_PATH), f"durable test {name} was removed"


def test_weights_durable_math_contract_kept() -> None:
    """The AFML uniqueness contract stays intact: the module header still
    documents the coverage list (concurrency counting, uniqueness bounds,
    normalization, Kish effective sample size) and the imports the durable
    proofs use."""
    src = _read(_WEIGHTS_PATH)
    assert "Sample Uniqueness Weighting & Anti-Leakage" in src, "the module purpose is intact"
    assert "Weight Normalization (Sum to N)" in src, "the normalization contract is documented"
    assert "compute_sample_uniqueness" in src
    assert "compute_concurrency_events" in src
    assert "SampleUniquenessReport" in src


def test_research_significance_contract_kept() -> None:
    """The OOS significance contract stays exact: the bootstrap CI decides the
    verdict only when present, and a legacy producer without it keeps old
    behavior."""
    src = _read(_RESEARCH_PATH)
    assert "_oos_evidence_is_decisive" in src
    assert "ci_low" in src and "decisive" in src


def test_no_production_file_changed() -> None:
    """The remediation is test-only. A benchmark assert is a property of the
    *measurement*, not of the production code, so the production tree must be
    untouched by this work."""
    proc = _run(["git", "-C", str(_REPO_ROOT), "diff", "--name-only", "origin/main..HEAD"])
    assert proc.strip() == "", f"unexpected production diff: {proc!r}"


# ===========================================================================
# 4. Behavioral: the CPU-time budgets hold on the real production paths
# ===========================================================================


def test_uniqueness_cpu_budget_holds_on_50k_rows() -> None:
    """Executes the real 50k-row vectorized path and asserts the declared
    CPU-time budget. This is the invariant the remediation is for: the measured
    cost must sit far enough under the budget that no loaded CI runner can trip
    it (the property a wall-clock bound does NOT have), while a real O(n^2)
    regression (a per-sample loop over the concurrency array) blows through it
    by orders of magnitude."""
    np = pytest.importorskip("numpy")
    from nexus_scalp.labeling.sample_weights import compute_sample_uniqueness

    n = 50_000
    rng = np.random.default_rng(12345)
    starts = np.sort(rng.integers(0, n - 20, size=n))
    durations = rng.integers(1, 16, size=n)
    ends = np.minimum(starts + durations - 1, n - 1)

    budget = _budget_constant("_UNIQUENESS_50K_BUDGET_CPU_MS")
    with budget_cpu_ms(budget) as sw:
        weights = compute_sample_uniqueness(
            start_indices=starts, end_indices=ends, total_bars=n, normalize=True
        )
    assert len(weights) == n
    assert np.all(weights > 0.0)
    assert sw.consumed_ms > 0.0, "the measured leg must actually execute"
    assert sw.consumed_ms < budget, (
        f"CPU-time budget exceeded: {sw.consumed_ms:.3f} ms (budget {budget} ms)"
    )


def test_sized_path_is_linear_in_cpu_time() -> None:
    """Executes the real sized re-valuation and asserts the linearity ratio
    that the module declares. Per-trade cost is constant by construction, so 4x
    samples costs ~4x CPU time; the ratio bound 6.0 carries margin over the
    measured 4.20x and a genuine O(n^2) regression (a per-trade scan over the
    whole equity history) clears it."""
    from nexus_scalp.research.metrics import compute_sized_economic_pnl
    from nexus_scalp.research.models import EconomicAssumptions, ResearchSample

    econ = EconomicAssumptions()
    base_costs: dict[int, float] = {}
    for n in (1000, 4000):
        samples = _mk_research_samples(ResearchSample, n)
        with budget_cpu_ms(_budget_constant("_SIZED_PATH_BUDGET_CPU_MS")) as sw:
            compute_sized_economic_pnl(samples, econ)
        base_costs[n] = sw.consumed_ms
    assert base_costs[4000] > 0.0 and base_costs[1000] > 0.0
    ratio = base_costs[4000] / max(base_costs[1000], 1e-9)
    _ratio_bound = _budget_constant("_SIZED_PATH_LINEARITY_RATIO")
    assert ratio <= _ratio_bound, (
        f"cost is not linear in samples: {base_costs} -> ratio {ratio:.2f} (bound {_ratio_bound})"
    )


def test_sized_path_per_sample_cost_is_constant() -> None:
    """The structural form of linearity: per-sample CPU cost is constant, so
    asserting it directly is the sharpest form of the invariant (a ratio can
    absorb noise in the small leg; per-unit cost cannot)."""
    from nexus_scalp.research.metrics import compute_sized_economic_pnl
    from nexus_scalp.research.models import EconomicAssumptions, ResearchSample

    econ = EconomicAssumptions()
    per_unit: dict[int, float] = {}
    for n in (1000, 2000, 4000):
        samples = _mk_research_samples(ResearchSample, n)
        with budget_cpu_ms(_budget_constant("_SIZED_PATH_BUDGET_CPU_MS")) as sw:
            compute_sized_economic_pnl(samples, econ)
        per_unit[n] = sw.consumed_ms / float(n)
    assert min(per_unit.values()) > 0.0, "every leg must actually execute"
    worst = max(per_unit.values())
    best = min(per_unit.values())
    assert worst <= best * 3.0, (
        f"per-sample cost is not constant: {per_unit} -> worst/best {worst / best:.2f}x"
    )


# ===========================================================================
# 5. Manifest registration (a battery CI never runs pins nothing)
# ===========================================================================


def test_critical_suite_manifest_entries() -> None:
    manifest = (_REPO_ROOT / "tests/critical_suite.txt").read_text(encoding="utf-8")
    assert "tests/unit/test_sample_weights.py" in manifest
    assert "tests/unit/test_research_edge_hardening_20260909.py" in manifest
    assert "tests/unit/test_ml_qa_018_benchmark_cpu_budget.py" in manifest


# ---------------------------------------------------------------------------
# Shared constants (lazy: the analysed modules import torch/polars, so a
# module-level exec would fail COLLECTION on an interpreter lacking them; the
# constants resolve on first behavioural use and are cached)
# ---------------------------------------------------------------------------

_CONST_CACHE: dict[str, float] = {}


def _budget_constant(name: str) -> float:
    """Reads a module-level float from the analysed module that declares it.

    The textual rules pin the constant's literal in the source; the behavioural
    legs assert the value the module actually declares, so a drift between the
    two is impossible.
    """
    if name in _CONST_CACHE:
        return _CONST_CACHE[name]
    import importlib.util

    module_path = {
        "_UNIQUENESS_50K_BUDGET_CPU_MS": _WEIGHTS_PATH,
        "_SIZED_PATH_LINEARITY_RATIO": _RESEARCH_PATH,
        "_SIZED_PATH_BUDGET_CPU_MS": _RESEARCH_PATH,
    }[name]
    spec = importlib.util.spec_from_file_location("ml_qa_018_under_test", module_path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    value = float(getattr(mod, name))
    _CONST_CACHE[name] = value
    return value


def _mk_research_samples(cls: type, n: int) -> list:
    """Builds the same deterministic sample sequence the analysed module's
    fixture builds (same base date, same 60/40 win split, same 10-minute
    spacing), so the behavioural legs exercise the production path on the data
    the benchmark contract was calibrated against."""
    from datetime import UTC, datetime, timedelta

    base = datetime(2026, 8, 1, tzinfo=UTC)
    out = []
    for i in range(n):
        win = i % 10 < 6
        out.append(
            cls(
                sample_id=f"s{i}",
                experience_id=f"e{i}",
                idempotency_key=f"k{i}",
                decision_timestamp=base + timedelta(minutes=i * 10),
                outcome_timestamp=base + timedelta(minutes=i * 10 + 7),
                symbol="XAUUSD",
                strategy_id="S",
                strategy_version="1.0.0",
                regime="LONDON",
                entry_price=2000.0,
                stop_loss=1998.0,
                take_profit=2006.0,
                direction="BUY",
                realized_r=0.6 if win else -0.9,
                realized_pnl_usd=12.0 if win else -18.0,
                risk_distance=2.0,
                holding_duration_sec=420.0,
                mae_r=0.2,
                mfe_r=0.8,
            )
        )
    return out
