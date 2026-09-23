/**
 * Handbook — gate chain overview entry + the ordered chain export.
 *
 * The chain order here MUST match evidence.py::GATE_CHAIN; tests/js/
 * research_handbook.test.js re-reads the Python source and fails the
 * build's test gate if the two ever drift.
 */
import type { HandbookEntry } from "../types";
import { staticValidationEntry } from "./staticValidation";
import { backtestEntry } from "./backtest";
import { walkForwardEntry } from "./walkForward";
import { oosEntry } from "./oos";
import { robustnessEntry } from "./robustness";
import { scoringGateEntry } from "./scoringGate";
import type { ScoringTranslate } from "../scoring";

/** Canonical chain order — mirrors evidence.py::GATE_CHAIN verbatim. */
export const GATE_CHAIN = [
  "STATIC_VALIDATION",
  "BACKTEST",
  "WALK_FORWARD",
  "OOS",
  "ROBUSTNESS",
  "SCORING",
] as const;

/** Gates REQUIRED for a VALIDATED verdict — mirrors REQUIRED_GATES_FOR_VALIDATION. */
export const REQUIRED_FOR_VALIDATED = [
  "BACKTEST",
  "WALK_FORWARD",
  "OOS",
  "ROBUSTNESS",
  "SCORING",
] as const;

/** Every GateStatus the backend may emit — mirrors evidence.py::GateStatus. */
export const GATE_STATUSES = [
  "PENDING",
  "QUEUED",
  "RUNNING",
  "PASSED",
  "FAILED",
  "SKIPPED",
  "BLOCKED",
  "ERROR",
  "CANCELLED",
] as const;

/** Terminal gate statuses — mirrors evidence.py::_TERMINAL_GATE. */
export const TERMINAL_GATE_STATUSES = ["PASSED", "FAILED", "CANCELLED"] as const;

/** Failure classes — mirrors evidence.py::FailureClass. */
export const FAILURE_CLASSES = ["TECHNICAL", "RESEARCH", "DATA", "UNKNOWN"] as const;

/** Identity translator (en): English fallback, no interpolation needed. */
const identity: ScoringTranslate = (_key, fallback) => fallback;

