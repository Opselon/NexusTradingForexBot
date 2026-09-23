/**
 * Handbook — operations: commands, availability, worker, queue, retention.
 * Compiled from src/nexus_scalp/research/{availability,commands,pipeline}.py,
 * src/nexus_scalp/web/debug_research_routes.py, observability.py health rules.
 */
import type { HandbookEntry } from "./types";
import type { ScoringTranslate } from "./scoring";

/** Identity translator (en): English fallback with {var} interpolation only. */
const identity: ScoringTranslate = (_key, fallback, vars) =>
  vars
    ? Object.entries(vars).reduce((s, [k, v]) => s.split(`{${k}}`).join(String(v)), fallback)
    : fallback;

/** Canonical English entry (identity) — TOC, search and tests consume this. */
function buildOperationsEntry(t: ScoringTranslate = identity): HandbookEntry {
  return {
    id: "topic/operations",
    kind: "topic",
    badge: t("research.hb.operations.operations.badge", "COMMANDS · WORKER · QUEUE"),
    title: t("research.hb.operations.operations.title", "Operating the research surface"),
    subtitle: t(
      "research.hb.operations.operations.subtitle",
      "What each command actually does, how unavailability is reported, how worker health and the queue are classified.",
    ),
    source:
      "src/nexus_scalp/web/debug_research_routes.py, research/{availability,commands,pipeline,observability}.py",
    seeAlso: ["topic/lifecycle", "topic/pipeline", "topic/evidence"],
    keywords: ["command", "discover", "validate", "cancel", "self heal", "retry", "repair", "recover", "promote", "availability", "worker", "queue", "health", "retention", "diagnostics"],
    sections: [
      {
        heading: t("research.hb.operations.operations.sec1_head", "The command surface"),
        body: [
          t(
            "research.hb.operations.operations.sec1_p1",
            "Commands hit POST /api/research/<command> (debug_research_routes.py); the UI\'s CommandBar is the thin, always-visible entry (ResearchCommands.tsx header). Every button delegates to the backend — none synthesizes its own success.",
          ),
          t(
            "research.hb.operations.operations.sec1_p2",
            "discover — runs candidate discovery over the current dataset: floors, expectancy, deterministic ids, registry upsert. Additive only: cannot demote or delete existing candidates.",
          ),
          t(
            "research.hb.operations.operations.sec1_p3",
            "validate — runs the full gate chain (pipeline.validate) for one candidate over its family partition (sample_ids); the matrix rows\' Validate buttons POST here with strategy_id + dataset_id.",
          ),
          t(
            "research.hb.operations.operations.sec1_p4",
            "cancel — requests CANCELLATION of queued/running research work; terminal FAILED and CANCELLED work is never re-run (commands.py: CANCELLED/FAILED are terminal).",
          ),
          t(
            "research.hb.operations.operations.sec1_p5",
            "self-heal — rebuilds derived strategy intelligence (registry/cache) from immutable experience stores; responses may report reason ENGINE_UNAVAILABLE when the engine is down — the cache never outranks the store.",
          ),
          t(
            "research.hb.operations.operations.sec1_p6",
            "retry-gate — re-attempts a FAILED gate, but ONLY for retryable failure classes: TECHNICAL and DATA. RESEARCH-class failures are never retried — re-running the same data through the same science cannot un-fail them.",
          ),
          t(
            "research.hb.operations.operations.sec1_p7",
            "repair-outcomes — re-derives economic outcomes that are missing/malformed, recording outcome_lineage + repair_state (never fabricating broker data); comes in dry-run and write forms.",
          ),
          t(
            "research.hb.operations.operations.sec1_p8",
            "recover-missing-outcomes — recovers closed outcomes absent from the economics pipeline; same lineage discipline, also dry-run first.",
          ),
          t(
            "research.hb.operations.operations.sec1_p9",
            "promote — lifecycle promotion through the state machine (approve_for_live rules apply): requires an actor, records lineage, refuses illegal jumps with LifecycleError.",
          ),
        ],
      },
      {
        heading: t("research.hb.operations.operations.sec2_head", "Availability contract"),
        body: [
          t(
            "research.hb.operations.operations.sec2_p1",
            "research/availability.py: availability is ALWAYS structured — {\'available\': false, \'reason\': <why>} when blocked, never an empty list pretending readiness (API.md).",
          ),
          t(
            "research.hb.operations.operations.sec2_p2",
            "Reasons are specific (e.g. \'dataset unavailable\', engine down, dataset absent) so the UI can distinguish \'nothing yet\' from \'not runnable right now\' — the difference between patience and intervention.",
          ),
          t(
            "research.hb.operations.operations.sec2_p3",
            "GET /api/research/availability powers the UI\'s disabled-state hints: a blocked command shows the backend\'s reason verbatim instead of failing on click.",
          ),
        ],
      },
      {
        heading: t("research.hb.operations.operations.sec3_head", "Worker health classification"),
        body: [
          t(
            "research.hb.operations.operations.sec3_p1",
            "observability.py classifies worker health deterministically (spec 16 counters), in this precedence (debug_research_routes.py):",
          ),
        ],
        bullets: [
          t("research.hb.operations.operations.sec3_b1", "status == \'FAILED\' -> FAILED."),
          t("research.hb.operations.operations.sec3_b2", "status != \'RUNNING\' -> IDLE."),
          t(
            "research.hb.operations.operations.sec3_b3",
            "heartbeat age > 900 s (15 min) -> STUCK (10 min = DEGRADED, 15 = STUCK per spec 16).",
          ),
          t("research.hb.operations.operations.sec3_b4", "heartbeat age > 300 s (5 min) -> DEGRADED."),
          t("research.hb.operations.operations.sec3_b5", "otherwise -> HEALTHY."),
          t(
            "research.hb.operations.operations.sec3_b6",
            "no worker row -> IDLE; non-sqlite backend -> UNKNOWN.",
          ),
        ],
      },
      {
        heading: t("research.hb.operations.operations.sec4_head", "The research queue"),
        body: [
          t(
            "research.hb.operations.operations.sec4_p1",
            "GET /api/research/queue returns the queued and running research work grouped by gate type, plus per-gate last_errors and the running work\'s ids (API.md).",
          ),
          t(
            "research.hb.operations.operations.sec4_p2",
            "The Gate queue tab renders exactly that shape — grouped counts, last_errors surfaced, running ids listed — so a stuck gate shows BOTH its backlog and its most recent failure without leaving the page.",
          ),
          t(
            "research.hb.operations.operations.sec4_p3",
            "Queue states on rows mirror terminality: QUEUED (not started), RUNNING (in flight), PASSED/FAILED/CANCELLED (terminal — FAILED|CANCELLED are never re-run by queue semantics).",
          ),
        ],
      },
      {
        heading: t("research.hb.operations.operations.sec5_head", "Diagnostics and blocked gates"),
        body: [
          t(
            "research.hb.operations.operations.sec5_p1",
            "pipeline diagnostics expose blocked_gates — gates a candidate currently cannot proceed past (missing prerequisites upstream), distinct from FAILED (a gate ran and said no).",
          ),
          t(
            "research.hb.operations.operations.sec5_p2",
            "Reading rule: blocked = waiting on upstream evidence; failed = evidence exists and is negative; queued = waiting on the worker. Three different problems with three different fixes — conflating them is how operators re-run the wrong thing.",
          ),
          t(
            "research.hb.operations.operations.sec5_p3",
            "The experiments/diagnostics view (task.md 33) frames running experiments and diagnostics with read-only verb semantics — the UI borrows that discipline: it explains state, it does not improvise fixes.",
          ),
        ],
      },
      {
        heading: t("research.hb.operations.operations.sec6_head", "Retention & audits"),
        body: [
          t(
            "research.hb.operations.operations.sec6_p1",
            "GET /api/research/history returns events and evidence in live/archived partitions: events_live, events_archived, evidence_live, evidence_archived (API.md).",
          ),
          t(
            "research.hb.operations.operations.sec6_p2",
            "Live = hot partition for the UI; archived = retained for audit, moved not mutated (EvidenceRetentionError guards the boundary — retention moves rows, it never rewrites them).",
          ),
          t(
            "research.hb.operations.operations.sec6_p3",
            "v1 research datasets are catalogued with their run counts (dataset_id + run_count) — the Datasets tab is that inventory; retention rows are physical-lifecycle facts, separate from evidence semantics.",
          ),
          t(
            "research.hb.operations.operations.sec6_p4",
            "If live and archived counts disagree with a snapshot\'s artifact counts, trust the snapshot + hashes (immutable) over the partition tally (physical) and treat the mismatch as a storage finding.",
          ),
        ],
      },
      {
        heading: t("research.hb.operations.operations.sec7_head", "Pipeline sovereignty"),
        body: [
          t(
            "research.hb.operations.operations.sec7_p1",
            "pipeline.py module contract: data -> discovery -> gate chain (backtest, walk-forward, OOS, robustness, scoring) -> registry, promotion remains operator-gated, and all research executes offline/background so it NEVER blocks the live tick path (spec 31/32/42).",
          ),
          t(
            "research.hb.operations.operations.sec7_p2",
            "The practical consequence: pressing Discover or Validate while the market moves is safe by construction — research consumes recorded experiences, not the live path.",
          ),
          t(
            "research.hb.operations.operations.sec7_p3",
            "Promotion operator-gating repeats at the pipeline layer: even a fully-passing chain cannot make the candidate trade — approve_for_live still requires a human.",
          ),
          t(
            "research.hb.operations.operations.sec7_p4",
            "Calibration notebooks remain explicit Python parity artifacts, not hidden side-channels (69) — anything that tunes research thresholds is visible code, reviewable like the rest.",
          ),
        ],
      },
      {
        heading: t("research.hb.operations.operations.sec8_head", "When a command goes wrong"),
        body: [
          t(
            "research.hb.operations.operations.sec8_p1",
            "Unavailable -> the reason is the answer: check availability\'s reason before anything else.",
          ),
          t(
            "research.hb.operations.operations.sec8_p2",
            "Legal rejections (LifecycleError, EvidenceConflictError, ZeroFrictionError) are explanations, not crashes: the message states the violated rule and the re-run conditions.",
          ),
          t(
            "research.hb.operations.operations.sec8_p3",
            "Retry only TECHNICAL/DATA failures; RESEARCH failures need new evidence or a new config — re-running them is theater.",
          ),
          t(
            "research.hb.operations.operations.sec8_p4",
            "After engine-level corruption scares, self-heal rebuilds derived state from immutable stores before any manual repair — the store is the source; the registry is a cache.",
          ),
        ],
      },
    ],
    params: [
      {
        name: "commands",
        value: "discover · validate · cancel · self-heal · retry-gate · repair-outcomes · recover-missing-outcomes · promote",
        meaning: t(
          "research.hb.operations.operations.param_commands",
          "The POST /api/research/* surface the CommandBar drives.",
        ),
        ref: "debug_research_routes.py",
      },
      {
        name: "retry policy",
        value: "TECHNICAL | DATA retryable · RESEARCH never · FAILED|CANCELLED terminal",
        meaning: t(
          "research.hb.operations.operations.param_retry",
          "Only infrastructure/data failures may be re-run; science failures need new science.",
        ),
        ref: "commands.py",
      },
      {
        name: "worker thresholds",
        value: "DEGRADED >300 s · STUCK >900 s heartbeat age",
        meaning: t(
          "research.hb.operations.operations.param_worker",
          "Heartbeat staleness classification (spec 16).",
        ),
        ref: "observability.py / debug_research_routes.py",
      },
      {
        name: "availability shape",
        value: "{ available: false, reason }",
        meaning: t(
          "research.hb.operations.operations.param_avail",
          "Structured unavailability — never an empty list pretending readiness.",
        ),
        ref: "availability.py",
      },
      {
        name: "history partitions",
        value: "events_live/archived · evidence_live/archived",
        meaning: t(
          "research.hb.operations.operations.param_history",
          "Retention moves rows for audit; it never mutates them.",
        ),
        ref: "API.md / evidence retention",
      },
      {
        name: "queue shape",
        value: "queued + running grouped by gate type, per-gate last_errors",
        meaning: t(
          "research.hb.operations.operations.param_queue",
          "The Gate queue tab\'s exact data source.",
        ),
        ref: "API.md /api/research/queue",
      },
    ],
    faq: [
      {
        q: t(
          "research.hb.operations.operations.faq1_q",
          "A command button is disabled with a reason — is the feature broken?",
        ),
        a: t(
          "research.hb.operations.operations.faq1_a",
          "No: the backend reported structured unavailability (engine down, dataset absent...). The reason IS the diagnosis — fix that prerequisite and the same button works without any UI change.",
        ),
      },
      {
        q: t("research.hb.operations.operations.faq2_q", "Worker says STUCK — what do I do?"),
        a: t(
          "research.hb.operations.operations.faq2_a",
          "STUCK means heartbeat age >15 min while status says RUNNING: the worker is not reporting. Check the engine/logs before re-running anything; DEGRADED (5-15 min) may just be a long gate — large families legitimately run minutes per fold.",
        ),
      },
      {
        q: t("research.hb.operations.operations.faq3_q", "Can the UI promote a strategy?"),
        a: t(
          "research.hb.operations.operations.faq3_a",
          "It can REQUEST promotion (promote command) and the backend decides: approve_for_live only accepts SHADOW/VALIDATED with an actor id. A refusal is a LifecycleError message quoting the rule — the UI renders it, never argues with it.",
        ),
      },
      {
        q: t("research.hb.operations.operations.faq4_q", "repair-outcomes vs recover-missing-outcomes?"),
        a: t(
          "research.hb.operations.operations.faq4_a",
          "repair re-derives existing-but-malformed outcomes; recover pulls in outcomes that never made it into the economics pipeline. Both are lineage-honest (record repair_state, never fabricate broker deals) and both offer dry-run first.",
        ),
      },
    ],
  };
}

