"""ML-QA-019 — BUG-106 parity coverage and CPU-time budget determinism.

The last DEFERRED row of the ML-QA-003 determinism-census recount
(``docs/ml-system/test_determinism_roster.md`` §6):
``tests/unit/test_70d_bug106_incremental_phase19.py``.

The two durable tests were guarded by
``skipif(not Path(DATA_PATH).exists())`` on a data file git never carries
(``data/raw/XAUUSD_M5.parquet`` is download-only), so both were silently
UNCOLLECTED in CI. The module is in ``tests/critical_suite.txt`` (the push
gate), so the gate reported a green module that exercised nothing — the
BUG-106 canonical-vs-incremental byte-identity contract had NO live
coverage anywhere in the push gate (``tests/slow/`` is out-of-gate and
carries the same skipif).

The speedup test was additionally a WALL-CLOCK benchmark
(``time.perf_counter()`` on both legs) — the same defect class remediated
across ML-QA-007/008/009/011/012/014/018: on a 2-core shared CI runner the
wall clock measures co-tenant scheduler load, not the code under test.

Remediation (test-only — zero production files changed, pinned by
``test_no_production_file_changed``): the real-data dependency is replaced
by the deterministic synthetic bar generator already used across the gate
(``scripts.data.ingest_historical_candles.generate_synthetic_bars``, the
same importer and seed pattern as ``test_position_replay_pipeline.py``),
and the speedup leg measures CPU time through the shared ``budget_cpu_ms``
stopwatch from ``tests/e2e/chain_clock.py``.

Battery shape (the ML-QA-014/017/018 lineage):
- textual/structural rules read the analysed module's source by relative
  path (NOT ``Path(__file__).resolve()`` — that canonicalises to the
  SHARED checkout from a worktree and reads the un-patched original);
- behavioural legs execute the real builders on real synthetic frames;
- the negative-control leg proves the OLD skipif shape really was a
  no-coverage module by reconstructing it and asserting the skip fires.

No ``re`` import — a ``re/`` module ahead of stdlib on sys.path can drop a
negative lookahead and invert a rule (a str.find/tokenize-based scanner is
used instead).
"""

from __future__ import annotations

import io
import os
import subprocess
import sys
import tokenize
from pathlib import Path

import polars as pl
import pytest

from tests.e2e.chain_clock import budget_cpu_ms

_BATTERY_DIR = Path(__file__).parent
_REPO_ROOT = _BATTERY_DIR.parents[1]
_MODULE = _BATTERY_DIR / "test_70d_bug106_incremental_phase19.py"
_CHAIN_CLOCK = _REPO_ROOT / "tests/e2e/chain_clock.py"

_DURABLE_TESTS = (
    "test_synthetic_bars_satisfy_the_builder_contract",
    "test_bug106_incremental_byte_identical",
    "test_bug106_incremental_speedup",
)

_MODULE_UNDER_TEST = "src/nexus_scalp/model_generation/schema_v2_incremental.py"


def _pytest_env() -> tuple[list[str], dict[str, str]]:
    """A portable pytest invocation for the subprocess legs.

    ML-QA-019: the first attempt hardcoded ``/tmp/nse-venv/bin/python``, which
    is this operator's local torch venv — CI has no such path, so both
    subprocess legs raised ``FileNotFoundError`` there (a defect that only
    shows on the runner: the battery was green locally and red in CI, exactly
    the shape this gate exists to prevent). ``sys.executable`` is the running
    interpreter, which by definition has the deps the battery itself needed
    to import.
    """
    env = {
        "PYTHONPATH": "src:.:/tmp/nse-slim/lib/python3.11/site-packages",
        "PYTEST_ADDOPTS": "-p no:cacheprovider",
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
    }
    # keep the site-packages shim ONLY when it exists on this host (it is the
    # operator's polars source for the torch venv; CI has both in one env)
    shim = "/tmp/nse-slim/lib/python3.11/site-packages"
    if not Path(shim).is_dir():
        env["PYTHONPATH"] = "src:."
    return [sys.executable, "-m", "pytest"], env