function buildchainOverviewEntry(t: ScoringTranslate = identity): HandbookEntry {
  return {
  id: "topic/gates",
  kind: "topic",
  badge: t("research.hb.gates.index.badge", "6 GATES"),
  title: t("research.hb.gates.index.title", "The gate chain, end to end"),
  subtitle: t("research.hb.gates.index.subtitle", "dataset -> discovery -> the six gates -> score -> registry — and never automatically to live."),
  source: "src/nexus_scalp/research/{evidence,pipeline}.py",
  seeAlso: ["topic/pipeline", "topic/lifecycle", "topic/discovery", "topic/evidence"],
  keywords: ["chain", "pipeline", "gate order", "required gates", "promotion"],
  sections: [
    {
      heading: t("research.hb.gates.index.sec1_head", "The canonical order"),
      body: [
        t("research.hb.gates.index.sec1_p1", "evidence.py defines GATE_CHAIN as a tuple and every other module derives order from it: STATIC_VALIDATION -> BACKTEST -> WALK_FORWARD -> OOS -> ROBUSTNESS -> SCORING. next_gate() walks the tuple; nothing hardcodes a successor."),
        t("research.hb.gates.index.sec1_p2", "pipeline.py states the full loop: dataset -> discovery -> backtest -> walk-forward -> OOS -> robustness -> score -> registry (the station-by-station version lives in topic/pipeline). Research is OFFLINE/BACKGROUND and never blocks the LiveEngine tick path (spec 31/32/42)."),
        t("research.hb.gates.index.sec1_p3", "Each gate emits three kinds of record: a gate row (status/reason/class/evidence link), timeline events (GATE_STARTED/…), and one immutable evidence artifact per completed evaluation. The drawer renders all three as Trace/Gates/Events/Evidence."),
      ],
    },
    {
      heading: t("research.hb.gates.index.sec2_head", "What VALIDATED actually certifies"),
      body: [
        t("research.hb.gates.index.sec2_p1", "The module-header invariant of evidence.py is the contract: VALIDATED requires BACKTEST, WALK_FORWARD, OOS, ROBUSTNESS all PASSED plus SCORING PASSED plus a closed evidence artifact per gate."),
        t("research.hb.gates.index.sec2_p2", "REJECTED requires at least one gate FAILED or a terminal research failure — it is never the default for unprocessed candidates. An untouched candidate is DISCOVERED, not rejected."),
        t("research.hb.gates.index.sec2_p3", "A research run is IMMUTABLE once completed: research_runs is append-only and new runs never overwrite prior runs. History is additive; interpretation changes, records do not."),
      ],
    },
    {
      heading: t("research.hb.gates.index.sec3_head", "Family-select validation (TASK-4)"),
      body: [
        t("research.hb.gates.index.sec3_p1", "Every validation gate runs on the candidate\'s OWN context family — the sample_ids recorded at discovery — instead of the whole heterogeneous dataset (pipeline.py module header)."),
        t("research.hb.gates.index.sec3_p2", "Before this fix, a \'LONDON RANGING\' candidate was evaluated on trades from 22 different context families, so its OOS/expectancy/robustness numbers were not family-specific evidence."),
        t("research.hb.gates.index.sec3_p3", "When discovery_evidence.sample_ids is absent (legacy registry revalidates), the pipeline falls back to the full dataset — loudly documented as legacy behavior, not silently as equivalent evidence."),
      ],
    },
    {
      heading: t("research.hb.gates.index.sec4_head", "Promotion is a human act"),
      body: [
        t("research.hb.gates.index.sec4_p1", "pipeline.py: \'A candidate NEVER becomes live automatically. Promotion is operator-gated on the production side.\' The UI\'s Promote -> SHADOW / Promote -> ACTIVE buttons are the only path, and both require an actor id recorded in lineage."),
        t("research.hb.gates.index.sec4_p2", "The state machine is the enforcement, not the button: lifecycle.py::approve_for_live raises unless the candidate is SHADOW or VALIDATED, and require_validation_gate keeps anything earlier out of trade-eligibility."),
        t("research.hb.gates.index.sec4_p3", "ACTIVE means real dispatch authority in LIVE mode — the confirm modal says so because it is true: the registry will reject invalid jumps regardless of the click, but a legal jump executes real authority."),
      ],
    },
    {
      heading: t("research.hb.gates.index.sec5_head", "Terminal statuses and retry policy"),
      body: [
        t("research.hb.gates.index.sec5_p1", "Terminal gate statuses (never retried): PASSED, FAILED, CANCELLED. Retryable classes are TECHNICAL (infra: timeout, provider failure, restart) and DATA (missing dataset/outcome/schema -> BLOCKED)."),
        t("research.hb.gates.index.sec5_p2", "RESEARCH-class failures (statistical: threshold not met) are NEVER retryable by the backend — retrying identical statistics reproduces identical failure. The UI hides retry for those rows because the API would refuse them anyway."),
        t("research.hb.gates.index.sec5_p3", "Run statuses are separate: QUEUED/RUNNING/COMPLETED/FAILED/CANCELLED/BLOCKED. Cancelling a run makes it CANCELLED — never FAILED — and completed gate results are preserved (the drawer\'s cancel modal states exactly this)."),
      ],
    },
  ],
  params: [
    {
      name: "GATE_CHAIN",
      value: "STATIC_VALIDATION → BACKTEST → WALK_FORWARD → OOS → ROBUSTNESS → SCORING",
      meaning: t("research.hb.gates.index.param1_meaning", "Single source of truth for gate ordering."),
      ref: "evidence.py",
    },
    {
      name: "REQUIRED_GATES_FOR_VALIDATION",
      value: "BACKTEST, WALK_FORWARD, OOS, ROBUSTNESS, SCORING",
      meaning: t("research.hb.gates.index.param2_meaning", "All must be PASSED with closed evidence artifacts for VALIDATED."),
      ref: "evidence.py",
    },
    {
      name: "retry policy",
      value: "TECHNICAL/DATA retryable; RESEARCH never; terminal = PASSED/FAILED/CANCELLED",
      meaning: t("research.hb.gates.index.param3_meaning", "Which gate failures the operator may re-queue."),
      ref: "evidence.py + debug_research_routes retry-gate",
    },
    {
      name: "run statuses",
      value: "QUEUED | RUNNING | COMPLETED | FAILED | CANCELLED | BLOCKED",
      meaning: t("research.hb.gates.index.param4_meaning", "Lifecycle of one validation attempt (a run spans all six gates)."),
      ref: "evidence.py::RunStatus",
    },
  ],
  faq: [
    {
      q: t("research.hb.gates.index.faq1_q", "Why does my candidate sit in QUEUED with no progress?"),
      a: t("research.hb.gates.index.faq1_a", "The research worker drains the queue on its cycle. Check the Worker tab: a STUCK/FAILED heartbeat (no beat > 900 s while RUNNING, or status FAILED) explains a frozen queue far more often than a slow gate."),
    },
    {
      q: t("research.hb.gates.index.faq2_q", "Do all six gates need to pass to promote?"),
      a: t("research.hb.gates.index.faq2_a", "To be VALIDATED (and thus promotable), the five required gates must PASS with evidence artifacts, and scoring\'s verdict chain must reach VALIDATED. Static validation gates entry to the chain rather than being listed in the required set."),
    },
    {
      q: t("research.hb.gates.index.faq3_q", "Are runs ever rewritten?"),
      a: t("research.hb.gates.index.faq3_a", "No. Completed runs are immutable and research_runs is append-only — new evaluations create new run ids, so lineage audits always read the original record."),
    },
  ],
  };
}

/** Canonical English entry (identity) — TOC, search and tests consume this. */
export const chainOverviewEntry: HandbookEntry = buildchainOverviewEntry();

/**
 * Translator-aware copy (features/research/model.ts commandVerdict pattern):
 * call during render with the store's current t(); the result changes with the
 * language — never cache it outside render.
 */
export function chainOverviewEntryTranslated(t?: ScoringTranslate): HandbookEntry {
  return buildchainOverviewEntry(t);
}

/** All chain-kind entries in chain order (the overview rides along as topic). */
export const pipelineEntries: HandbookEntry[] = [
  chainOverviewEntry,
  staticValidationEntry,
  backtestEntry,
  walkForwardEntry,
  oosEntry,
  robustnessEntry,
  scoringGateEntry,
];
