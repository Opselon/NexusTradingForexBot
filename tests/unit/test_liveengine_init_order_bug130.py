"""Regression: LiveEngine.__init__ assigns order_manager + all critical
constructor attributes (BUG-130 init-order corruption).

Root cause (2026-08-20): a missing method boundary split `__init__` — after
the StrategyFactory block, `def _build_factory_llm_provider` / `def
_rebuild_factory_llm_provider` swallowed the ENTIRE remaining constructor
body (order_manager, trainer, champion_manager, _rolling_feature_records,
workers...). Construction succeeded syntactically (the code parsed) but
order_manager etc. were NEVER assigned, so run_loop crashed at the
EXECUTION_RECONCILIATION call with 'LiveEngine' object has no attribute
'order_manager'.

Guard: assert via AST that __init__'s body CONTAINS the critical
assignments (not merely that the file compiles — the corruption compiled
fine) and that _build/_rebuild are separate methods outside __init__.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENGINE_FILE = REPO_ROOT / "src" / "nexus_scalp" / "application" / "live_engine.py"

#: Constructor-only assignments that MUST live inside __init__ (they were
#: silently swallowed into _rebuild_factory_llm_provider by the bug).
_INIT_MARKERS = (
    "self.order_manager = OrderLifecycleManager",
    "self.trainer = WalkForwardTrainer",
    "self._rolling_feature_records: deque",
    "self.strategy_factory_worker = AutonomousLoopWorker",
    "self.champion_manager = ChampionManager",
    "self.risk_engine = RiskEngine",
    "self.signal_policy = SignalPolicy",
)


def _parse_engine() -> ast.Module:
    return ast.parse(ENGINE_FILE.read_text(encoding="utf-8"))


class TestLiveEngineInitOrderRegression:
    def test_init_contains_critical_assignments(self) -> None:
        tree = _parse_engine()
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "LiveEngine")
        init = next(m for m in cls.body if isinstance(m, ast.FunctionDef) and m.name == "__init__")
        src = ast.get_source_segment(ENGINE_FILE.read_text(encoding="utf-8"), init) or ""
        missing = [m for m in _INIT_MARKERS if m not in src]
        assert not missing, f"__init__ missing constructor assignments: {missing}"

    def test_factory_helpers_are_separate_methods_outside_init(self) -> None:
        tree = _parse_engine()
        cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "LiveEngine")
        methods = {m.name: m for m in cls.body if isinstance(m, ast.FunctionDef)}
        init = methods["__init__"]
        for name in ("_build_factory_llm_provider", "_rebuild_factory_llm_provider"):
            m = methods.get(name)
            assert m is not None, f"{name} method missing"
            # The helper must be a sibling method (outside __init__'s span)
            assert m.lineno > init.end_lineno, (
                f"{name} (line {m.lineno}) must live AFTER __init__ (ends line {init.end_lineno})"
            )
            # And its body must NOT contain constructor-only markers
            src = ast.get_source_segment(ENGINE_FILE.read_text(encoding="utf-8"), m) or ""
            for marker in _INIT_MARKERS:
                assert marker not in src, f"{name} still swallows constructor marker {marker!r}"

    def test_engine_file_compiles(self) -> None:
        import py_compile

        py_compile.compile(str(ENGINE_FILE), doraise=True)