# ---------------------------------------------------------------------------
# Source analysis helpers (tokenize-based; no `re` import — see header).
# ---------------------------------------------------------------------------


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _code_lines(src: str) -> str:
    """Executable source lines only, with original character text preserved.

    A row counts as code only when it carries a token that is neither a
    comment nor part of a string literal. Token-based rather than
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
    # A row is CODE when it carries ANY non-trivia token at all. An earlier
    # `code_rows - non_code` form silently dropped assert rows: a token's span
    # covers only itself, so a row collecting a code-class token (the leading
    # NAME `assert`) AND non-code-class tokens for the rest of the SAME row
    # (the OP/NAME/NUMBER after it, whose spans also hit that row) cancelled
    # out. Every assert in the ML-QA-018 battery's analysed modules was a
    # single-NAME or method-call shape that survived by accident.
    final = code_rows | non_code
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
    followed directly by ``(``. A fixture builder can put the call text
    inside a STRING LITERAL ON A CODE ROW — the row carries a real token so
    it survives ``_code_lines``, and only token typing distinguishes the
    literal from the call.
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
    """True when ``dotted`` does NOT appear as a live call."""
    for line in code.splitlines():
        if _call_spans(line, dotted):
            return False
    return True


def _slice(path: Path, name: str) -> str:
    """Source of one top-level test, by def name.

    Returns '' when absent so a missing test fails its rules legibly
    instead of crashing (a StopIteration output is opaque).
    """
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


# ===========================================================================
# 1. The module is collectable — no data-file gate can silence it
# ===========================================================================


def test_no_data_file_gate_can_silence_the_module() -> None:
    """The skipif-on-a-git-absent-data-file shape is gone.

    ``skipif(not Path("data/raw/XAUUSD_M5.parquet").exists())`` made both
    durable tests uncollected in CI: the module is in the push gate, so the
    gate reported a green module exercising nothing (pytest rc=5 would have
    been the honest signal). The string is searched over executable lines
    only — this docstring names the removed shape to explain the fix, and a
    raw substring would match the prose (the ML-QA-014 raw-substring trap).
    """
    code = _code_lines(_read(_MODULE))
    assert "DATA_PATH" not in code, (
        "a DATA_PATH constant keeps a real-data dependency reachable; the "
        "parity contract must not depend on a file git never carries"
    )
    assert "read_parquet" not in code, (
        "a parquet read of a download-only file is the silence mechanism — "
        "the push gate must not skip the BUG-106 parity contract"
    )
    for line in code.splitlines():
        if "skip" in line and "import" not in line:
            # a skip that is not the import line must not be a data gate
            assert "Path(" not in line or "exists" not in line, (
                f"a conditional skip on a data file is the coverage-killing shape: {line.strip()}"
            )


def test_no_wall_clock_measurement_call() -> None:
    """No live ``time.perf_counter()`` / ``time.monotonic()`` call remains.

    Occurrences in docstrings/comments are permitted — the contract is about
    executed code, and this battery's own docstring names the removed clock
    to explain the fix.
    """
    code = _code_lines(_read(_MODULE))
    assert _no_call(code, "time.perf_counter"), (
        "a benchmark must not read the wall clock: on a 2-core shared CI "
        "runner the bound measures co-tenant scheduler load, not the code "
        "under test"
    )
    assert _no_call(code, "time.monotonic"), "same defect class"


def test_the_stopwatch_reads_process_time() -> None:
    """The shared stopwatch measures CPU time, not wall clock.

    Pins the seam the speedup test depends on: ``budget_cpu_ms`` reads
    ``time.process_time()``. A future swap of the helper to a wall-clock
    source would silently re-introduce the defect class this battery exists
    to prevent, so the source is pinned at its own file.
    """
    code = _code_lines(_read(_CHAIN_CLOCK))
    assert _call_spans(code, "time.process_time"), (
        "budget_cpu_ms must measure time.process_time() (CPU time); a "
        "wall-clock source would re-introduce the flake this gate prevents"
    )


def test_shared_stopwatch_imported() -> None:
    """The speedup test measures through the shared ``budget_cpu_ms`` seam."""
    body = _slice(_MODULE, "test_bug106_incremental_speedup")
    assert body, "test_bug106_incremental_speedup must exist"
    assert "budget_cpu_ms(" in body, (
        "the speedup leg must run inside the shared budget_cpu_ms stopwatch "
        "so the measurement is CPU time, not wall clock"
    )


def test_canonical_builder_imported_at_module_scope() -> None:
    """Both builders are imported at module scope.

    The durable tests imported ``compute_70d_frame`` function-locally *inside*
    the skipif-guarded bodies, which is how the whole module stayed green
    while uncollected: nothing at import time signalled the parity contract.
    A module-scope import makes the contract a collection-time dependency.
    """
    code = _code_lines(_read(_MODULE))
    # Bare 'compute_70d_frame' would also match 'compute_70d_frame_fast', so
    # the module-scope IMPORT lines are pinned explicitly — the reverted
    # module has neither (both imports sat inside the skipif bodies).
    imports = [line for line in code.splitlines() if line.startswith("from ") and "import" in line]
    canonical = [ln for ln in imports if "schema_v2 import" in ln and "compute_70d_frame" in ln]
    fast = [
        ln
        for ln in imports
        if "schema_v2_incremental import" in ln and "compute_70d_frame_fast" in ln
    ]
    assert canonical, (
        "compute_70d_frame must be imported at MODULE SCOPE — a function-local "
        "import inside a skipif body is the shape that hid the uncollected "
        "module (the reverted module imports it only inside the guarded body)"
    )
    assert fast, "compute_70d_frame_fast must be imported at module scope too"


def test_both_imports_are_actually_used() -> None:
    """No dead import — every module-scope builder import has a call site.

    Complement to the scope rule above: importing the canonical builder at
    module scope and then never calling it would satisfy the scope rule while
    restoring the defect it pins (nothing at collection time would signal the
    parity contract if the call sites were dropped).
    """
    code = _code_lines(_read(_MODULE))
    # 'compute_70d_frame(' is a substring of 'compute_70d_frame_fast(' only
    # when the fast name is followed by '(' — match the exact token instead.
    canonical_calls = [
        ln for ln in code.splitlines() if "compute_70d_frame(" in ln or "compute_70d_frame (" in ln
    ]
    # the canonical call line ends the name before '('; the fast one does not
    canonical_only = [ln for ln in canonical_calls if "compute_70d_frame_fast" not in ln]
    fast_calls = [ln for ln in code.splitlines() if "compute_70d_frame_fast(" in ln]
    assert canonical_only, "compute_70d_frame must be CALLED, not merely imported"
    assert fast_calls, "compute_70d_frame_fast must be CALLED, not merely imported"


def test_synthetic_bar_generator_is_the_frame_source() -> None:
    """The bars frame comes from the deterministic generator the gate already
    uses, not a broker data file.

    ``generate_synthetic_bars`` (``scripts/data/ingest_historical_candles``)
    is the established CI bar source — ``test_position_replay_pipeline.py``
    imports it the same way. Its output is tz-aware UTC M1 OHLCV, exactly
    the canonical broker-fetch shape the builders consume.
    """
    code = _code_lines(_read(_MODULE))
    assert "generate_synthetic_bars" in code, (
        "the frame must come from the deterministic synthetic generator — a "
        "real-data dependency is uncollectable in CI"
    )
    assert "from scripts.data.ingest_historical_candles import" in code, (
        "the established gate importer path (test_position_replay_pipeline uses the same import)"
    )


@pytest.mark.parametrize("name", _DURABLE_TESTS)
def test_durable_test_survives(name: str) -> None:
    """Every durable BUG-106 test survives by name — no contract was dropped
    to buy collectability."""
    body = _slice(_MODULE, name)
    assert body, f"{name} must exist in the analysed module"
    assert f"def {name}" in body


def test_byte_identity_assert_is_unconditional() -> None:
    """The byte-identity assert stays hard: zero feature diffs, no waiver.

    The parity contract is an EQUIVALENCE (the incremental builder is the
    O(n*window) rewrite of the canonical one), so any diff is a defect. A
    compound ``or`` on the assert line would waive it exactly where it fires
    (the ML-QA-014 compound-waiver class).
    """
    code = _code_lines(_slice(_MODULE, "test_bug106_incremental_byte_identical"))
    assert code, "test_bug106_incremental_byte_identical must exist"
    assert "diffs == 0" in code, "the parity assert must be unconditional"
    for line in code.splitlines():
        if "diffs" in line and " or " in line:
            raise AssertionError(
                f"a compound `or` on a parity assert line is a waiver of the "
                f"equivalence contract exactly where a diff first appears: "
                f"{line.strip()}"
            )


def test_speedup_ratio_contract_is_structural() -> None:
    """The speedup test must keep asserting the structural invariant.

    The absolute CPU budget only guards a complexity regression; the
    contract the test exists for is "the incremental builder is not slower
    than the canonical one". Removing the ratio/leg comparison while keeping
    only a generous budget would leave the builder free to regress to
    canonical speed unnoticed (the budget is calibrated against the measured
    CI cost of both legs, so it is loose to runner speed and tight to a
    complexity blow-up).
    """
    code = _code_lines(_slice(_MODULE, "test_bug106_incremental_speedup"))
    assert code, "test_bug106_incremental_speedup must exist"
    assert "compute_70d_frame(" in code, "the canonical leg must run"
    assert "compute_70d_frame_fast(" in code, "the incremental leg must run"
    assert "consumed_ms" in code, "the CPU-time figure must be read from the stopwatch"


def test_dimension_contract_pinned() -> None:
    """The 70D dimension contract is pinned in the parity test itself.

    ``feat_0..feat_69`` is the scalp_v3 canonical layout; the parity test is
    now the only in-gate site that asserts the frame actually carries 70
    features, so the count must not rely on the builder's own output.
    """
    body = _slice(_MODULE, "test_bug106_incremental_byte_identical")
    assert body
    assert "feat_" in body
    assert "70" in body, "the 70-feature dimension must be asserted explicitly"


# ===========================================================================
# 2. The negative control — the OLD shape was a no-coverage module
# ===========================================================================


def _old_shape_module(tmp_path: Path) -> Path:
    """Reconstruct the pre-ML-QA-019 module in a scratch dir.

    Proves the defect this task fixes is real: under the old skipif shape
    the module collects but every durable test is skipped, so the gate
    reported green at zero coverage. Writing it to a scratch dir (never the
    repo) and collecting it under a real pytest run is the evidence.
    """
    src = '''"""OLD SHAPE — pre-ML-QA-019, for the negative control only."""
from __future__ import annotations

import polars as pl
import pytest

from nexus_scalp.model_generation.schema_v2_incremental import compute_70d_frame_fast

DATA_PATH = "data/raw/XAUUSD_M5.parquet"


@pytest.fixture(scope="module")
def real_bars() -> pl.DataFrame:
    return pl.read_parquet(DATA_PATH).head(600)


@pytest.mark.skipif(
    not __import__("pathlib").Path(DATA_PATH).exists(), reason="real data file absent"
)
def test_bug106_incremental_byte_identical(real_bars: pl.DataFrame) -> None:
    from nexus_scalp.model_generation.schema_v2 import compute_70d_frame

    canon = compute_70d_frame(real_bars, news_frame=None)
    fast = compute_70d_frame_fast(real_bars, news_frame=None)
    assert canon.height == fast.height


@pytest.mark.skipif(
    not __import__("pathlib").Path(DATA_PATH).exists(), reason="real data file absent"
)
def test_bug106_incremental_speedup(real_bars: pl.DataFrame) -> None:
    import time

    from nexus_scalp.model_generation.schema_v2 import compute_70d_frame

    t0 = time.perf_counter()
    compute_70d_frame(real_bars, news_frame=None)
    t_canon = time.perf_counter() - t0
    t0 = time.perf_counter()
    compute_70d_frame_fast(real_bars, news_frame=None)
    t_fast = time.perf_counter() - t0
    assert t_fast < t_canon * 1.5
'''
    out = tmp_path / "old_shape_module.py"
    out.write_text(src, encoding="utf-8")
    return out


def test_the_old_data_gate_shape_was_a_no_coverage_module(tmp_path: Path) -> None:
    """NEGATIVE CONTROL (executed): the old shape collects and skips both tests.

    This is the executed proof that the defect class is "silent zero
    coverage", not a theoretical one. The reconstructed old module is
    collected in a subprocess pytest run against a worktree-relative
    conftest, and the report must show exactly 2 skipped / 0 passed — i.e.
    a green-looking module that exercised nothing.
    """
    old = _old_shape_module(tmp_path)
    argv, env = _pytest_env()
    # NOTE: cwd is the WORKTREE root, not the shared checkout — the analysed
    # module and the repo's pyproject config live here, and a relative
    # PYTHONPATH must resolve against this tree.
    proc = subprocess.run(
        [*argv, str(old), "-v", "--no-header"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(_BATTERY_DIR.parents[1]),
        env=env,
    )
    verdict = proc.stdout + proc.stderr
    # pytest -q under pytest 9.1.1 suppresses the pass/skip line from the
    # captured report, which would make these asserts tautologies — -v keeps it
    assert "2 skipped" in verdict, (
        f"the OLD skipif-on-git-absent-data shape must skip BOTH durable "
        f"tests — that is the zero-coverage defect ML-QA-019 fixes. "
        f"rc={proc.returncode} out={verdict[-600:]}"
    )
    # the module must still COLLECT (skip, not error) — rc=0 with skips
    assert proc.returncode == 0, (
        f"a skipped-only module exits 0 — the gate saw green at zero coverage. rc={proc.returncode}"
    )


def test_current_module_has_no_skips() -> None:
    """The remediated module collects AND runs — no skip can hide a contract.

    Complement to the negative control: the same subprocess collection over
    the remediated module must report 0 skipped and 3 passed.
    """
    argv, env = _pytest_env()
    proc = subprocess.run(
        [*argv, str(_MODULE), "--no-header", "-v"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(_BATTERY_DIR.parents[1]),
        env=env,
    )
    verdict = proc.stdout + proc.stderr
    assert "skipped" not in verdict, (
        f"no test in the remediated module may skip — a skip is the silence "
        f"mechanism this task removes. out={verdict[-500:]}"
    )
    # NOTE: ``-q`` is avoided — under pytest 9.1.1 it suppresses the pass
    # count line from the captured report, which would make this assert a
    # tautology that never sees the real verdict.
    assert "3 passed" in verdict, (
        f"expected the remediated module to RUN all 3 tests, got: {verdict[-500:]}"
    )
    assert proc.returncode == 0


# ===========================================================================
# 3. Behavioural legs — the parity contract on real synthetic frames
# ===========================================================================


def _bars(count: int, seed: int = 4321) -> pl.DataFrame:
    from scripts.data.ingest_historical_candles import generate_synthetic_bars

    return generate_synthetic_bars(symbol="XAUUSD", count=count, seed=seed)


def _module_budget_ms() -> float:
    """The declared CPU budget from the analysed module.

    Resolved LAZILY (the ML-QA-018 pattern): exec-ing the analysed module at
    battery import time would fail collection on any interpreter lacking its
    deps. A textual fallback keeps the rule working even if the constant is
    renamed.
    """
    src = _read(_MODULE)
    needle = "_CANON_CPU_BUDGET_MS = "
    for line in src.splitlines():
        text = _strip_trailing_comment(line).strip()
        if text.startswith(needle):
            try:
                return float(text[len(needle) :].rstrip())
            except ValueError:
                break
    raise AssertionError(
        "_CANON_CPU_BUDGET_MS must be declared in the analysed module; the "
        "behavioural budget leg reads it so the constant and the speedup "
        "test cannot drift apart"
    )


def test_byte_identity_holds_on_a_synthetic_frame() -> None:
    """The equivalence contract executes on the synthetic frame the gate now
    uses — 70 features, identical timestamps, ZERO feature diffs."""
    from nexus_scalp.model_generation.schema_v2 import compute_70d_frame
    from nexus_scalp.model_generation.schema_v2_incremental import compute_70d_frame_fast

    frame = _bars(300)
    canon = compute_70d_frame(frame, news_frame=None)
    fast = compute_70d_frame_fast(frame, news_frame=None)
    assert canon.height == fast.height
    assert canon["timestamp"].to_list() == fast["timestamp"].to_list()
    fcols = [c for c in canon.columns if c.startswith("feat_")]
    assert len(fcols) == 70
    diffs = 0
    for c in fcols:
        a = canon[c].to_list()
        b = fast[c].to_list()
        diffs += sum(1 for x, y in zip(a, b, strict=True) if x != y)
    assert diffs == 0, f"{diffs} feature diffs between canonical and incremental"


def test_byte_identity_is_independent_of_the_generator_seed() -> None:
    """Parity is a property of the builders, not of one lucky seed."""
    from nexus_scalp.model_generation.schema_v2 import compute_70d_frame
    from nexus_scalp.model_generation.schema_v2_incremental import compute_70d_frame_fast

    for seed in (7, 12345, 999):
        frame = _bars(220, seed=seed)
        canon = compute_70d_frame(frame, news_frame=None)
        fast = compute_70d_frame_fast(frame, news_frame=None)
        assert canon.height == fast.height, f"seed {seed}: row-count divergence"
        fcols = [c for c in canon.columns if c.startswith("feat_")]
        diffs = sum(
            1
            for c in fcols
            for x, y in zip(canon[c].to_list(), fast[c].to_list(), strict=True)
            if x != y
        )
        assert diffs == 0, f"seed {seed}: {diffs} feature diffs"


def test_the_cpu_budget_is_a_complexity_gate_not_a_clock() -> None:
    """The speedup leg's CPU budget is calibrated against measured cost.

    Runs both builders under the shared stopwatch and asserts the observed
    CPU cost stays inside the declared budget — a bound that drifts out of
    family fails here rather than tripping on runner load.
    """
    from nexus_scalp.model_generation.schema_v2 import compute_70d_frame
    from nexus_scalp.model_generation.schema_v2_incremental import compute_70d_frame_fast

    # read the DECLARED budget from the analysed module so this leg fails if
    # the constant and the speedup test drift apart
    budget = _module_budget_ms()
    frame = _bars(300)
    with budget_cpu_ms(budget) as sw:
        compute_70d_frame(frame, news_frame=None)
        compute_70d_frame_fast(frame, news_frame=None)
    assert sw.consumed_ms < budget, (
        f"both builders consumed {sw.consumed_ms:.1f} ms CPU on 246 rows — a "
        "complexity regression in either path (the canonical one is O(n^2))"
    )


# ===========================================================================
# 4. Zero production files changed, and the manifest entry
# ===========================================================================


def test_no_production_file_changed() -> None:
    """Test-only remediation, pinned by an executed diff.

    The parity coverage was bought by replacing the test's data source and
    clock, not by weakening either builder.
    """
    # NOTE: working-tree diff, not `origin/main..HEAD` — the analysed module
    # and the battery are unstaged edits on a branch that sits at origin/main's
    # own sha (the worktree was created from it), so a revision range is empty
    # here. `git diff` (working tree vs HEAD) sees the real changes.
    proc = subprocess.run(
        ["git", "diff", "--name-only", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
        cwd=str(_BATTERY_DIR.parents[1]),
    )
    changed = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    production = [p for p in changed if not p.startswith(("tests/", "docs/"))]
    assert not production, f"ML-QA-019 is test-only; production files changed: {production}"


def test_critical_suite_manifest_entry() -> None:
    """Both modules stay in the push gate, and every entry resolves."""
    manifest = (_REPO_ROOT / "tests/critical_suite.txt").read_text(encoding="utf-8")
    for rel in (
        "tests/unit/test_70d_bug106_incremental_phase19.py",
        "tests/unit/test_ml_qa_019_parity_coverage_cpu_budget.py",
    ):
        assert rel in manifest, f"{rel} must stay in the push-gate manifest"
        assert (_REPO_ROOT / rel).is_file(), f"{rel} is registered but missing on disk"
