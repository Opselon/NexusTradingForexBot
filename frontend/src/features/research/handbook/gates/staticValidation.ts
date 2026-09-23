/**
 * Handbook entry — STATIC_VALIDATION gate (chain position 1 of 6).
 * Compiled from src/nexus_scalp/research/{evidence,models,contract_check}.py.
 */
import type { HandbookEntry } from "../types";
import type { ScoringTranslate } from "../scoring";

/** Identity translator (en): English fallback, no interpolation needed. */
const identity: ScoringTranslate = (_key, fallback) => fallback;

function buildstaticValidationEntry(t: ScoringTranslate = identity): HandbookEntry {
  return {
  id: "gate/STATIC_VALIDATION",
  kind: "gate",
  badge: t("research.hb.gates.staticValidation.badge", "GATE 1 / 6"),
  title: t("research.hb.gates.staticValidation.title", "STATIC validation"),
  subtitle: t("research.hb.gates.staticValidation.subtitle", "Cheap contract checks that run before any compute is spent."),
  source: "src/nexus_scalp/research/evidence.py (GATE_CHAIN), models.py, contract_check.py",
  seeAlso: ["gate/BACKTEST", "topic/pipeline", "topic/identity"],
  keywords: ["static", "schema", "contract", "feature_schema_id", "dimension", "precheck"],
  sections: [
    {
      heading: t("research.hb.gates.staticValidation.sec1_head", "What it is"),
      body: [
        t("research.hb.gates.staticValidation.sec1_p1", "STATIC_VALIDATION is the first node of the canonical gate chain (evidence.py::GATE_CHAIN). It answers one question before anything expensive runs: is this candidate internally consistent and comparable to the data it claims to be tested on?"),
        t("research.hb.gates.staticValidation.sec1_p2", "The check is called static because it never simulates a trade. It inspects the candidate record, its dataset linkage and the schema identity stamps that every candidate and every result carry."),
        t("research.hb.gates.staticValidation.sec1_p3", "The research domain model is explicit about why these stamps exist: a strategy discovered under scalp_v1/50D must never be silently compared under a wider schema (models.py module docstring). The static gate is where that rule is enforced as a gate row rather than a comment."),
      ],
      bullets: [
        t("research.hb.gates.staticValidation.sec1_b1", "Candidate shape: strategy_id, strategy_version, entry/exit/risk blocks must parse as the registry expects."),
        t("research.hb.gates.staticValidation.sec1_b2", "Schema identity: feature_schema_id + feature_dimension are present and belong to the active contract."),
        t("research.hb.gates.staticValidation.sec1_b3", "Dataset linkage: the candidate resolves to a dataset it can legally be evaluated against (own family samples when discovery recorded them)."),
        t("research.hb.gates.staticValidation.sec1_b4", "Static failures are DATA/TECHNICAL class — they are exactly what the retry-gate command exists for."),
      ],
    },
    {
      heading: t("research.hb.gates.staticValidation.sec2_head", "Where it sits in the chain"),
      body: [
        t("research.hb.gates.staticValidation.sec2_p1", "GATE_CHAIN orders the six gates: STATIC_VALIDATION -> BACKTEST -> WALK_FORWARD -> OOS -> ROBUSTNESS -> SCORING. next_gate() in evidence.py derives the successor from that tuple, so ordering is a single source of truth, not a per-callsite convention."),
        t("research.hb.gates.staticValidation.sec2_p2", "A candidate does not advance by wall-clock or by operator patience; it advances when the current gate reaches a terminal status and the pipeline schedules the successor."),
        t("research.hb.gates.staticValidation.sec2_p3", "Because it runs first, a STATIC_VALIDATION failure is the cheapest possible stop: no backtest CPU, no fold construction, no stress scenarios were burned on an inconsistent record."),
      ],
    },
    {
      heading: t("research.hb.gates.staticValidation.sec3_head", "How to read its rows"),
      body: [
        t("research.hb.gates.staticValidation.sec3_p1", "In the drawer\'s Gate ledger a static row carries the same columns as every other gate: status (PENDING/QUEUED/RUNNING/PASSED/FAILED/SKIPPED/BLOCKED/ERROR/CANCELLED), failure_reason, failure_class and retryable."),
        t("research.hb.gates.staticValidation.sec3_p2", "failure_class TECHNICAL or DATA plus retryable=true means the operator may re-queue the gate from the UI once the underlying condition is fixed (a missing dataset, a schema stamp that has since been written)."),
        t("research.hb.gates.staticValidation.sec3_p3", "failure_class RESEARCH would be unusual here — the static gate does not judge statistics. If you see a research-class static failure, treat it as a taxonomy surprise and read the verbatim failure_reason before acting."),
      ],
    },
    {
      heading: t("research.hb.gates.staticValidation.sec4_head", "What it deliberately does NOT do"),
      body: [
        t("research.hb.gates.staticValidation.sec4_p1", "It does not look at realized R, win rate or expectancy — no performance number is consulted before the backtest gate, keeping discovery and validation separated (spec 27 boundary in discovery.py)."),
        t("research.hb.gates.staticValidation.sec4_p2", "It does not mutate the candidate. A gate is an observation with a verdict; lifecycle movement is the registry state machine\'s job (lifecycle.py), never a side effect of a gate row."),
        t("research.hb.gates.staticValidation.sec4_p3", "It does not waive the evidence floors. The sample floors (8 / 20) are enforced where evidence is consumed — static validation only guarantees the pipeline can attempt to consume it."),
      ],
    },
  ],
  params: [
    {
      name: "GATE_CHAIN[0]",
      value: "STATIC_VALIDATION",
      meaning: t("research.hb.gates.staticValidation.param1_meaning", "First gate of the canonical chain."),
      ref: "evidence.py::GATE_CHAIN",
    },
    {
      name: "required for VALIDATED",
      value: "no (chain member, not in REQUIRED_GATES_FOR_VALIDATION)",
      meaning: t("research.hb.gates.staticValidation.param2_meaning", "VALIDATED requires BACKTEST+WF+OOS+ROBUSTNESS+SCORING passed; static validation still runs first."),
      ref: "evidence.py::REQUIRED_GATES_FOR_VALIDATION",
    },
  ],
  faq: [
    {
      q: t("research.hb.gates.staticValidation.faq1_q", "My static gate failed with DATA class after I rebuilt the dataset. What now?"),
      a: t("research.hb.gates.staticValidation.faq1_a", "Fix the precondition (dataset present, schema stamps written), then use the retry button on that gate row — DATA/TECHNICAL gates are the retryable class. RESEARCH failures are never retried by the backend."),
    },
    {
      q: t("research.hb.gates.staticValidation.faq2_q", "Why isn\'t STATIC_VALIDATION in the required-gates set for VALIDATED?"),
      a: t("research.hb.gates.staticValidation.faq2_a", "The required set names the gates whose PASSED verdicts are the evidence for VALIDATED. Static validation gates entry INTO that chain; by the time scoring completes, its earlier PASS is implied by the run having progressed."),
    },
  ],
  };
}

/** Canonical English entry (identity) — TOC, search and tests consume this. */
export const staticValidationEntry: HandbookEntry = buildstaticValidationEntry();

/**
 * Translator-aware copy (features/research/model.ts commandVerdict pattern):
 * call during render with the store's current t(); the result changes with the
 * language — never cache it outside render.
 */
export function staticValidationEntryTranslated(t?: ScoringTranslate): HandbookEntry {
  return buildstaticValidationEntry(t);
}
