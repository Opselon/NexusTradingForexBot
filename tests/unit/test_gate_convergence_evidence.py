"""Gates consume convergence evidence honestly (ECON closure P0).

ac414e82 threaded REAL fold metrics into TrainingRun.metrics with the
explicit "NOT_AVAILABLE" sentinel for values the producer did not
legitimately produce. The lifecycle gates (GATE4/GATE5) that consume
those metrics must:
  1. treat the sentinel as MISSING EVIDENCE -> gate FAILS (fail-closed),
  2. never crash converting the sentinel to float,
  3. still pass/fail real numeric values exactly as before.

Mutation intent: if a future change makes gates treat "NOT_AVAILABLE"
as a pass (or crashes on it), the lifecycle can promote a model whose
convergence was never evidenced — these tests must fail.
"""

from __future__ import annotations

import logging

from nexus_scalp.model_lifecycle.gates import (
    gate_training_stability,
    gate_validation_performance,
)

# Disable ONLY the nexus logger the gates write to (NOT the root logger:
# test_model_lifecycle_phase10's BUG-118 tests capture root-level records).
logging.getLogger("nexus_scalp.model_lifecycle.gates").disabled = True


def test_gate4_treats_not_available_as_missing_evidence_fail_closed() -> None:
    r = gate_training_stability({"final_loss": "NOT_AVAILABLE"})
    assert r.passed is False
    assert "NOT_AVAILABLE" in r.reason


def test_gate4_none_still_fails_without_crash() -> None:
    r = gate_training_stability({"final_loss": None})
    assert r.passed is False


def test_gate4_real_values_unchanged() -> None:
    ok = gate_training_stability({"final_loss": 0.888})
    exploding = gate_training_stability({"final_loss": 1e6})
    assert ok.passed is True
    assert exploding.passed is False


def test_gate5_treats_not_available_as_missing_evidence_fail_closed() -> None:
    r = gate_validation_performance({"validation_accuracy": "NOT_AVAILABLE"})
    assert r.passed is False
    assert "NOT_AVAILABLE" in r.reason


def test_gate5_real_values_unchanged() -> None:
    assert gate_validation_performance({"validation_accuracy": 0.5}).passed is True
    assert gate_validation_performance({"validation_accuracy": 0.2}).passed is False


def test_mutation_zero_substitution_detected() -> None:
    """The sentinel must NOT be coerced to a numeric zero anywhere.

    float('NOT_AVAILABLE') raises ValueError; if someone 'fixes' that by
    coercing the sentinel to 0.0 the stability gate would silently pass
    (0.0 is finite and <= 1e3) and validation would fail only by accident
    of the accuracy floor. Pin the sentinel semantics explicitly.
    """
    r = gate_training_stability({"final_loss": "NOT_AVAILABLE"})
    assert r.passed is False, "zero-coerced sentinel would pass GATE4 — mutation"
    r2 = gate_validation_performance({"validation_accuracy": "NOT_AVAILABLE"})
    assert r2.passed is False, "zero-coerced sentinel must fail GATE5 explicitly"
