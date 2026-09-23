/**
 * Handbook — the candidate lifecycle state machine (cross-cutting topic).
 * Compiled from src/nexus_scalp/research/{lifecycle,models}.py and the
 * registry lifecycle vocabulary rendered by /api/research (model.ts).
 *
 * STATE_IDS mirrors models.py::CandidateLifecycle verbatim; the test
 * re-reads the Python enum and fails on drift.
 */
import type { HandbookEntry } from "./types";
import type { ScoringTranslate } from "./scoring";

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

/** Identity translator (en): English fallback with {var} interpolation only. */
const identity: ScoringTranslate = (_key, fallback, vars) =>
  vars
    ? Object.entries(vars).reduce((s, [k, v]) => s.split(`{${k}}`).join(String(v)), fallback)
    : fallback;

/**
 * Single source of every lifecycle entry: title, subtitle, headings, body,
 * bullets, param meanings and FAQ all go through t() with the English only
 * as fallback; keys live in features/research/i18n.ts under
 * research.hb.lifecycle.<slug>.*. State ids, gate tokens, field names and
 * function names stay verbatim inside the translated prose.
 */
function buildLifecycleEntries(t: ScoringTranslate = identity): HandbookEntry[] {
  const state = (
    name: string,
    title: string,
    head: string,
    deck: string,
    body: string[],
    bullets?: string[],
  ): HandbookEntry => ({
    id: `state/${name}`,
    kind: "state",
    badge: t("research.hb.lifecycle.state_badge", "LIFECYCLE"),
    title,
    subtitle: deck,
    source: "src/nexus_scalp/research/models.py (CandidateLifecycle), lifecycle.py (_TRANSITIONS)",
    seeAlso: ["topic/lifecycle", "gate/SCORING", "topic/discovery"],
    keywords: [name, "state", "transition", "lifecycle"],
    sections: [
      {
        heading: head,
        body,
        ...(bullets ? { bullets } : {}),
      },
    ],
  });

  const stateEntries: HandbookEntry[] = [
    state(
      "DISCOVERED",
      t("research.hb.lifecycle.discovered.title", "Discovered"),
      t("research.hb.lifecycle.discovered.sec1_head", "State DISCOVERED"),
      t(
        "research.hb.lifecycle.discovered.subtitle",
        "A context family crossed the discovery floors and became a named candidate.",
      ),
      [
        t(
          "research.hb.lifecycle.discovered.p1",
          "Discovery groups closed experiences into MEANINGFUL context families using coarse normalized ranges and a context fingerprint, so it produces pattern families rather than one strategy per tiny numerical combination (discovery.py module docstring, spec 10/11).",
        ),
        t(
          "research.hb.lifecycle.discovered.p2",
          "A family qualifies only above the absolute floor (8 samples) and the discovery expectancy floor (+0.10R mean realized R); identity is STRAT-<sha256(fingerprint)[:10].upper()> — deterministic, reproducible from the fingerprint alone.",
        ),
        t(
          "research.hb.lifecycle.discovered.p3",
          "Two tiers exist at this state: STANDARD (>= 20 samples, the standard floor) and SMALL_SAMPLE (8..19 — TASK-4 two-tier discovery: still DISCOVERED, with validation gates independently requiring the evidence floor; no threshold is weakened).",
        ),
        t(
          "research.hb.lifecycle.discovered.p4",
          "From DISCOVERED the machine fans out three ways: INITIAL_TESTING (normal path), EVIDENCE_BUILDING (failed ONLY on sample size with positive expectancy), BACKTESTING (direct), or REJECTED.",
        ),
      ],
      [
        t(
          "research.hb.lifecycle.discovered.b1",
          "Never consults OOS results — discovery/validation boundary is a spec rule (spec 27).",
        ),
        t(
          "research.hb.lifecycle.discovered.b2",
          "discovery_evidence records samples, expectancy_r, win_rate, fingerprint, tier and the exact sample_ids validation must restrict itself to.",
        ),
      ],
    ),
    state(
      "INITIAL_TESTING",
      t("research.hb.lifecycle.initial_testing.title", "Initial Testing"),
      t("research.hb.lifecycle.initial_testing.sec1_head", "State INITIAL_TESTING"),
      t(
        "research.hb.lifecycle.initial_testing.subtitle",
        "First-pass evaluation slot before the candidate commits to the full chain.",
      ),
      [
        t(
          "research.hb.lifecycle.initial_testing.p1",
          "INITIAL_TESTING is the normal hop out of DISCOVERED: the candidate is staged for its first backtest evaluation with the family partition resolved.",
        ),
        t(
          "research.hb.lifecycle.initial_testing.p2",
          "Legal successors: BACKTESTING (proceed), EVIDENCE_BUILDING (sample support too thin — wait for data), or REJECTED.",
        ),
        t(
          "research.hb.lifecycle.initial_testing.p3",
          "It sits in models.py::_INELIGIBLE: nothing in this state can become live under any promotion attempt — require_validation_gate raises for anything short of VALIDATED/SHADOW/ACTIVE.",
        ),
      ],
    ),
    state(
      "EVIDENCE_BUILDING",
      t("research.hb.lifecycle.evidence_building.title", "Evidence Building"),
      t("research.hb.lifecycle.evidence_building.sec1_head", "State EVIDENCE_BUILDING"),
      t(
        "research.hb.lifecycle.evidence_building.subtitle",
        "The second-chance track: positive expectancy but too few trades yet.",
      ),
      [
        t(
          "research.hb.lifecycle.evidence_building.p1",
          "PHASE 25 evidence lifecycle (2026-08-25): DISCOVERED may route here instead of a hard rejection when a candidate fails ONLY on sample size (INSUFFICIENT_TRADES / small-sample floors) while expectancy stayed positive (models.py CandidateLifecycle docstring).",
        ),
        t(
          "research.hb.lifecycle.evidence_building.p2",
          "The candidate is parked, not blessed: evidence states are strictly PRE-validation, they never satisfy the live-trade eligibility gate, and they never weaken any WF/OOS/robustness threshold.",
        ),
        t(
          "research.hb.lifecycle.evidence_building.p3",
          "Legal successors: INITIAL_TESTING or BACKTESTING (data accrued — re-test), or REJECTED. Oscillations between this track and DISCOVERED/BACKTESTING feed debug_intelligence\'s anomaly score.",
        ),
        t(
          "research.hb.lifecycle.evidence_building.p4",
          "Operator reading: an EVIDENCE_BUILDING row means \'waiting for evidence\', not \'failing\'. The failure that parked it was sample size, quoted in the gate\'s failure_reason.",
        ),
      ],
    ),
    state(
      "BACKTESTING",
      t("research.hb.lifecycle.backtesting.title", "Backtesting"),
      t("research.hb.lifecycle.backtesting.sec1_head", "State BACKTESTING"),
      t(
        "research.hb.lifecycle.backtesting.subtitle",
        "The BACKTEST gate is evaluating this candidate right now.",
      ),
      [
        t(
          "research.hb.lifecycle.backtesting.p1",
          "The candidate has entered chain position 2. The gate row for BACKTEST shows PENDING/QUEUED/RUNNING while the deterministic replay executes over the family partition.",
        ),
        t(
          "research.hb.lifecycle.backtesting.p2",
          "Legal successors: VALIDATING (backtest passed — next gate) or REJECTED (statistical failure).",
        ),
        t(
          "research.hb.lifecycle.backtesting.p3",
          "Duration expectations come from the dataset size and fold config, not from this state; a candidate lingering here belongs in the Worker/Queue diagnosis path, not a lifecycle complaint.",
        ),
      ],
    ),
    state(
      "VALIDATING",
      t("research.hb.lifecycle.validating.title", "Validating"),
      t("research.hb.lifecycle.validating.sec1_head", "State VALIDATING"),
      t(
        "research.hb.lifecycle.validating.subtitle",
        "Walk-forward evaluation in progress — fold battery running.",
      ),
      [
        t(
          "research.hb.lifecycle.validating.p1",
          "Named for the validation gate between backtest and OOS: walk-forward folds are being backtested one by one (splitting.py folds with purge 300 s / embargo 60 s).",
        ),
        t(
          "research.hb.lifecycle.validating.p2",
          "Legal successors: OOS_TESTING (>= 50% of folds passed with positive expectancy each) or REJECTED.",
        ),
        t(
          "research.hb.lifecycle.validating.p3",
          "A long dwell here is normal on large families — each fold is a full deterministic backtest; the events timeline shows per-fold progress as the backend records it.",
        ),
      ],
    ),
    state(
      "OOS_TESTING",
      t("research.hb.lifecycle.oos_testing.title", "Oos Testing"),
      t("research.hb.lifecycle.oos_testing.sec1_head", "State OOS_TESTING"),
      t(
        "research.hb.lifecycle.oos_testing.subtitle",
        "The hard out-of-sample gate is evaluating the candidate.",
      ),
      [
        t(
          "research.hb.lifecycle.oos_testing.p1",
          "The candidate faces the gate that has the only REJECT-without-appeal verdict in scoring: OOS failure forces verdict REJECTED (OOS_FAILURE), never merely inconclusive.",
        ),
        t(
          "research.hb.lifecycle.oos_testing.p2",
          "Legal successors: ROBUSTNESS_TESTING (OOS passed) or OOS_READY (staged) or REJECTED.",
        ),
        t(
          "research.hb.lifecycle.oos_testing.p3",
          "The numbers produced here (expectancy floor +0.02R default, degradation ceiling 1.0, bootstrap CI) are the ones quoted verbatim in every later dispute about this candidate.",
        ),
      ],
    ),
    state(
      "OOS_READY",
      t("research.hb.lifecycle.oos_ready.title", "Oos Ready"),
      t("research.hb.lifecycle.oos_ready.sec1_head", "State OOS_READY"),
      t(
        "research.hb.lifecycle.oos_ready.subtitle",
        "OOS evidence recorded; candidate staged before robustness.",
      ),
      [
        t(
          "research.hb.lifecycle.oos_ready.p1",
          "A readiness checkpoint on the OOS branch: the out-of-sample result exists and the machine has not yet consumed it for robustness.",
        ),
        t("research.hb.lifecycle.oos_ready.p2", "Legal successors: ROBUSTNESS_TESTING, or REJECTED."),
        t(
          "research.hb.lifecycle.oos_ready.p3",
          "Listed in _INELIGIBLE: readiness states are bookkeeping, never trade-eligibility — a READY suffix means \'gate completed\', not \'approved\'.",
        ),
      ],
    ),
    state(
      "ROBUSTNESS_TESTING",
      t("research.hb.lifecycle.robustness_testing.title", "Robustness Testing"),
      t("research.hb.lifecycle.robustness_testing.sec1_head", "State ROBUSTNESS_TESTING"),
      t(
        "research.hb.lifecycle.robustness_testing.subtitle",
        "Six stress scenarios are re-pricing the candidate\'s trades.",
      ),
      [
        t(
          "research.hb.lifecycle.robustness_testing.p1",
          "spread +1/+2, slippage +1/+2, latency +50/+150ms run against the same trades; degradation beyond 0.25R from baseline classifies the strategy FRAGILE (robustness.py).",
        ),
        t(
          "research.hb.lifecycle.robustness_testing.p2",
          "Legal successors: ROBUSTNESS_READY, VALIDATED (all evidence in), REJECTED, or DEGRADED.",
        ),
        t(
          "research.hb.lifecycle.robustness_testing.p3",
          "This is the last place the candidate can die for micro-structure fragility before scoring assembles the verdict.",
        ),
      ],
    ),
    state(
      "ROBUSTNESS_READY",
      t("research.hb.lifecycle.robustness_ready.title", "Robustness Ready"),
      t("research.hb.lifecycle.robustness_ready.sec1_head", "State ROBUSTNESS_READY"),
      t(
        "research.hb.lifecycle.robustness_ready.subtitle",
        "Robustness recorded; staged for the scoring verdict.",
      ),
      [
        t(
          "research.hb.lifecycle.robustness_ready.p1",
          "All heavy evidence exists; scoring is the remaining step before VALIDATED or a terminal verdict.",
        ),
        t(
          "research.hb.lifecycle.robustness_ready.p2",
          "Legal successors: VALIDATED, REJECTED, or DEGRADED.",
        ),
        t(
          "research.hb.lifecycle.robustness_ready.p3",
          "Like other *_READY states it is _INELIGIBLE for live — readiness never confers eligibility (models.py::_INELIGIBLE).",
        ),
      ],
    ),
    state(
      "WALK_FORWARD_READY",
      t("research.hb.lifecycle.walk_forward_ready.title", "Walk Forward Ready"),
      t("research.hb.lifecycle.walk_forward_ready.sec1_head", "State WALK_FORWARD_READY"),
      t(
        "research.hb.lifecycle.walk_forward_ready.subtitle",
        "Walk-forward recorded; staged before OOS.",
      ),
      [
        t(
          "research.hb.lifecycle.walk_forward_ready.p1",
          "A checkpoint on the walk-forward branch: fold evidence persisted, OOS not yet consumed.",
        ),
        t(
          "research.hb.lifecycle.walk_forward_ready.p2",
          "Legal successors: OOS_TESTING, VALIDATED (legacy completion path), or REJECTED.",
        ),
        t(
          "research.hb.lifecycle.walk_forward_ready.p3",
          "Enumerated in models.py but reached by specific pipeline paths; the registry list renders whatever the backend records — the UI never smooths state names.",
        ),
      ],
    ),
    state(
      "VALIDATED",
      t("research.hb.lifecycle.validated.title", "Validated"),
      t("research.hb.lifecycle.validated.sec1_head", "State VALIDATED"),
      t(
        "research.hb.lifecycle.validated.subtitle",
        "Every required gate PASSED with evidence — eligible, not yet trading.",
      ),
      [
        t(
          "research.hb.lifecycle.validated.p1",
          "The certification moment: REQUIRED_GATES_FOR_VALIDATION all PASSED with a closed evidence artifact per gate, and scoring\'s verdict chain reached VALIDATED with reason \'All evidence gates passed\'.",
        ),
        t(
          "research.hb.lifecycle.validated.p2",
          "VALIDATED is in the trade-eligible set (require_validation_gate), but NOTHING trades yet — dispatch authority requires ACTIVE, and only SHADOW/VALIDATED candidates may be promoted (approve_for_live).",
        ),
        t(
          "research.hb.lifecycle.validated.p3",
          "Legal successors: SHADOW (the normal next step — observe without risk), REJECTED, or DEGRADED (fresh evidence turned against it).",
        ),
        t(
          "research.hb.lifecycle.validated.p4",
          "Promotion to this state is earned by gates, promotion OUT of it is an operator act with an actor id in lineage.",
        ),
      ],
    ),
    state(
      "SHADOW",
      t("research.hb.lifecycle.shadow.title", "Shadow"),
      t("research.hb.lifecycle.shadow.sec1_head", "State SHADOW"),
      t(
        "research.hb.lifecycle.shadow.subtitle",
        "Running in production observation with zero dispatch authority.",
      ),
      [
        t(
          "research.hb.lifecycle.shadow.p1",
          "Shadow means the candidate\'s decisions are recorded and comparable while real orders remain governed by the incumbent. It is the safety rehearsal before ACTIVE.",
        ),
        t(
          "research.hb.lifecycle.shadow.p2",
          "Legal successors: ACTIVE (operator promotion), DEGRADED (performance deteriorated in observation), or REJECTED.",
        ),
        t(
          "research.hb.lifecycle.shadow.p3",
          "Shadow is a legal promotion source: approve_for_live accepts SHADOW or VALIDATED — the two states from which a human may grant live authority.",
        ),
        t(
          "research.hb.lifecycle.shadow.p4",
          "The model-side shadow systems (e.g. shadow 70D recorder) are a DIFFERENT \'shadow\' — model observation, not strategy lifecycle. Context tells them apart; this entry is the strategy state.",
        ),
      ],
    ),
    state(
      "ACTIVE",
      t("research.hb.lifecycle.active.title", "Active"),
      t("research.hb.lifecycle.active.sec1_head", "State ACTIVE"),
      t(
        "research.hb.lifecycle.active.subtitle",
        "Real dispatch authority — the state money moves from.",
      ),
      [
        t(
          "research.hb.lifecycle.active.p1",
          "ACTIVE is reached only through approve_for_live: a deliberate, operator-gated promotion (spec 21), never automatically — the source\'s own phrasing is \'no Candidate -> Auto Live\'.",
        ),
        t(
          "research.hb.lifecycle.active.p2",
          "In LIVE mode, ACTIVE candidates can produce orders. The confirm modal\'s warning is literal: \'ACTIVE means real dispatch authority in LIVE mode.\'",
        ),
        t(
          "research.hb.lifecycle.active.p3",
          "Legal successors: DEGRADED (evidence or observation deteriorated) or RETIRED (voluntary, orderly exit). There is no silent path out of ACTIVE — every exit is a recorded transition.",
        ),
      ],
    ),
    state(
      "DEGRADED",
      t("research.hb.lifecycle.degraded.title", "Degraded"),
      t("research.hb.lifecycle.degraded.sec1_head", "State DEGRADED"),
      t(
        "research.hb.lifecycle.degraded.subtitle",
        "Was eligible, now suspect — fresh evidence or observation turned against it.",
      ),
      [
        t(
          "research.hb.lifecycle.degraded.p1",
          "DEGRADED is the system\'s honest middle state: the candidate previously earned trust and has since lost it, but the record may still support rehabilitation or retirement.",
        ),
        t(
          "research.hb.lifecycle.degraded.p2",
          "Legal successors: RETIRED, REJECTED, or VALIDATED (evidence re-established — the machine permits rehabilitation precisely because DEGRADED is not a verdict of fraud, only of decline).",
        ),
        t(
          "research.hb.lifecycle.degraded.p3",
          "It is permanently _INELIGIBLE for live while it lasts: _INELIGIBLE includes DEGRADED, so no promotion can sneak it past require_validation_gate.",
        ),
      ],
    ),
    state(
      "REJECTED",
      t("research.hb.lifecycle.rejected.title", "Rejected"),
      t("research.hb.lifecycle.rejected.sec1_head", "State REJECTED"),
      t(
        "research.hb.lifecycle.rejected.subtitle",
        "Terminal: evidence came back against the candidate.",
      ),
      [
        t(
          "research.hb.lifecycle.rejected.p1",
          "REJECTED requires at least one gate FAILED or a terminal research failure — it is never the default for unprocessed candidates (evidence.py module header). The machine gives it NO outgoing transitions: _TRANSITIONS[REJECTED] = {}.",
        ),
        t(
          "research.hb.lifecycle.rejected.p2",
          "Rehabilitation is impossible BY DESIGN for a given candidate identity. New evidence means a NEW discovery — a new strategy_id over a new dataset — not a resurrection of the rejected record.",
        ),
        t(
          "research.hb.lifecycle.rejected.p3",
          "The distinction that matters: candidates that failed ONLY on sample size route to EVIDENCE_BUILDING instead (PHASE 25), so REJECTED here means more than \'not enough data yet\'.",
        ),
      ],
    ),
    state(
      "RETIRED",
      t("research.hb.lifecycle.retired.title", "Retired"),
      t("research.hb.lifecycle.retired.sec1_head", "State RETIRED"),
      t(
        "research.hb.lifecycle.retired.subtitle",
        "Terminal: an orderly, recorded exit from service.",
      ),
      [
        t(
          "research.hb.lifecycle.retired.p1",
          "RETIRED is the voluntary/final state for candidates that served or were stood down without a statistical condemnation — ACTIVE may retire, DEGRADED may retire.",
        ),
        t(
          "research.hb.lifecycle.retired.p2",
          "No outgoing transitions: _TRANSITIONS[RETIRED] = {}. Retirement is a bookkeeping finality, not a rejection.",
        ),
        t(
          "research.hb.lifecycle.retired.p3",
          "Registry rows keep their history; retirement never deletes evidence (immutable stores are append-only — hygiene/retention governs physical rows, not semantics).",
        ),
      ],
    ),
  ];

  const transitionsEntry: HandbookEntry = {
    id: "topic/lifecycle",
    kind: "topic",
    badge: t("research.hb.lifecycle.lifecycle.badge", "16 STATES"),
    title: t(
      "research.hb.lifecycle.lifecycle.title",
      "Lifecycle state machine — transitions, gates, promotion",
    ),
    subtitle: t(
      "research.hb.lifecycle.lifecycle.subtitle",
      "Every move legal, every move refused, and why \'skipping validation\' raises an error.",
    ),
    source: "src/nexus_scalp/research/lifecycle.py, models.py",
    seeAlso: ["gate/SCORING", "topic/discovery", "topic/operations"],
    keywords: ["transition", "approve_for_live", "require_validation_gate", "eligibility", "promotion"],
    sections: [
      {
        heading: t("research.hb.lifecycle.lifecycle.sec1_head", "The adjacency map"),
        body: [
          t(
            "research.hb.lifecycle.lifecycle.sec1_p1",
            "lifecycle.py::_TRANSITIONS is a dict of sets — the COMPLETE list of legal moves, not a suggestion. transition() raises LifecycleError(\'Illegal lifecycle transition: X -> Y\') for anything off the map, which is why the UI can never be talked into a skip.",
          ),
          t(
            "research.hb.lifecycle.lifecycle.sec1_p2",
            "Main spine: DISCOVERED -> {INITIAL_TESTING | EVIDENCE_BUILDING | BACKTESTING} -> BACKTESTING -> VALIDATING -> OOS_TESTING -> {ROBUSTNESS_TESTING | OOS_READY} -> ROBUSTNESS_TESTING -> {ROBUSTNESS_READY | VALIDATED} -> VALIDATED -> SHADOW -> ACTIVE.",
          ),
          t(
            "research.hb.lifecycle.lifecycle.sec1_p3",
            "Failure exits hang off every stage toward REJECTED; DEGRADED enters from VALIDATED/SHADOW/ROBUSTNESS*; ACTIVE exits only to DEGRADED/RETIRED; REJECTED and RETIRED have empty successor sets.",
          ),
          t(
            "research.hb.lifecycle.lifecycle.sec1_p4",
            "The concept summary in the module header: DISCOVERED -> BACKTESTING -> VALIDATING -> OOS_TESTING -> ROBUSTNESS_TESTING -> VALIDATED -> SHADOW -> ACTIVE, with failure paths REJECTED, DEGRADED, RETIRED. A strategy MUST NOT skip validation, and CANNOT reach ACTIVE without passing all gates AND explicit operator approval.",
          ),
        ],
      },
      {
        heading: t("research.hb.lifecycle.lifecycle.sec2_head", "Two gates guard the money"),
        body: [
          t(
            "research.hb.lifecycle.lifecycle.sec2_p1",
            "approve_for_live(candidate): ONLY a SHADOW (or previously VALIDATED) candidate may become ACTIVE, and never automatically. Anything else raises LifecycleError(\'Cannot promote X to ACTIVE: must be SHADOW/VALIDATED first\').",
          ),
          t(
            "research.hb.lifecycle.lifecycle.sec2_p2",
            "require_validation_gate(lifecycle): trade-eligibility requires VALIDATED, SHADOW or ACTIVE. DISCOVERED/BACKTESTING/REJECTED/RETIRED/DEGRADED and every *_READY/EVIDENCE state raise \'not validation-gated for live use\'.",
          ),
          t(
            "research.hb.lifecycle.lifecycle.sec2_p3",
            "Together they encode the spec\'s central promise: research cannot spend money, and only a human can authorize research to try.",
          ),
        ],
      },
      {
        heading: t("research.hb.lifecycle.lifecycle.sec3_head", "Eligibility sets, verbatim"),
        body: [
          t(
            "research.hb.lifecycle.lifecycle.sec3_p1",
            "TRADE_ELIGIBLE = {VALIDATED, SHADOW, ACTIVE} — require_validation_gate\'s allow-list.",
          ),
          t(
            "research.hb.lifecycle.lifecycle.sec3_p2",
            "INELIGIBLE_FOR_LIVE (models.py::_INELIGIBLE) additionally zeroes out REJECTED, RETIRED, DEGRADED, INITIAL_TESTING, EVIDENCE_BUILDING, WALK_FORWARD_READY, OOS_READY and ROBUSTNESS_READY — the states most likely to be mistaken for \'almost there\'.",
          ),
          t(
            "research.hb.lifecycle.lifecycle.sec3_p3",
            "TERMINAL = {REJECTED, RETIRED} — empty successor sets in _TRANSITIONS.",
          ),
        ],
      },
      {
        heading: t(
          "research.hb.lifecycle.lifecycle.sec4_head",
          "Registry lifecycle vocabulary vs the state machine",
        ),
        body: [
          t(
            "research.hb.lifecycle.lifecycle.sec4_p1",
            "The registry\'s by_lifecycle counters (what the Lifecycle census chips filter on) use gate-progress names such as BACKTEST_RUN, WALK_FORWARD_TESTED/PASSED, OOS_TESTED/PASSED, ROBUSTNESS_TESTED/PASSED and SCORING_COMPLETED alongside the shared states DISCOVERED/VALIDATED/SHADOW/ACTIVE/REJECTED/DEGRADED/RETIRED.",
          ),
          t(
            "research.hb.lifecycle.lifecycle.sec4_p2",
            "Those are progress labels over the same evidence — which gate a registry row is parked at — while CandidateLifecycle is the machine\'s formal state. The handbook documents both because the UI shows both; the backend is the only writer of either.",
          ),
          t(
            "research.hb.lifecycle.lifecycle.sec4_p3",
            "If a chip shows a state this page does not list, the backend vocabulary has extended — treat the backend\'s response as truth and the docs as lagging (the test pins the states it knows; new enum members fail loudly, prompting a docs update rather than silent drift).",
          ),
        ],
      },
      {
        heading: t("research.hb.lifecycle.lifecycle.sec5_head", "Oscillation and anomaly scoring"),
        body: [
          t(
            "research.hb.lifecycle.lifecycle.sec5_p1",
            "debug_intelligence.py scores lifecycle churn: transitions counted, failures counted, and \'oscillations\' — sequences that bounce back into DISCOVERED/BACKTESTING after leaving them — weighted 0.4 of anomaly_score.",
          ),
          t(
            "research.hb.lifecycle.lifecycle.sec5_p2",
            "An oscillating candidate is a discovery threshold tuning signal, not a candidate defect: the family keeps almost-qualifying and failing, which points at the floors/expectancy settings rather than the strategy itself.",
          ),
        ],
      },
    ],
    params: [
      {
        name: "approve_for_live sources",
        value: "SHADOW | VALIDATED -> ACTIVE",
        meaning: t(
          "research.hb.lifecycle.lifecycle.param_sources",
          "The only legal promotion path into live authority; always operator-triggered.",
        ),
        ref: "lifecycle.py::approve_for_live",
      },
      {
        name: "trade-eligible set",
        value: "VALIDATED, SHADOW, ACTIVE",
        meaning: t(
          "research.hb.lifecycle.lifecycle.param_eligible",
          "require_validation_gate\'s allow-list; everything else raises.",
        ),
        ref: "lifecycle.py::require_validation_gate",
      },
      {
        name: "terminal states",
        value: "REJECTED, RETIRED (empty successor sets)",
        meaning: t(
          "research.hb.lifecycle.lifecycle.param_terminal",
          "No transition out — new evidence means a new candidate identity.",
        ),
        ref: "lifecycle.py::_TRANSITIONS",
      },
      {
        name: "state count",
        value: "16",
        meaning: t(
          "research.hb.lifecycle.lifecycle.param_count",
          "CandidateLifecycle enum members in models.py.",
        ),
        ref: "models.py::CandidateLifecycle",
      },
    ],
    faq: [
      {
        q: t(
          "research.hb.lifecycle.lifecycle.faq1_q",
          "The UI shows WALK_FORWARD_PASSED but this page calls it a \'gate-progress label\'. Which is real?",
        ),
        a: t(
          "research.hb.lifecycle.lifecycle.faq1_a",
          "Both, at different layers: the registry row\'s lifecycle_state records how far the EVIDENCE got (progress label), while CandidateLifecycle is the formal machine state. The backend writes both; neither is inferred by the UI.",
        ),
      },
      {
        q: t(
          "research.hb.lifecycle.lifecycle.faq2_q",
          "Can two agents promote the same strategy concurrently?",
        ),
        a: t(
          "research.hb.lifecycle.lifecycle.faq2_a",
          "Promotion is a registry state machine transition — an illegal jump raises regardless of who attempts it, and concurrent writers serialize through the registry\'s persistence. The actor id in lineage records WHO asked, not who succeeded; the backend response decides.",
        ),
      },
      {
        q: t(
          "research.hb.lifecycle.lifecycle.faq3_q",
          "Why can DEGRADED go back to VALIDATED but REJECTED cannot go anywhere?",
        ),
        a: t(
          "research.hb.lifecycle.lifecycle.faq3_a",
          "DEGRADED means trust was lost to decline and the evidence may be re-established (decline is re-testable). REJECTED means the evidence itself came back against the candidate — rehabilitating it would rewrite what the gates measured, so the record stands and new evidence requires a new identity.",
        ),
      },
    ],
  };

  return [transitionsEntry, ...stateEntries];
}

/** Canonical English entries (identity) — TOC, search and tests consume these. */
export const lifecycleEntries: HandbookEntry[] = buildLifecycleEntries();

/**
 * Translator-aware copy: call during render with the store's current t();
 * the result changes with the language — never cache it outside render.
 * Yields every entry lifecycleEntries yields, same ids, translated prose.
 */
export function lifecycleTranslated(t?: ScoringTranslate): HandbookEntry[] {
  return buildLifecycleEntries(t);
}
