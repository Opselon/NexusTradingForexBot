"""Regression tests for the lifecycle verdict-mapping fix (2026-08-23).

Original defect (RC5): research/pipeline.py mapped any score.verdict that was
not exactly "VALIDATED" or "REJECTED" back to CandidateLifecycle.DISCOVERED.
Consequence: candidates that ran every gate but received an INCONCLUSIVE
(or any other non-terminal) verdict were written back as DISCOVERED, so the
research worker re-validated them every cycle (infinite loop) and the UI
showed Validated=0 / Discovered=72 forever.

The fixed behavior: every non-passing verdict becomes an explicit REJECTED.
DISCOVERED is never re-entered after gates have run. No threshold is weakened,
nothing is auto-promoted, and REJECTED remains terminal.

Harness: a real ResearchPipeline instance whose gate subsystems
(self.backtest / self.walkforward / self.oos_gate / self.robustness) and the
module-level compute_strategy_score are stubbed. All gates deterministically
PASS so ONLY the scorer verdict varies between cases.
"""

from __future__ import annotations

from typing import Any

import pytest

import nexus_scalp.research.pipeline as pipeline_module
from nexus_scalp.research.candidates import StrategyCandidate
from nexus_scalp.research.models import CandidateLifecycle, ResearchDataset
from nexus_scalp.research.pipeline import ResearchPipeline

# ---------------------------------------------------------------------------
# Stub gate/score result objects (duck-typed to what validate_candidate reads)
# ---------------------------------------------------------------------------


def _backtest_result() -> Any:
    class _BT:
        passed = True
        total_trades = 120
        expectancy_r = 0.31

        def model_dump(self, mode: str = "json") -> dict[str, Any]:
            return {"passed": True, "total_trades": 120, "expectancy_r": 0.31}

    return _BT()


def _walkforward_result() -> Any:
    class _WF:
        passed = True
        degradation = 0.04

        def model_dump(self, mode: str = "json") -> dict[str, Any]:
            return {"passed": True, "degradation": 0.04}

    return _WF()


def _oos_result() -> Any:
    class _OOS:
        status = "PASS"
        reason = ""
        oos_expectancy_r = 0.21

        def model_dump(self, mode: str = "json") -> dict[str, Any]:
            return {"status": "PASS", "oos_expectancy_r": 0.21}

    return _OOS()


def _robustness_result() -> Any:
    class _ROB:
        status = "PASS"
        reason = ""

        def model_dump(self, mode: str = "json") -> dict[str, Any]:
            return {"status": "PASS"}

    return _ROB()


def _score_result(verdict: str | None) -> Any:
    class _Score:
        final_score = 0.42

        def model_dump(self, mode: str = "json") -> dict[str, Any]:
            return {"verdict": verdict, "final_score": 0.42}

    _Score.verdict = verdict
    return _Score()


