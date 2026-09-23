/**
 * Handbook — glossary of backend vocabulary (topic entry).
 * Every term maps to where it lives; nothing here is coined by the UI.
 */
import type { HandbookEntry } from "./types";
import type { ScoringTranslate } from "./scoring";

/** Identity translator (en): English fallback with {var} interpolation only. */
const identity: ScoringTranslate = (_key, fallback, vars) =>
  vars
    ? Object.entries(vars).reduce((s, [k, v]) => s.split(`{${k}}`).join(String(v)), fallback)
    : fallback;

/**
 * Single source of the entry: badge, title, subtitle, section headings,
 * every term definition and the FAQ go through t() with the English only as
 * fallback; keys live in features/research/i18n.ts under
 * research.hb.glossary.glossary.*. Backend tokens (symbols, enum members,
 * commands, field names, units) stay verbatim inside the translated prose.
 */
function buildGlossaryEntry(t: ScoringTranslate = identity): HandbookEntry {
  return {
    id: "topic/glossary",
    kind: "topic",
    badge: t("research.hb.glossary.glossary.badge", "A–Z"),
    title: t(
      "research.hb.glossary.glossary.title",
      "Glossary — every backend term this page uses",
    ),
    subtitle: t(
      "research.hb.glossary.glossary.subtitle",
      "One-line definitions with the source that defines them — the bridge between dashboard words and code words.",
    ),
    source: "src/nexus_scalp/research/*.py + API.md (term-by-term citations below)",
    seeAlso: ["topic/faq", "topic/uiguide", "topic/gates"],
    keywords: ["glossary", "terms", "definitions", "vocabulary", "名词", "meaning"],
    sections: [
      {
        heading: t("research.hb.glossary.glossary.sec1_head", "Evidence and integrity"),
        body: [
          t(
            "research.hb.glossary.glossary.sec1_p1",
            "artifact — one immutable evidence record for (run_id, gate, kind); unique, append-only, content-hashed (evidence.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec1_p2",
            "content_hash — sha256(stable_digest(payload)): canonical serialization then hash; recompute to verify (evidence.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec1_p3",
            "stable_digest — deterministic JSON-ish serialization (sorted keys, fixed float formatting) so identical payloads hash identically everywhere (evidence.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec1_p4",
            "EvidenceConflictError — raised when a second artifact is written for an already-occupied (run, gate, kind) key: append-only, no upsert (errors.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec1_p5",
            "EvidenceNotFound — absence is an exception, not an empty result: scoring cannot score silence (errors.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec1_p6",
            "NOT_RECORDED — explicit honest absence in v2 snapshots/events: the datum was not captured; it is never backfilled (run_snapshot.py, task.md 34).",
          ),
          t(
            "research.hb.glossary.glossary.sec1_p7",
            "event — append-only narrated step of a run (GATE_STARTED, retry pairing, etc.) with its own timestamp + lineage (events.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec1_p8",
            "outcome lineage — provenance class of an economic outcome: NONE / BROKER_DEALS / BROKER_DEALS_AGGREGATED / RECONSTRUCTED, plus repair_state (outcome_lineage.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec1_p9",
            "run snapshot (v2, CHG-0035) — engine identity + dataset fingerprint + evidence counts + signals_b64 blob bound to (run_id, attempt) (run_snapshot.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec1_p10",
            "signals_b64 — base64 blob of the model\'s decision inputs (fields + context fingerprint) so a verdict\'s inputs can be reproduced (run_snapshot.py).",
          ),
        ],
      },
      {
        heading: t("research.hb.glossary.glossary.sec2_head", "Chain, gates, failures"),
        body: [
          t(
            "research.hb.glossary.glossary.sec2_p1",
            "GATE_CHAIN — the ordered tuple STATIC_VALIDATION → BACKTEST → WALK_FORWARD → OOS → ROBUSTNESS → SCORING; single source of gate ordering (evidence.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec2_p2",
            "GateStatus — PENDING / QUEUED / RUNNING / PASSED / FAILED / SKIPPED / BLOCKED / ERROR / CANCELLED; terminal = PASSED/FAILED/CANCELLED (evidence.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec2_p3",
            "FailureClass — TECHNICAL / DATA / RESEARCH / UNKNOWN; retry policy keys off this (TECHNICAL+DATA retryable, RESEARCH never) (evidence.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec2_p4",
            "RunStatus — QUEUED/RUNNING/COMPLETED/FAILED/CANCELLED/BLOCKED: lifecycle of one validation attempt spanning the chain; cancel ⇒ CANCELLED, never FAILED, completed gate results preserved (evidence.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec2_p5",
            "BLOCKED — cannot run because upstream evidence is missing — distinct from FAILED (ran, said no) and QUEUED (waiting on worker) (pipeline diagnostics).",
          ),
          t(
            "research.hb.glossary.glossary.sec2_p6",
            "REQUIRED_GATES_FOR_VALIDATION — {BACKTEST, WALK_FORWARD, OOS, ROBUSTNESS, SCORING}: all must PASS with closed artifacts for VALIDATED (evidence.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec2_p7",
            "family partition — the candidate\'s OWN family rows (sample_ids) that every gate evaluates on (pipeline.py::_family_dataset; TASK-4).",
          ),
          t(
            "research.hb.glossary.glossary.sec2_p8",
            "purge / embargo — removal of boundary-straddling label windows (default 300 s) + withheld post-boundary training rows (default 60 s) at every split (splitting.py).",
          ),
        ],
      },
      {
        heading: t("research.hb.glossary.glossary.sec3_head", "Statistics and scoring"),
        body: [
          t(
            "research.hb.glossary.glossary.sec3_p1",
            "expectancy (R) — mean realized R per economic observation; the currency of every floor (+0.10 discovery, +0.02 OOS default, >0 folds) (discovery/oos/walkforward).",
          ),
          t(
            "research.hb.glossary.glossary.sec3_p2",
            "economic observation — one executed+closed trade, deduplicated by idempotency key; the unit sample floors count (discovery.py header).",
          ),
          t(
            "research.hb.glossary.glossary.sec3_p3",
            "bootstrap CI (decisive) — 95% CI for mean OOS R must lie ENTIRELY above breakeven or evidence is inconclusive (scoring.py::_oos_evidence_is_decisive).",
          ),
          t(
            "research.hb.glossary.glossary.sec3_p4",
            "DSR (deflated Sharpe ratio) — selection-bias-corrected Sharpe; floor 0.95 across mined n_trials>1 (scoring.py::DSR_CONFIDENCE_FLOOR, Bailey–de Prado).",
          ),
          t(
            "research.hb.glossary.glossary.sec3_p5",
            "Reality Check (SPA) — family-level best-of-N significance: p-value quoted on failure (scoring.py family checks).",
          ),
          t(
            "research.hb.glossary.glossary.sec3_p6",
            "verdict chain — ordered hard gates on the final score: OOS failure ⇒ REJECTED; floors/CI/DSR ⇒ INCONCLUSIVE; else VALIDATED (scoring.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec3_p7",
            "score dimensions — the ten weighted components (perf .20, oos .20, risk .15, robustness .15, stability .10, sample .08, regime .04, recency .04, execution .02, degradation .02), sum 1.00 (scoring.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec3_p8",
            "sample logistic — confidence-from-n curve, mid 60 / steepness 0.06, hard floor <8 (scoring.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec3_p9",
            "regime coverage — normalized breadth of regime-bucket expectancy so a one-regime specialist cannot fake uniformity (scoring.py::_regime_expectancy_coverage).",
          ),
          t(
            "research.hb.glossary.glossary.sec3_p10",
            "degradation — drop from baseline: stress-vs-baseline (robustness, FRAGILE >0.25R) vs OOS-vs-baseline (ceiling 1.0) (robustness.py / oos.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec3_p11",
            "stress scenarios — the six re-pricings: spread+1/+2, slippage+1/+2, latency+50/+150ms over identical trades (robustness.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec3_p12",
            "FRAGILE / RESILIENT — robustness classification against the 0.25R line (robustness.py, docs/gates.md).",
          ),
        ],
      },
      {
        heading: t("research.hb.glossary.glossary.sec4_head", "Discovery, identity, registry"),
        body: [
          t(
            "research.hb.glossary.glossary.sec4_p1",
            "context fingerprint — symbol|timeframe|session|regime|volatility_regime|trend_state: the six coarse axes defining a family (discovery.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec4_p2",
            "STRAT id — STRAT-<sha256(fingerprint)[:10].UPPER>: deterministic family identity (discovery.py::_id).",
          ),
          t(
            "research.hb.glossary.glossary.sec4_p3",
            "tiers — STANDARD (≥20 samples) vs SMALL_SAMPLE (8..19): two-tier discovery, no threshold weakening (TASK-4, discovery.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec4_p4",
            "floors — absolute 8, standard 20, discovery expectancy +0.10R (discovery.py / models.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec4_p5",
            "canonical_version — content-derived immutable strategy version: an edit is a new version, same id (models.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec4_p6",
            "registry — strategy_intelligence_registry: DERIVED CACHE of strategy intelligence; self-heal rebuilds it (experience/evaluator.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec4_p7",
            "by_lifecycle — server-side census of registry rows keyed by lifecycle state — feeds the rail chips (API.md summary).",
          ),
          t(
            "research.hb.glossary.glossary.sec4_p8",
            "score_payload — the StrategyScore JSON persisted on the registry row (models.py / API.md).",
          ),
          t(
            "research.hb.glossary.glossary.sec4_p9",
            "availability — always-structured {available:false, reason}: blocked ≠ empty (availability.py, API.md).",
          ),
          t(
            "research.hb.glossary.glossary.sec4_p10",
            "worker health — HEALTHY / DEGRADED(>300 s beat) / STUCK(>900 s) / FAILED / IDLE / UNKNOWN (observability.py, spec 16).",
          ),
          t(
            "research.hb.glossary.glossary.sec4_p11",
            "self-heal — rebuild derived intelligence from immutable stores; may report ENGINE_UNAVAILABLE (debug routes / experience).",
          ),
          t(
            "research.hb.glossary.glossary.sec4_p12",
            "retry-gate — re-queue a failed gate; TECHNICAL/DATA only (debug_research_routes.py + FailureClass policy).",
          ),
        ],
      },
      {
        heading: t(
          "research.hb.glossary.glossary.sec5_head",
          "Economics, execution, experiments",
        ),
        body: [
          t(
            "research.hb.glossary.glossary.sec5_p1",
            "E1 (zero-friction guard) — ZeroFrictionError on mixed/unset friction provenance: zero-cost studies must be intentional and exclusive (backtest_economics.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec5_p2",
            "CANONICAL_COSTS / FALLBACK_ZERO — provenance labels: production costs from configs/execution_assumptions.json vs refused-unless-allowed zero (models.py::default_research_assumptions).",
          ),
          t(
            "research.hb.glossary.glossary.sec5_p3",
            "ECON v1 — production-like economic engine: conservative evidence-derived friction, required-swap validation, sized economic re-valuation (economics.py labels).",
          ),
          t(
            "research.hb.glossary.glossary.sec5_p4",
            "FRICTIONLESS_RESEARCH (LEGACY) — explicit label for legacy ExecutionAssumptions runs: research-only, never production evidence (economics.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec5_p5",
            "SHADOW — lifecycle state running in production observation with zero dispatch authority; a legal approve_for_live source (lifecycle.py, models.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec5_p6",
            "ACTIVE — real dispatch authority; reached only via operator approve_for_live with actor recorded (lifecycle.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec5_p7",
            "approve_for_live — the promotion gate: SHADOW|VALIDATED → ACTIVE only, never automatic (lifecycle.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec5_p8",
            "require_validation_gate — trade-eligibility check: {VALIDATED, SHADOW, ACTIVE} else LifecycleError (lifecycle.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec5_p9",
            "FREEZE (forward test) — cutoff-time capture of model/scaler/strategy fingerprints + schema hash + git commit, copied + re-verified post-run (forward_test.py, CHG-0035).",
          ),
          t(
            "research.hb.glossary.glossary.sec5_p10",
            "COUNTERFACTUAL_REPLAY — evaluation_mode stamp on replay blocks: counterfactual ≠ executed (streaming_replay/replay_benchmark).",
          ),
          t(
            "research.hb.glossary.glossary.sec5_p11",
            "first-touch — tick-level SL/TP resolution in replay fills: the stop that TOUCHES first wins (streaming_replay.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec5_p12",
            "idempotency key — dedupe key ensuring one economic observation per real trade across reruns (experience/economics pipeline).",
          ),
          t(
            "research.hb.glossary.glossary.sec5_p13",
            "MFE/MAE — maximum favorable/adverse excursion recorded on the replay ledger for stop-placement analysis (streaming_replay.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec5_p14",
            "DATA_ERROR — explicit bad-data failure path in event sources: fail loud, never truncate silently (event_source.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec5_p15",
            "family_distribution — census of family sizes (largest/median/smallest) plus floor-rejection counts: fragmentation and floor effects are measured, not guessed (discovery.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec5_p16",
            "_safe_mean — expectancy averaged over FINITE values only, so one NaN/Inf observation cannot poison a family into fake qualification (discovery.py).",
          ),
          t(
            "research.hb.glossary.glossary.sec5_p17",
            "closed_outcomes — the outcome-quality counter surfaced under the worker KPI: how many economic observations exist to judge strategies with (summary outcome_quality).",
          ),
        ],
      },
    ],
    faq: [
      {
        q: t(
          "research.hb.glossary.glossary.faq1_q",
          "A term is missing from the glossary — now what?",
        ),
        a: t(
          "research.hb.glossary.glossary.faq1_a",
          "The backend vocabulary extended: treat the backend response as truth, then add the term here with its source citation (the handbook\'s discipline: docs follow code, never the reverse).",
        ),
      },
      {
        q: t(
          "research.hb.glossary.glossary.faq2_q",
          "BLOCKED vs FAILED vs QUEUED — the one-line difference?",
        ),
        a: t(
          "research.hb.glossary.glossary.faq2_a",
          "QUEUED = waiting on the worker; BLOCKED = cannot start (upstream evidence missing); FAILED = ran, evidence came back negative. Three fixes: start the worker, produce upstream evidence, or accept/act on the statistics.",
        ),
      },
      {
        q: t("research.hb.glossary.glossary.faq3_q", "Is \'degradation\' one thing?"),
        a: t(
          "research.hb.glossary.glossary.faq3_a",
          "Two: robustness degradation (stress-vs-baseline, FRAGILE >0.25R, robustness.py) and OOS degradation (holdout-vs-baseline, ceiling 1.0, oos.py). Different comparisons, both called degradation in casual speech — the params tables name their files.",
        ),
      },
    ],
  };
}

/** Canonical English entry (identity) — TOC, search and tests consume this. */
export const glossaryEntry: HandbookEntry = buildGlossaryEntry();

/**
 * Translator-aware copy: call during render with the store's current t();
 * the result changes with the language — never cache it outside render.
 * Yields the single entry glossaryEntry yields, same id, translated prose.
 */
export function glossaryTranslated(t?: ScoringTranslate): HandbookEntry[] {
  return [buildGlossaryEntry(t)];
}
