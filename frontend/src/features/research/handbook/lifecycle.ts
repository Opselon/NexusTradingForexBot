/**
 * Handbook — the candidate lifecycle state machine (cross-cutting topic).
 * Compiled from src/nexus_scalp/research/{lifecycle,models}.py and the
 * registry lifecycle vocabulary rendered by /api/research (model.ts).
 *
 * STATE_IDS mirrors models.py::CandidateLifecycle verbatim; the test
 * re-reads the Python enum and fails on drift.
 */
import type { HandbookEntry } from "./types";

/** Mirrors models.py::CandidateLifecycle (all 16 states, source order). */
export const STATE_IDS = [
  "DISCOVERED",
  "INITIAL_TESTING",
  "EVIDENCE_BUILDING",
  "WALK_FORWARD_READY",
  "OOS_READY",
  "ROBUSTNESS_READY",
  "BACKTESTING",
  "VALIDATING",
  "OOS_TESTING",
  "ROBUSTNESS_TESTING",
  "VALIDATED",
  "SHADOW",
  "ACTIVE",
  "REJECTED",
  "DEGRADED",
  "RETIRED",
] as const;

/** Terminal states: no outgoing transitions in lifecycle.py::_TRANSITIONS. */
export const TERMINAL_STATES = ["REJECTED", "RETIRED"] as const;

/** Trade-eligible set — mirrors lifecycle.py::require_validation_gate. */
export const TRADE_ELIGIBLE = ["VALIDATED", "SHADOW", "ACTIVE"] as const;

/** States that may never become live — mirrors models.py::_INELIGIBLE. */
export const INELIGIBLE_FOR_LIVE = [
  "REJECTED",
  "RETIRED",
  "DEGRADED",
  "INITIAL_TESTING",
  "EVIDENCE_BUILDING",
  "WALK_FORWARD_READY",
  "OOS_READY",
  "ROBUSTNESS_READY",
] as const;

const state = (
  name: string,
  deck: string,
  body: string[],
  bullets?: string[],
): HandbookEntry => ({
  id: `state/${name}`,
  kind: "state",
  badge: "LIFECYCLE",
  title: name.replace(/_/g, " ").toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase()),
  subtitle: deck,
  source: "src/nexus_scalp/research/models.py (CandidateLifecycle), lifecycle.py (_TRANSITIONS)",
  seeAlso: ["topic/lifecycle", "gate/SCORING", "topic/discovery"],
  keywords: [name, "state", "transition", "lifecycle"],
  sections: [
    {
      heading: `State ${name}`,
      body,
      ...(bullets ? { bullets } : {}),
    },
  ],
});