/** Canonical English entry (identity) — TOC, search and tests consume this. */
export const operationsEntry: HandbookEntry = buildOperationsEntry();

/** Canonical English pipeline entry (identity). */
function buildPipelineEntry(t: ScoringTranslate = identity): HandbookEntry {
  return {
    id: "topic/pipeline",
    kind: "topic",
    badge: t("research.hb.operations.pipeline.badge", "DATA → REGISTRY"),
    title: t(
      "research.hb.operations.pipeline.title",
      "The pipeline — from data to operator decision",
    ),
    subtitle: t(
      "research.hb.operations.pipeline.subtitle",
      "dataset -> discovery -> gate chain -> registry -> (operator) promotion, offline and never blocking the tick path.",
    ),
    source: "src/nexus_scalp/research/pipeline.py",
    seeAlso: ["topic/discovery", "topic/lifecycle", "topic/operations"],
    keywords: ["pipeline", "dataset", "registry", "promotion", "offline", "tick path", "background"],
    sections: [
      {
        heading: t("research.hb.operations.pipeline.sec1_head", "The eight stations"),
        body: [
          t(
            "research.hb.operations.pipeline.sec1_p1",
            "Dataset — the research dataset binds symbol/timeframe and records its own fingerprint; v1 datasets are inventoried (dataset_id + run_count) in the Datasets tab.",
          ),
          t(
            "research.hb.operations.pipeline.sec1_p2",
            "Discovery — floors and fingerprint mint DISCOVERED candidates with sample_ids (topic/discovery).",
          ),
          t(
            "research.hb.operations.pipeline.sec1_p3",
            "Gate chain — the six chained gates (topic/gates) run in order over the family partition; each persists an evidence artifact.",
          ),
          t(
            "research.hb.operations.pipeline.sec1_p4",
            "Registry — strategy_intelligence_registry upserts derived rows: lifecycle state, score_payload, by_lifecycle census (a cache; self-heal rebuilds it).",
          ),
          t(
            "research.hb.operations.pipeline.sec1_p5",
            "Operator — approve_for_live: VALIDATED/SHADOW -> ACTIVE with an actor, the only path to dispatch authority.",
          ),
        ],
      },
      {
        heading: t("research.hb.operations.pipeline.sec2_head", "Offline by construction"),
        body: [
          t(
            "research.hb.operations.pipeline.sec2_p1",
            "All research executes offline/background and never blocks the live tick path (pipeline.py spec 31/32/42): discovery, gates, scoring and registry writes consume RECORDED experiences.",
          ),
          t(
            "research.hb.operations.pipeline.sec2_p2",
            "The live path\'s only interaction with research is consumption of an ACTIVE candidate\'s decisions — and reaching ACTIVE required the whole chain plus a human.",
          ),
          t(
            "research.hb.operations.pipeline.sec2_p3",
            "This is why heavy commands (validate on a large family) are safe to fire during market hours: the design separates study from execution at the architecture level, not by convention.",
          ),
        ],
      },
      {
        heading: t(
          "research.hb.operations.pipeline.sec3_head",
          "Re-running and incremental behavior",
        ),
        body: [
          t(
            "research.hb.operations.pipeline.sec3_p1",
            "Discovery is idempotent on identity: the same family re-qualifies to the SAME STRAT-id — re-runs update counts/versions rather than forking duplicates into the registry.",
          ),
          t(
            "research.hb.operations.pipeline.sec3_p2",
            "Data appends grow families over time: a SMALL_SAMPLE row crosses to STANDARD once 20 observations exist; an EVIDENCE_BUILDING candidate retests as data accrues (its legal successors include BACKTESTING).",
          ),
          t(
            "research.hb.operations.pipeline.sec3_p3",
            "Gate re-runs append evidence (never mutate): the latest artifact per (run, gate, kind) drives the verdict, all prior attempts remain for audit.",
          ),
        ],
      },
      {
        heading: t("research.hb.operations.pipeline.sec4_head", "Family partition discipline"),
        body: [
          t(
            "research.hb.operations.pipeline.sec4_p1",
            "pipeline._family_dataset builds each candidate\'s dataset from ITS OWN sample_ids — validation cannot leak across families (TASK-4); legacy revalidate falls back to full dataset as documented legacy behavior.",
          ),
          t(
            "research.hb.operations.pipeline.sec4_p2",
            "The partition is what makes per-strategy metrics honest: every R-number behind a registry row comes from that candidate\'s own closed economic observations.",
          ),
        ],
      },
    ],
    params: [
      {
        name: "order",
        value:
          "dataset -> discovery -> backtest -> walk-forward -> OOS -> robustness -> scoring -> registry",
        meaning: t(
          "research.hb.operations.pipeline.param_order",
          "The station order pipeline.py guarantees.",
        ),
        ref: "pipeline.py",
      },
      {
        name: "promotion",
        value: "operator-gated (approve_for_live)",
        meaning: t(
          "research.hb.operations.pipeline.param_promotion",
          "Pipeline never self-promotes; only SHADOW/VALIDATED -> ACTIVE with actor.",
        ),
        ref: "pipeline.py / lifecycle.py",
      },
      {
        name: "execution context",
        value: "offline/background",
        meaning: t(
          "research.hb.operations.pipeline.param_context",
          "Research never blocks the live tick path.",
        ),
        ref: "pipeline.py",
      },
    ],
    faq: [
      {
        q: t(
          "research.hb.operations.pipeline.faq1_q",
          "Why did a re-run NOT create a second row for my strategy?",
        ),
        a: t(
          "research.hb.operations.pipeline.faq1_a",
          "Identity is deterministic (STRAT-hash of fingerprint): rediscovery upserts the same key with fresher counts instead of forking duplicates — the registry stays one-row-per-family by construction.",
        ),
      },
      {
        q: t("research.hb.operations.pipeline.faq2_q", "Which dataset does Validate use?"),
        a: t(
          "research.hb.operations.pipeline.faq2_a",
          "The candidate\'s own family partition (sample_ids), built by _family_dataset — plus the dataset_id sent in the command. Legacy revalidate without sample_ids uses the full dataset (documented legacy fallback).",
        ),
      },
      {
        q: t("research.hb.operations.pipeline.faq3_q", "Does anything in the pipeline trade?"),
        a: t(
          "research.hb.operations.pipeline.faq3_a",
          "No. The pipeline\'s terminal act is a registry row; trading requires approve_for_live — an explicit operator promotion recorded with an actor in lineage.",
        ),
      },
    ],
  };
}

/** Canonical English pipeline entry (identity) — TOC, search and tests consume this. */
export const pipelineEntry: HandbookEntry = buildPipelineEntry();

/**
 * Translator-aware copies of BOTH entries this module exports: call during
 * render with the store's current t(); the result changes with the language —
 * never cache it outside render. Same ids as operationsEntry/pipelineEntry.
 */
export function operationsTranslated(t?: ScoringTranslate): HandbookEntry[] {
  return [buildOperationsEntry(t), buildPipelineEntry(t)];
}
