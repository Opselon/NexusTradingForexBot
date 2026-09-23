/**
 * Research handbook — the backend constants the UI quotes (no prose).
 *
 * PURPOSE: the small constant surface that non-handbook code needs without
 * pulling the handbook's prose corpus — the gate chain for the ResearchPage
 * hero pipeline map, plus gate-status / failure-class vocabularies for badges.
 *
 * OWNER: lane D (features/research).
 *
 * CONSUMES: nothing — only literals. This file is deliberately prose-free:
 * importing it must not transitively import any HandbookEntry module.
 *
 * PROVIDES: GATE_CHAIN (canonical order), REQUIRED_FOR_VALIDATED,
 * GATE_STATUSES, TERMINAL_GATE_STATUSES, FAILURE_CLASSES.
 *
 * INVARIANTS: these mirror the Python sources verbatim, pinned by tests/js/
 * research_handbook.test.js (chain order + statuses against evidence.py).
 * GATE_CHAIN here is the SAME array gates/index.ts re-exports — do not fork it;
 * gates/index.ts now re-exports from here so there is one definition.
 *
 * EXTEND: add a new constant here only if non-handbook code needs it and it
 * carries no prose; entry content belongs in a leaf entry module.
 */

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