const stateEntries: HandbookEntry[] = [
  state(
    "DISCOVERED",
    "A context family crossed the discovery floors and became a named candidate.",
    [
      "Discovery groups closed experiences into MEANINGFUL context families using coarse normalized ranges and a context fingerprint, so it produces pattern families rather than one strategy per tiny numerical combination (discovery.py module docstring, spec 10/11).",
      "A family qualifies only above the absolute floor (8 samples) and the discovery expectancy floor (+0.10R mean realized R); identity is STRAT-<sha256(fingerprint)[:10].upper()> — deterministic, reproducible from the fingerprint alone.",
      "Two tiers exist at this state: STANDARD (>= 20 samples, the standard floor) and SMALL_SAMPLE (8..19 — TASK-4 two-tier discovery: still DISCOVERED, with validation gates independently requiring the evidence floor; no threshold is weakened).",
      "From DISCOVERED the machine fans out three ways: INITIAL_TESTING (normal path), EVIDENCE_BUILDING (failed ONLY on sample size with positive expectancy), BACKTESTING (direct), or REJECTED.",
    ],
    [
      "Never consults OOS results — discovery/validation boundary is a spec rule (spec 27).",
      "discovery_evidence records samples, expectancy_r, win_rate, fingerprint, tier and the exact sample_ids validation must restrict itself to.",
    ],
  ),
  state(
    "INITIAL_TESTING",
    "First-pass evaluation slot before the candidate commits to the full chain.",
    [
      "INITIAL_TESTING is the normal hop out of DISCOVERED: the candidate is staged for its first backtest evaluation with the family partition resolved.",
      "Legal successors: BACKTESTING (proceed), EVIDENCE_BUILDING (sample support too thin — wait for data), or REJECTED.",
      "It sits in models.py::_INELIGIBLE: nothing in this state can become live under any promotion attempt — require_validation_gate raises for anything short of VALIDATED/SHADOW/ACTIVE.",
    ],
  ),
  state(
    "EVIDENCE_BUILDING",
    "The second-chance track: positive expectancy but too few trades yet.",
    [
      "PHASE 25 evidence lifecycle (2026-08-25): DISCOVERED may route here instead of a hard rejection when a candidate fails ONLY on sample size (INSUFFICIENT_TRADES / small-sample floors) while expectancy stayed positive (models.py CandidateLifecycle docstring).",
      "The candidate is parked, not blessed: evidence states are strictly PRE-validation, they never satisfy the live-trade eligibility gate, and they never weaken any WF/OOS/robustness threshold.",
      "Legal successors: INITIAL_TESTING or BACKTESTING (data accrued — re-test), or REJECTED. Oscillations between this track and DISCOVERED/BACKTESTING feed debug_intelligence's anomaly score.",
      "Operator reading: an EVIDENCE_BUILDING row means 'waiting for evidence', not 'failing'. The failure that parked it was sample size, quoted in the gate's failure_reason.",
    ],
  ),
  state(
    "BACKTESTING",
    "The BACKTEST gate is evaluating this candidate right now.",
    [
      "The candidate has entered chain position 2. The gate row for BACKTEST shows PENDING/QUEUED/RUNNING while the deterministic replay executes over the family partition.",
      "Legal successors: VALIDATING (backtest passed — next gate) or REJECTED (statistical failure).",
      "Duration expectations come from the dataset size and fold config, not from this state; a candidate lingering here belongs in the Worker/Queue diagnosis path, not a lifecycle complaint.",
    ],
  ),
  state(
    "VALIDATING",
    "Walk-forward evaluation in progress — fold battery running.",
    [
      "Named for the validation gate between backtest and OOS: walk-forward folds are being backtested one by one (splitting.py folds with purge 300 s / embargo 60 s).",
      "Legal successors: OOS_TESTING (>= 50% of folds passed with positive expectancy each) or REJECTED.",
      "A long dwell here is normal on large families — each fold is a full deterministic backtest; the events timeline shows per-fold progress as the backend records it.",
    ],
  ),
  state(
    "OOS_TESTING",
    "The hard out-of-sample gate is evaluating the candidate.",
    [
      "The candidate faces the gate that has the only REJECT-without-appeal verdict in scoring: OOS failure forces verdict REJECTED (OOS_FAILURE), never merely inconclusive.",
      "Legal successors: ROBUSTNESS_TESTING (OOS passed) or OOS_READY (staged) or REJECTED.",
      "The numbers produced here (expectancy floor +0.02R default, degradation ceiling 1.0, bootstrap CI) are the ones quoted verbatim in every later dispute about this candidate.",
    ],
  ),
  state(
    "OOS_READY",
    "OOS evidence recorded; candidate staged before robustness.",
    [
      "A readiness checkpoint on the OOS branch: the out-of-sample result exists and the machine has not yet consumed it for robustness.",
      "Legal successors: ROBUSTNESS_TESTING, or REJECTED.",
      "Listed in _INELIGIBLE: readiness states are bookkeeping, never trade-eligibility — a READY suffix means 'gate completed', not 'approved'.",
    ],
  ),
  state(
    "ROBUSTNESS_TESTING",
    "Six stress scenarios are re-pricing the candidate's trades.",
    [
      "spread +1/+2, slippage +1/+2, latency +50/+150ms run against the same trades; degradation beyond 0.25R from baseline classifies the strategy FRAGILE (robustness.py).",
      "Legal successors: ROBUSTNESS_READY, VALIDATED (all evidence in), REJECTED, or DEGRADED.",
      "This is the last place the candidate can die for micro-structure fragility before scoring assembles the verdict.",
    ],
  ),
  state(
    "ROBUSTNESS_READY",
    "Robustness recorded; staged for the scoring verdict.",
    [
      "All heavy evidence exists; scoring is the remaining step before VALIDATED or a terminal verdict.",
      "Legal successors: VALIDATED, REJECTED, or DEGRADED.",
      "Like other *_READY states it is _INELIGIBLE for live — readiness never confers eligibility (models.py::_INELIGIBLE).",
    ],
  ),
  state(
    "WALK_FORWARD_READY",
    "Walk-forward recorded; staged before OOS.",
    [
      "A checkpoint on the walk-forward branch: fold evidence persisted, OOS not yet consumed.",
      "Legal successors: OOS_TESTING, VALIDATED (legacy completion path), or REJECTED.",
      "Enumerated in models.py but reached by specific pipeline paths; the registry list renders whatever the backend records — the UI never smooths state names.",
    ],
  ),
  state(
    "VALIDATED",
    "Every required gate PASSED with evidence — eligible, not yet trading.",
    [
      "The certification moment: REQUIRED_GATES_FOR_VALIDATION all PASSED with a closed evidence artifact per gate, and scoring's verdict chain reached VALIDATED with reason 'All evidence gates passed'.",
      "VALIDATED is in the trade-eligible set (require_validation_gate), but NOTHING trades yet — dispatch authority requires ACTIVE, and only SHADOW/VALIDATED candidates may be promoted (approve_for_live).",
      "Legal successors: SHADOW (the normal next step — observe without risk), REJECTED, or DEGRADED (fresh evidence turned against it).",
      "Promotion to this state is earned by gates, promotion OUT of it is an operator act with an actor id in lineage.",
    ],
  ),
  state(
    "SHADOW",
    "Running in production observation with zero dispatch authority.",
    [
      "Shadow means the candidate's decisions are recorded and comparable while real orders remain governed by the incumbent. It is the safety rehearsal before ACTIVE.",
      "Legal successors: ACTIVE (operator promotion), DEGRADED (performance deteriorated in observation), or REJECTED.",
      "Shadow is a legal promotion source: approve_for_live accepts SHADOW or VALIDATED — the two states from which a human may grant live authority.",
      "The model-side shadow systems (e.g. shadow 70D recorder) are a DIFFERENT 'shadow' — model observation, not strategy lifecycle. Context tells them apart; this entry is the strategy state.",
    ],
  ),
  state(
    "ACTIVE",
    "Real dispatch authority — the state money moves from.",
    [
      "ACTIVE is reached only through approve_for_live: a deliberate, operator-gated promotion (spec 21), never automatically — the source's own phrasing is 'no Candidate -> Auto Live'.",
      "In LIVE mode, ACTIVE candidates can produce orders. The confirm modal's warning is literal: 'ACTIVE means real dispatch authority in LIVE mode.'",
      "Legal successors: DEGRADED (evidence or observation deteriorated) or RETIRED (voluntary, orderly exit). There is no silent path out of ACTIVE — every exit is a recorded transition.",
    ],
  ),
  state(
    "DEGRADED",
    "Was eligible, now suspect — fresh evidence or observation turned against it.",
    [
      "DEGRADED is the system's honest middle state: the candidate previously earned trust and has since lost it, but the record may still support rehabilitation or retirement.",
      "Legal successors: RETIRED, REJECTED, or VALIDATED (evidence re-established — the machine permits rehabilitation precisely because DEGRADED is not a verdict of fraud, only of decline).",
      "It is permanently _INELIGIBLE for live while it lasts: _INELIGIBLE includes DEGRADED, so no promotion can sneak it past require_validation_gate.",
    ],
  ),
  state(
    "REJECTED",
    "Terminal: evidence came back against the candidate.",
    [
      "REJECTED requires at least one gate FAILED or a terminal research failure — it is never the default for unprocessed candidates (evidence.py module header). The machine gives it NO outgoing transitions: _TRANSITIONS[REJECTED] = {}.",
      "Rehabilitation is impossible BY DESIGN for a given candidate identity. New evidence means a NEW discovery — a new strategy_id over a new dataset — not a resurrection of the rejected record.",
      "The distinction that matters: candidates that failed ONLY on sample size route to EVIDENCE_BUILDING instead (PHASE 25), so REJECTED here means more than 'not enough data yet'.",
    ],
  ),
  state(
    "RETIRED",
    "Terminal: an orderly, recorded exit from service.",
    [
      "RETIRED is the voluntary/final state for candidates that served or were stood down without a statistical condemnation — ACTIVE may retire, DEGRADED may retire.",
      "No outgoing transitions: _TRANSITIONS[RETIRED] = {}. Retirement is a bookkeeping finality, not a rejection.",
      "Registry rows keep their history; retirement never deletes evidence (immutable stores are append-only — hygiene/retention governs physical rows, not semantics).",
    ],
  ),
];

