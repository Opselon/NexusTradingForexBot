/**
 * Handbook — master operator FAQ (topic entry).
 * Answers are compiled from backend source behavior; each cites where the
 * rule lives so an operator can verify rather than trust.
 */
import type { HandbookEntry } from "./types";
import type { ScoringTranslate } from "./scoring";

/** Identity translator (en): English fallback with {var} interpolation only. */
const identity: ScoringTranslate = (_key, fallback, vars) =>
  vars
    ? Object.entries(vars).reduce((s, [k, v]) => s.split(`{${k}}`).join(String(v)), fallback)
    : fallback;

function buildFaqEntry(t: ScoringTranslate = identity): HandbookEntry {
  return {
  id: "topic/faq",
  kind: "topic",
  badge: t("research.hb.faq.badge", "MASTER FAQ"),
  title: t("research.hb.faq.title", "Operator FAQ — discovery to promotion to triage"),
  subtitle: t("research.hb.faq.subtitle", "The questions that actually get asked at this page, answered from the rules that enforce them."),
  source: "src/nexus_scalp/research/*.py (see per-answer citations), API.md",
  seeAlso: ["topic/uiguide", "topic/discovery", "topic/lifecycle", "topic/operations"],
  keywords: ["faq", "questions", "help", "why", "how", "troubleshoot", "promote", "retry", "empty", "zero"],
  sections: [
    {
      heading: t("research.hb.faq.s1_head", "Discovery and candidates"),
      body: [
        t("research.hb.faq.s1_p1", "Why is my registry empty? — Three honest states: discovery has not run (Run discovery), it ran but no family cleared the floors (check analytics rejection reasons + topic/discovery floors: 8 samples absolute, 20 standard, +0.10R expectancy), or the subsystem is unavailable (every panel would show the availability reason — fix that first)."),
        t("research.hb.faq.s1_p2", "Two candidates share a fingerprint — why two rows? — They should not: strategy_id is deterministic from the fingerprint (STRAT-<sha10>). Two rows mean different fingerprints (an axis differs — check discovery_evidence) or a version transition (version column shows content-derived version, same id)."),
        t("research.hb.faq.s1_p3", "My family has 15 samples and +0.5R — SMALL_SAMPLE or not discovered? — SMALL_SAMPLE tier: 8..19 samples still DISCOVER at tier=SMALL_SAMPLE (TASK-4 two-tier). Below 8: not discovered at all, regardless of expectancy."),
        t("research.hb.faq.s1_p4", "Does discovery look at out-of-sample results? — Never (spec 27). Selection uses in-sample character only; OOS is consumed by the OOS gate later. This separation is what keeps the holdout honest."),
      ],
    },
    {
      heading: t("research.hb.faq.s2_head", "Gates and verdicts"),
      body: [
        t("research.hb.faq.s2_p1", "Which gate is strictest? — OOS has the only reject-without-appeal path in scoring (OOS != PASS ⇒ verdict REJECTED/OOS_FAILURE), plus the bootstrap CI must sit entirely above breakeven. In practice OOS and ROBUSTNESS (0.25R stress line) end most candidates."),
        t("research.hb.faq.s2_p2", "My backtest expectancy is +0.8R but the score is low / inconclusive. — Read the verdict chain before the number: n<8 ⇒ INCONCLUSIVE, n<20 ⇒ below evidence floor ⇒ INCONCLUSIVE, walk-forward not passed ⇒ INCONCLUSIVE, CI straddling zero ⇒ INCONCLUSIVE, DSR < 0.95 under mining ⇒ REJECTED-family reasons. The score cannot overvote these blocks."),
        t("research.hb.faq.s2_p3", "What does 'class: TECHNICAL' mean on a failed gate? — FailureClass: TECHNICAL/DATA are retryable (infra/data problems — retry button is shown because the API will accept it); RESEARCH means the statistics failed (never retried — same data would fail the same way); UNKNOWN is the conservative bucket."),
        t("research.hb.faq.s2_p4", "Why is STATIC_VALIDATION in the chain but not in REQUIRED_GATES_FOR_VALIDATION? — It gates ENTRY to the chain (schema/identity contract), while VALIDATED is certified by the five evaluation gates (BACKTEST..SCORING) each with a closed evidence artifact."),
        t("research.hb.faq.s2_p5", "The rail in my drawer shows a neutral step with no rows. — No gate row exists for that step for THIS strategy: it has not reached it (chain order) or the run was cancelled before it. Neutral = no claim; the UI will not infer a status the backend did not record."),
      ],
    },
    {
      heading: t("research.hb.faq.s3_head", "Evidence and trust"),
      body: [
        t("research.hb.faq.s3_p1", "How do I know this evidence wasn't tampered with? — content_hash = sha256(stable_digest(payload)); recompute and compare. Artifacts are append-only, uniqueness (run, gate, kind) raises EvidenceConflictError, and corrections append rather than edit (topic/evidence)."),
        t("research.hb.faq.s3_p2", "NOT_RECORDED everywhere — broken? — Honest absence: v2 snapshots record NOT_RECORDED for data that genuinely was not captured (e.g. lineage fields pre-v2). Filling those with plausible values would be fabrication — the model explicitly forbids it."),
        t("research.hb.faq.s3_p3", "The registry score disagrees with the gate artifacts. — The registry is a derived CACHE (experience/evaluator.py says so). Artifacts + experience store are authoritative; run self-heal to rebuild derived intelligence, then re-read."),
        t("research.hb.faq.s3_p4", "What makes an outcome 'real'? — Outcome lineage: BROKER_DEALS > BROKER_DEALS_AGGREGATED > RECONSTRUCTED > NONE, with repair_state (not_attempted/succeeded/failed). Broker statements are never fabricated; repairs record lineage + state."),
      ],
    },
    {
      heading: t("research.hb.faq.s4_head", "Promotion and money"),
      body: [
        t("research.hb.faq.s4_p1", "How does a strategy go live? — The only path: all five required gates PASSED with evidence ⇒ verdict VALIDATED ⇒ (usually) SHADOW observation ⇒ operator promote → ACTIVE via approve_for_live, actor recorded in lineage. There is no automatic promotion — not even for a perfect score."),
        t("research.hb.faq.s4_p2", "Can I promote straight from VALIDATED to ACTIVE? — Yes, the state machine allows VALIDATED → ACTIVE (approve_for_live accepts both sources). It is still an operator act with an actor id; SHADOW exists so you can observe first, not because the machine demands it."),
        t("research.hb.faq.s4_p3", "What does ACTIVE mean in practice? — Real dispatch authority: in LIVE mode ACTIVE candidates can produce orders. The confirm modal's warning is literal. Exiting ACTIVE goes through DEGRADED or RETIRED — every transition is recorded."),
        t("research.hb.faq.s4_p4", "A DEGRADED strategy improved — can it come back? — DEGRADED → VALIDATED is a legal transition (re-established evidence). REJECTED has no transitions: new evidence means a NEW candidate identity, discovered afresh."),
      ],
    },
    {
      heading: t("research.hb.faq.s5_head", "Triage and operations"),
      body: [
        t("research.hb.faq.s5_p1", "Queue is not draining. — Worker health first: status FAILED ⇒ FAILED; heartbeat >900s while RUNNING ⇒ STUCK; >300s ⇒ DEGRADED (observability rules). Fix the worker before touching gates — retrying gates on a dead worker just queues more work."),
        t("research.hb.faq.s5_p2", "Should I retry this failure? — Retry only TECHNICAL/DATA (the API refuses RESEARCH anyway). Terminal statuses PASSED/FAILED/CANCELLED are never re-run in place — a retry appends a new evaluation."),
        t("research.hb.faq.s5_p3", "Discover button did nothing. — No-op discovery is healthy: floors unchanged, no new qualifying family. Check family_distribution (smallest/median/largest + floor rejections) to see which floor rejected how many."),
        t("research.hb.faq.s5_p4", "Is research blocking my live trading? — No, by construction: research runs offline/background over recorded experiences and never touches the tick path (pipeline.py spec 31/32/42). Promotion — not research — is the only bridge to live."),
        t("research.hb.faq.s5_p5", "What is safe to click during market hours? — Everything on this page: discovery/validate/cancel/promote are background or state-machine guarded. The riskiest legal click is promote→ACTIVE (real authority) — that is why it is confirm-guarded and actor-recorded."),
        t("research.hb.faq.s5_p6", "Something shows a number this page doesn't document. — The backend response wins; docs lag by design (never the reverse). The handbook test fails when pinned constants drift, prompting an update instead of silent divergence."),
        t("research.hb.faq.s5_p7", "My strategy shows '—' for score/confidence — is it broken? — score_payload did not carry those fields (perhaps only some gates produced evidence yet). '—' is honest absence; the drawer's Evidence tab shows exactly which artifacts exist, and scoring fills the payload once the SCORING gate runs."),
        t("research.hb.faq.s5_p8", "Why do two runs of the same strategy have different ids? — They should not for the same family: id is deterministic from the fingerprint. Different ids mean the fingerprint changed (any of the six axes) — compare discovery_evidence.fingerprint between the rows to see which axis moved."),
        t("research.hb.faq.s5_p9", "How large can the registry get before this page chokes? — The registry list is server-side bounded (limit params enforced by the backend — the PAGE_SIZE_HINT under the tab title); the census rail aggregates first, so chips stay readable at any total. Narrow with a lifecycle filter rather than scrolling."),
        t("research.hb.faq.s5_p10", "Is the confidence column a probability the strategy makes money? — No. It is the score payload's evidence-confidence (logistic in sample count plus verdict discipline): 'how much evidence backs this claim', not 'probability of profit'. A high confidence with a failing verdict is impossible by construction — the verdict blocks first."),
        t("research.hb.faq.s5_p11", "Can I export this page's numbers for a report? — Raw payloads are available through the API (summary/registry/gates/evidence endpoints) and the drawer's Raw invariant tab; the page deliberately renders what the backend sends and adds no derived export formats that could drift from the API truth."),
        t("research.hb.faq.s5_p12", "Why does discovery quote +0.10R but validation demands +0.02R OOS — which is stricter? — They measure different samples: +0.10R is IN-SAMPLE family character at selection (before any holdout exists); +0.02R is the default floor on UNSEEN out-of-sample expectancy (with degradation ceilings and CI decisiveness stacked on top). Selection can be demanding precisely because validation is the court of record."),
        t("research.hb.faq.s5_p13", "A gate row says PASSED but the strategy is REJECTED — how? — REJECTED requires at least one FAILED gate OR a terminal research failure at the verdict layer: scoring's chain can reject even after individual gates pass (e.g. OOS failure surfaces in the verdict, DSR/SPA blocks under mining). Read the scoring artifact's reasons — the verdict, not the loudest gate, decides lifecycle."),
        t("research.hb.faq.s5_p14", "What is the safest order to learn this page? — Hero (what the chain is) → census rail + registry (who exists) → drawer of one row (how one strategy's evidence reads) → queue/worker (how work moves) → Playbook topics pipeline → gates → lifecycle → FAQ; the UI guide (topic/uiguide) is the map, the glossary is the dictionary, and the master FAQ is the troubleshooting entry point."),
        t("research.hb.faq.s5_p15", "Do archive counts shrinking mean data loss? — Live→archived migration moves rows (retention, never mutation): live drops as archived rises, TOTAL stays whole. A total that drops breaks the archive-only contract (EvidenceRetentionError guards that boundary) — treat it as a storage incident and compare against snapshot artifact counts (topic/evidence arbitration)."),
      ],
    },
  ],
  params: [
    {
      name: "absolute floor",
      value: "8 samples",
      meaning: t("research.hb.faq.param_abs_floor", "Below this, discovery mints nothing (expectancy irrelevant)."),
      ref: "discovery.py::SMALL_SAMPLE_FLOOR",
    },
    {
      name: "evidence floor",
      value: "20 samples",
      meaning: t("research.hb.faq.param_evidence_floor", "Below this, scoring verdict is INCONCLUSIVE regardless of gates."),
      ref: "models.py::MIN_EVIDENCE_SAMPLES",
    },
    {
      name: "retryable classes",
      value: "TECHNICAL · DATA",
      meaning: t("research.hb.faq.param_retryable", "Only these may be re-queued; RESEARCH failures are terminal-by-science."),
      ref: "evidence.py::FailureClass",
    },
    {
      name: "promotion sources",
      value: "SHADOW | VALIDATED -> ACTIVE",
      meaning: t("research.hb.faq.param_promotion_sources", "approve_for_live allow-list; actor required, never automatic."),
      ref: "lifecycle.py::approve_for_live",
    },
    {
      name: "worker thresholds",
      value: "DEGRADED >300s · STUCK >900s",
      meaning: t("research.hb.faq.param_worker_thresholds", "Heartbeat-age classification before touching anything else."),
      ref: "observability.py (spec 16)",
    },
  ],
  };
}

/** Canonical English entry (identity) — TOC, search and tests consume this. */
export const masterFaqEntry: HandbookEntry = buildFaqEntry();

/** Translator-aware copy: call during render with the store's current t(). */
export function faqTranslated(t?: ScoringTranslate): HandbookEntry {
  return buildFaqEntry(t);
}