class _CapturingRegistry:
    """Records the lifecycle each registry upsert would persist."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, CandidateLifecycle]] = []

    def upsert(self, entry: Any, forbid_lifecycle_regression: bool = False) -> bool:
        self.entries.append((entry.strategy_id, entry.lifecycle))
        return True


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


def _make_pipeline_with_verdict(monkeypatch: pytest.MonkeyPatch, verdict: str | None):
    pipeline = ResearchPipeline.__new__(ResearchPipeline)
    pipeline.observability = None  # obs=None skips gate/event recording blocks

    # Real subsystem attributes with stubbed results — all gates PASS.
    pipeline.backtest = type(
        "BTGate", (), {"run": staticmethod(lambda *a, **k: _backtest_result())}
    )()
    pipeline.walkforward = type(
        "WFGate", (), {"validate": staticmethod(lambda *a, **k: _walkforward_result())}
    )()
    pipeline.oos_gate = type(
        "OOSGate", (), {"evaluate": staticmethod(lambda *a, **k: _oos_result())}
    )()
    pipeline.robustness = type(
        "RobGate", (), {"evaluate": staticmethod(lambda *a, **k: _robustness_result())}
    )()

    captured_registry = _CapturingRegistry()

    def _capture_register(candidate, dataset, lifecycle, **kw):
        captured_registry.entries.append((candidate.strategy_id, lifecycle))
        return {"strategy_id": candidate.strategy_id, "lifecycle": lifecycle.value}

    pipeline._register = _capture_register  # type: ignore[assignment]
    pipeline._record_run = lambda *a, **k: None  # type: ignore[assignment]

    # Scorer: module-level function imported inside validate_candidate's scope
    # as `compute_strategy_score` — patch it at the module attribute level used
    # by the call site (pipeline_module.compute_strategy_score).
    monkeypatch.setattr(
        pipeline_module,
        "compute_strategy_score",
        lambda *a, **k: _score_result(verdict),
        raising=False,
    )

    candidate = StrategyCandidate(
        strategy_id="STRAT-VERDICT-MAP-TEST",
        strategy_version="1.0.0",
        discovery_source="test",
        discovery_window="test-window",
        context_definition={"symbol": "XAUUSD", "fingerprint": "fingerprint-test"},
        entry_logic={"direction": "long"},
        exit_logic={"direction": "short"},
        feature_dimension=4,
    )
    dataset = ResearchDataset(dataset_id="ds-verdict-test", samples=[])

    # Family selection reads dataset.samples; empty dataset is fine because all
    # downstream stages are stubbed.
    monkeypatch.setattr(pipeline_module, "_select_family", lambda ds, cand: ds, raising=False)

    return pipeline, candidate, dataset, captured_registry


def _run_and_capture(pipeline, candidate, dataset, registry) -> CandidateLifecycle:
    pipeline.validate_candidate(candidate=candidate, dataset=dataset)
    assert registry.entries, "pipeline must persist a registry entry"
    return registry.entries[-1][1]


# ---------------------------------------------------------------------------
# Regression table
# ---------------------------------------------------------------------------

TERMINAL_CASES = [
    ("VALIDATED", CandidateLifecycle.VALIDATED),
    ("REJECTED", CandidateLifecycle.REJECTED),
    ("INCONCLUSIVE", CandidateLifecycle.REJECTED),
    ("PENDING", CandidateLifecycle.REJECTED),
    ("", CandidateLifecycle.REJECTED),
    (None, CandidateLifecycle.REJECTED),
]


@pytest.mark.parametrize("verdict,expected", TERMINAL_CASES)
def test_every_verdict_maps_to_terminal_lifecycle(monkeypatch, verdict, expected):
    """RC5 regression: a non-terminal verdict must NEVER map back to DISCOVERED.

    Old code: else -> final_lifecycle = DISCOVERED (infinite re-validation loop).
    New code: INCONCLUSIVE/unknown -> REJECTED (terminal + observable).
    """
    pipeline, candidate, dataset, registry = _make_pipeline_with_verdict(monkeypatch, verdict)
    final = _run_and_capture(pipeline, candidate, dataset, registry)

    assert final == expected
    assert final != CandidateLifecycle.DISCOVERED, (
        "candidate must never fall back to DISCOVERED after gates have run"
    )


def test_inconclusive_is_rejected_not_discovered(monkeypatch):
    """The exact historical trap: INCONCLUSIVE verdict used to loop forever."""
    pipeline, candidate, dataset, registry = _make_pipeline_with_verdict(
        monkeypatch, "INCONCLUSIVE"
    )
    final = _run_and_capture(pipeline, candidate, dataset, registry)
    assert final == CandidateLifecycle.REJECTED


def test_validated_requires_explicit_scorer_verdict(monkeypatch):
    """Safety: VALIDATED remains gated on the scorer's explicit verdict —
    nothing in the fix auto-promotes or weakens acceptance."""
    pipeline_ok, candidate, dataset, registry_ok = _make_pipeline_with_verdict(
        monkeypatch, "VALIDATED"
    )
    assert (
        _run_and_capture(pipeline_ok, candidate, dataset, registry_ok)
        == CandidateLifecycle.VALIDATED
    )