const transitionsEntry: HandbookEntry = {
  id: "topic/lifecycle",
  kind: "topic",
  badge: "16 STATES",
  title: "Lifecycle state machine — transitions, gates, promotion",
  subtitle: "Every move legal, every move refused, and why 'skipping validation' raises an error.",
  source: "src/nexus_scalp/research/lifecycle.py, models.py",
  seeAlso: ["gate/SCORING", "topic/discovery", "topic/operations"],
  keywords: ["transition", "approve_for_live", "require_validation_gate", "eligibility", "promotion"],
  sections: [
    {
      heading: "The adjacency map",
      body: [
        "lifecycle.py::_TRANSITIONS is a dict of sets — the COMPLETE list of legal moves, not a suggestion. transition() raises LifecycleError('Illegal lifecycle transition: X -> Y') for anything off the map, which is why the UI can never be talked into a skip.",
        "Main spine: DISCOVERED -> {INITIAL_TESTING | EVIDENCE_BUILDING | BACKTESTING} -> BACKTESTING -> VALIDATING -> OOS_TESTING -> {ROBUSTNESS_TESTING | OOS_READY} -> ROBUSTNESS_TESTING -> {ROBUSTNESS_READY | VALIDATED} -> VALIDATED -> SHADOW -> ACTIVE.",
        "Failure exits hang off every stage toward REJECTED; DEGRADED enters from VALIDATED/SHADOW/ROBUSTNESS*; ACTIVE exits only to DEGRADED/RETIRED; REJECTED and RETIRED have empty successor sets.",
        "The concept summary in the module header: DISCOVERED -> BACKTESTING -> VALIDATING -> OOS_TESTING -> ROBUSTNESS_TESTING -> VALIDATED -> SHADOW -> ACTIVE, with failure paths REJECTED, DEGRADED, RETIRED. A strategy MUST NOT skip validation, and CANNOT reach ACTIVE without passing all gates AND explicit operator approval.",
      ],
    },
    {
      heading: "Two gates guard the money",
      body: [
        "approve_for_live(candidate): ONLY a SHADOW (or previously VALIDATED) candidate may become ACTIVE, and never automatically. Anything else raises LifecycleError('Cannot promote X to ACTIVE: must be SHADOW/VALIDATED first').",
        "require_validation_gate(lifecycle): trade-eligibility requires VALIDATED, SHADOW or ACTIVE. DISCOVERED/BACKTESTING/REJECTED/RETIRED/DEGRADED and every *_READY/EVIDENCE state raise 'not validation-gated for live use'.",
        "Together they encode the spec's central promise: research cannot spend money, and only a human can authorize research to try.",
      ],
    },
    {
      heading: "Eligibility sets, verbatim",
      body: [
        "TRADE_ELIGIBLE = {VALIDATED, SHADOW, ACTIVE} — require_validation_gate's allow-list.",
        "INELIGIBLE_FOR_LIVE (models.py::_INELIGIBLE) additionally zeroes out REJECTED, RETIRED, DEGRADED, INITIAL_TESTING, EVIDENCE_BUILDING, WALK_FORWARD_READY, OOS_READY and ROBUSTNESS_READY — the states most likely to be mistaken for 'almost there'.",
        "TERMINAL = {REJECTED, RETIRED} — empty successor sets in _TRANSITIONS.",
      ],
    },
    {
      heading: "Registry lifecycle vocabulary vs the state machine",
      body: [
        "The registry's by_lifecycle counters (what the Lifecycle census chips filter on) use gate-progress names such as BACKTEST_RUN, WALK_FORWARD_TESTED/PASSED, OOS_TESTED/PASSED, ROBUSTNESS_TESTED/PASSED and SCORING_COMPLETED alongside the shared states DISCOVERED/VALIDATED/SHADOW/ACTIVE/REJECTED/DEGRADED/RETIRED.",
        "Those are progress labels over the same evidence — which gate a registry row is parked at — while CandidateLifecycle is the machine's formal state. The handbook documents both because the UI shows both; the backend is the only writer of either.",
        "If a chip shows a state this page does not list, the backend vocabulary has extended — treat the backend's response as truth and the docs as lagging (the test pins the states it knows; new enum members fail loudly, prompting a docs update rather than silent drift).",
      ],
    },
    {
      heading: "Oscillation and anomaly scoring",
      body: [
        "debug_intelligence.py scores lifecycle churn: transitions counted, failures counted, and 'oscillations' — sequences that bounce back into DISCOVERED/BACKTESTING after leaving them — weighted 0.4 of anomaly_score.",
        "An oscillating candidate is a discovery threshold tuning signal, not a candidate defect: the family keeps almost-qualifying and failing, which points at the floors/expectancy settings rather than the strategy itself.",
      ],
    },
  ],
  params: [
    {
      name: "approve_for_live sources",
      value: "SHADOW | VALIDATED -> ACTIVE",
      meaning: "The only legal promotion path into live authority; always operator-triggered.",
      ref: "lifecycle.py::approve_for_live",
    },
    {
      name: "trade-eligible set",
      value: "VALIDATED, SHADOW, ACTIVE",
      meaning: "require_validation_gate's allow-list; everything else raises.",
      ref: "lifecycle.py::require_validation_gate",
    },
    {
      name: "terminal states",
      value: "REJECTED, RETIRED (empty successor sets)",
      meaning: "No transition out — new evidence means a new candidate identity.",
      ref: "lifecycle.py::_TRANSITIONS",
    },
    {
      name: "state count",
      value: "16",
      meaning: "CandidateLifecycle enum members in models.py.",
      ref: "models.py::CandidateLifecycle",
    },
  ],
  faq: [
    {
      q: "The UI shows WALK_FORWARD_PASSED but this page calls it a 'gate-progress label'. Which is real?",
      a: "Both, at different layers: the registry row's lifecycle_state records how far the EVIDENCE got (progress label), while CandidateLifecycle is the formal machine state. The backend writes both; neither is inferred by the UI.",
    },
    {
      q: "Can two agents promote the same strategy concurrently?",
      a: "Promotion is a registry state machine transition — an illegal jump raises regardless of who attempts it, and concurrent writers serialize through the registry's persistence. The actor id in lineage records WHO asked, not who succeeded; the backend response decides.",
    },
    {
      q: "Why can DEGRADED go back to VALIDATED but REJECTED cannot go anywhere?",
      a: "DEGRADED means trust was lost to decline and the evidence may be re-established (decline is re-testable). REJECTED means the evidence itself came back against the candidate — rehabilitating it would rewrite what the gates measured, so the record stands and new evidence requires a new identity.",
    },
  ],
};

export const lifecycleEntries: HandbookEntry[] = [transitionsEntry, ...stateEntries];
