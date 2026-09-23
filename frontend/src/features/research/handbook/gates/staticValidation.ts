/**
 * Handbook entry — STATIC_VALIDATION gate (chain position 1 of 6).
 * Compiled from src/nexus_scalp/research/{evidence,models,contract_check}.py.
 */
import type { HandbookEntry } from "../types";

export const staticValidationEntry: HandbookEntry = {
  id: "gate/STATIC_VALIDATION",
  kind: "gate",
  badge: "GATE 1 / 6",
  title: "STATIC validation",
  subtitle: "Cheap contract checks that run before any compute is spent.",
  source: "src/nexus_scalp/research/evidence.py (GATE_CHAIN), models.py, contract_check.py",
  seeAlso: ["gate/BACKTEST", "topic/pipeline", "topic/identity"],
  keywords: ["static", "schema", "contract", "feature_schema_id", "dimension", "precheck"],
  sections: [
    {
      heading: "What it is",
      body: [
        "STATIC_VALIDATION is the first node of the canonical gate chain (evidence.py::GATE_CHAIN). It answers one question before anything expensive runs: is this candidate internally consistent and comparable to the data it claims to be tested on?",
        "The check is called static because it never simulates a trade. It inspects the candidate record, its dataset linkage and the schema identity stamps that every candidate and every result carry.",
        "The research domain model is explicit about why these stamps exist: a strategy discovered under scalp_v1/50D must never be silently compared under a wider schema (models.py module docstring). The static gate is where that rule is enforced as a gate row rather than a comment.",
      ],
      bullets: [
        "Candidate shape: strategy_id, strategy_version, entry/exit/risk blocks must parse as the registry expects.",
        "Schema identity: feature_schema_id + feature_dimension are present and belong to the active contract.",
        "Dataset linkage: the candidate resolves to a dataset it can legally be evaluated against (own family samples when discovery recorded them).",
        "Static failures are DATA/TECHNICAL class — they are exactly what the retry-gate command exists for.",
      ],
    },
    {
      heading: "Where it sits in the chain",
      body: [
        "GATE_CHAIN orders the six gates: STATIC_VALIDATION -> BACKTEST -> WALK_FORWARD -> OOS -> ROBUSTNESS -> SCORING. next_gate() in evidence.py derives the successor from that tuple, so ordering is a single source of truth, not a per-callsite convention.",
        "A candidate does not advance by wall-clock or by operator patience; it advances when the current gate reaches a terminal status and the pipeline schedules the successor.",
        "Because it runs first, a STATIC_VALIDATION failure is the cheapest possible stop: no backtest CPU, no fold construction, no stress scenarios were burned on an inconsistent record.",
      ],
    },
    {
      heading: "How to read its rows",
      body: [
        "In the drawer's Gate ledger a static row carries the same columns as every other gate: status (PENDING/QUEUED/RUNNING/PASSED/FAILED/SKIPPED/BLOCKED/ERROR/CANCELLED), failure_reason, failure_class and retryable.",
        "failure_class TECHNICAL or DATA plus retryable=true means the operator may re-queue the gate from the UI once the underlying condition is fixed (a missing dataset, a schema stamp that has since been written).",
        "failure_class RESEARCH would be unusual here — the static gate does not judge statistics. If you see a research-class static failure, treat it as a taxonomy surprise and read the verbatim failure_reason before acting.",
      ],
    },
    {
      heading: "What it deliberately does NOT do",
      body: [
        "It does not look at realized R, win rate or expectancy — no performance number is consulted before the backtest gate, keeping discovery and validation separated (spec 27 boundary in discovery.py).",
        "It does not mutate the candidate. A gate is an observation with a verdict; lifecycle movement is the registry state machine's job (lifecycle.py), never a side effect of a gate row.",
        "It does not waive the evidence floors. The sample floors (8 / 20) are enforced where evidence is consumed — static validation only guarantees the pipeline can attempt to consume it.",
      ],
    },
  ],
  params: [
    {
      name: "GATE_CHAIN[0]",
      value: "STATIC_VALIDATION",
      meaning: "First gate of the canonical chain.",
      ref: "evidence.py::GATE_CHAIN",
    },
    {
      name: "required for VALIDATED",
      value: "no (chain member, not in REQUIRED_GATES_FOR_VALIDATION)",
      meaning: "VALIDATED requires BACKTEST+WF+OOS+ROBUSTNESS+SCORING passed; static validation still runs first.",
      ref: "evidence.py::REQUIRED_GATES_FOR_VALIDATION",
    },
  ],
  faq: [
    {
      q: "My static gate failed with DATA class after I rebuilt the dataset. What now?",
      a: "Fix the precondition (dataset present, schema stamps written), then use the retry button on that gate row — DATA/TECHNICAL gates are the retryable class. RESEARCH failures are never retried by the backend.",
    },
    {
      q: "Why isn't STATIC_VALIDATION in the required-gates set for VALIDATED?",
      a: "The required set names the gates whose PASSED verdicts are the evidence for VALIDATED. Static validation gates entry INTO that chain; by the time scoring completes, its earlier PASS is implied by the run having progressed.",
    },
  ],
};
