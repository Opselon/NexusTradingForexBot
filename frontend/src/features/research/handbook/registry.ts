/**
 * Handbook — the registry as a data structure (topic entry).
 * Compiled from src/nexus_scalp/research/{models,pipeline}.py,
 * experience/evaluator.py (cache semantics) and model.ts field mapping.
 *
 * Single source of the entry (scoring.ts pattern): every user-visible string
 * goes through t() with the English only as fallback; keys live in
 * features/research/i18n.ts under research.hb.registry.*.
 */
import type { HandbookEntry } from "./types";
import type { ScoringTranslate } from "./scoring";

/** Identity translator (en): English fallback with {var} interpolation only. */
const identity: ScoringTranslate = (_key, fallback, vars) =>
  vars
    ? Object.entries(vars).reduce((s, [k, v]) => s.split(`{${k}}`).join(String(v)), fallback)
    : fallback;

function buildRegistryEntry(t: ScoringTranslate = identity): HandbookEntry {
  return {
  id: "topic/registry",
  kind: "topic",
  badge: t("research.hb.registry.badge", "CACHE + STORE"),
  title: t("research.hb.registry.title", "The strategy registry — fields, versions, cache discipline"),
  subtitle: t("research.hb.registry.subtitle", "What a registry row IS: a derived cache with content-derived versions, rebuildable from immutable stores."),
  source: "src/nexus_scalp/research/{models,pipeline}.py, experience/evaluator.py, frontend .../model.ts",
  seeAlso: ["topic/scoring", "topic/lifecycle", "topic/evidence"],
  keywords: ["registry", "strategy_intelligence_registry", "row", "fields", "version", "canonical_version", "cache", "self heal", "upsert", "by_lifecycle"],
  sections: [
    {
      heading: t("research.hb.registry.s1_head", "The row, field by field (as the table renders it)"),
      body: [
        t("research.hb.registry.s1_p1", "strategy_id — deterministic identity STRAT-<sha16(fingerprint)[:10]>, from discovery. The table truncates at 16 chars with full id on hover; it is the join key for gates/runs/events/evidence queries."),
        t("research.hb.registry.s1_p2", "version — canonical_version(): content-derived, immutable per definition. An edited definition becomes a new version under the same id, so a score can never silently change meaning under a stable key."),
        t("research.hb.registry.s1_p3", "lifecycle_state — the registry's progress/state label rendered as a StatusPill (see topic/lifecycle for the two-layer vocabulary). Backend-writer only; the UI never recomputes it."),
        t("research.hb.registry.s1_p4", "score, confidence, samples — projections of score_payload (StrategyScore): final rounded to 4 decimals in [0,1], confidence, sample_count. Missing field renders '—', never a fabricated default."),
        t("research.hb.registry.s1_p5", "updated_at — when the registry row was last upserted. Freshness of the ROW, independent of the FreshnessCaption (which timestamps the summary payload)."),
      ],
    },
    {
      heading: t("research.hb.registry.s2_head", "Cache, not truth"),
      body: [
        t("research.hb.registry.s2_p1", "experience/evaluator.py states it plainly: the registry 'holds no authoritative state' — it is derived intelligence, a CACHE over the immutable experience + evidence stores (spec: registry is a cache)."),
        t("research.hb.registry.s2_p2", "Consequences operators feel: a corrupt or stale registry is a cache problem, not a data-loss event — self-heal (POST /api/experience/self-heal) rebuilds derived strategy intelligence and reports rebuilt_strategies (or a reason like ENGINE_UNAVAILABLE when it cannot)."),
        t("research.hb.registry.s2_p3", "Ranking decisions must not treat cached rows as ground truth without evidence links — the contract's evidence discipline applies to anything derived (agents/multi-agent-git-contract.md stance, mirrored in the backend docstrings)."),
        t("research.hb.registry.s2_p4", "When cache and store disagree: believe the store (artifacts, content hashes), then rebuild the cache. The reverse — editing evidence to match a registry row — would violate append-only."),
      ],
    },
    {
      heading: t("research.hb.registry.s3_head", "Upsert semantics on re-discovery"),
      body: [
        t("research.hb.registry.s3_p1", "Discovery is idempotent on identity: the same family re-qualifies to the SAME strategy_id, so re-runs UPDATE counts/versions/evidence rather than forking duplicate rows (deterministic id = upsert key)."),
        t("research.hb.registry.s3_p2", "by_lifecycle counters are aggregated from these rows server-side (summary.by_lifecycle) — the census rail's chip vocabulary is exactly this aggregation's key set; new backend states appear as new chips automatically."),
        t("research.hb.registry.s3_p3", "A no-op discovery changes nothing — not an error, not a staleness signal; the verdict line reports what the backend counted."),
        t("research.hb.registry.s3_p4", "Concurrent writers: registry transitions are state-machine guarded — an illegal jump raises regardless of who attempts it (LifecycleError), so two agents promoting concurrently cannot produce a half-state; the loser gets the error."),
      ],
    },
    {
      heading: t("research.hb.registry.s4_head", "What the registry deliberately does NOT store"),
      body: [
        t("research.hb.registry.s4_p1", "Raw trade payloads — evidence artifacts hold payload + content_hash; the registry stores scores and pointers."),
        t("research.hb.registry.s4_p2", "Mutable history — append-only stores carry history; the row is a current-view projection (updated_at moves, past values are not overwritten in the evidence chain)."),
        t("research.hb.registry.s4_p3", "Invented fields — anything absent upstream renders '—'/NOT_RECORDED downstream. 'No row in the registry row-set means no row, never an inferred default' is the display contract (API.md registry shape)."),
        t("research.hb.registry.s4_p4", "Live authority — ACTIVE is a state transition recorded through approve_for_live, not a field the UI can flip; require_validation_gate re-checks eligibility wherever trades would be authorized."),
      ],
    },
  ],
  params: [
    {
      name: "id format",
      value: "STRAT-<sha256(fingerprint)[:10].UPPER>",
      meaning: t("research.hb.registry.param_id_format", "Deterministic upsert key — rediscovery updates, never forks."),
      ref: "discovery.py::_id",
    },
    {
      name: "version",
      value: "canonical_version()",
      meaning: t("research.hb.registry.param_version", "Content-derived immutable version per definition."),
      ref: "models.py",
    },
    {
      name: "cache rebuild",
      value: "POST /api/experience/self-heal",
      meaning: t("research.hb.registry.param_cache_rebuild", "Rebuild derived intelligence from immutable stores."),
      ref: "debug routes / experience",
    },
    {
      name: "census",
      value: "summary.by_lifecycle",
      meaning: t("research.hb.registry.param_census", "Server-side aggregation feeding the lifecycle rail chips."),
      ref: "API.md /api/research/summary",
    },
  ],
  faq: [
    {
      q: t("research.hb.registry.faq1_q", "Why does re-running discovery update my row instead of creating a new one?"),
      a: t("research.hb.registry.faq1_a", "Identity is deterministic from the context fingerprint — same family, same strategy_id — so the upsert updates counts/version/evidence in place. A genuinely different fingerprint (any axis changed) mints a new id."),
    },
    {
      q: t("research.hb.registry.faq2_q", "Registry says score 0.72 but score_payload is empty after self-heal — lost?"),
      a: t("research.hb.registry.faq2_a", "Self-heal rebuilds from stores; an empty payload after rebuild means the authoritative stores had no score for that row (never fabricated). Read the gate evidence artifacts — they, not the cache column, are the record."),
    },
    {
      q: t("research.hb.registry.faq3_q", "Can two agents upsert the same row concurrently?"),
      a: t("research.hb.registry.faq3_a", "Upsert is keyed on deterministic id; state transitions inside are machine-guarded (illegal jumps raise for everyone). The store serializes persistence — you get either the updated row or a LifecycleError, not a torn write."),
    },
  ],
  };
}

/** Canonical English entry (identity) — TOC, search and tests consume this. */
export const registryEntry: HandbookEntry = buildRegistryEntry();

/** Translator-aware copy: call during render with the store's current t(). */
export function registryTranslated(t?: ScoringTranslate): HandbookEntry {
  return buildRegistryEntry(t);
}
