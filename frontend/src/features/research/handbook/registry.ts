/**
 * Handbook — the registry as a data structure (topic entry).
 * Compiled from src/nexus_scalp/research/{models,pipeline}.py,
 * experience/evaluator.py (cache semantics) and model.ts field mapping.
 */
import type { HandbookEntry } from "./types";

export const registryEntry: HandbookEntry = {
  id: "topic/registry",
  kind: "topic",
  badge: "CACHE + STORE",
  title: "The strategy registry — fields, versions, cache discipline",
  subtitle: "What a registry row IS: a derived cache with content-derived versions, rebuildable from immutable stores.",
  source: "src/nexus_scalp/research/{models,pipeline}.py, experience/evaluator.py, frontend .../model.ts",
  seeAlso: ["topic/scoring", "topic/lifecycle", "topic/evidence"],
  keywords: ["registry", "strategy_intelligence_registry", "row", "fields", "version", "canonical_version", "cache", "self heal", "upsert", "by_lifecycle"],
  sections: [
    {
      heading: "The row, field by field (as the table renders it)",
      body: [
        "strategy_id — deterministic identity STRAT-<sha16(fingerprint)[:10]>, from discovery. The table truncates at 16 chars with full id on hover; it is the join key for gates/runs/events/evidence queries.",
        "version — canonical_version(): content-derived, immutable per definition. An edited definition becomes a new version under the same id, so a score can never silently change meaning under a stable key.",
        "lifecycle_state — the registry's progress/state label rendered as a StatusPill (see topic/lifecycle for the two-layer vocabulary). Backend-writer only; the UI never recomputes it.",
        "score, confidence, samples — projections of score_payload (StrategyScore): final rounded to 4 decimals in [0,1], confidence, sample_count. Missing field renders '—', never a fabricated default.",
        "updated_at — when the registry row was last upserted. Freshness of the ROW, independent of the FreshnessCaption (which timestamps the summary payload).",
      ],
    },
    {
      heading: "Cache, not truth",
      body: [
        "experience/evaluator.py states it plainly: the registry 'holds no authoritative state' — it is derived intelligence, a CACHE over the immutable experience + evidence stores (spec: registry is a cache).",
        "Consequences operators feel: a corrupt or stale registry is a cache problem, not a data-loss event — self-heal (POST /api/experience/self-heal) rebuilds derived strategy intelligence and reports rebuilt_strategies (or a reason like ENGINE_UNAVAILABLE when it cannot).",
        "Ranking decisions must not treat cached rows as ground truth without evidence links — the contract's evidence discipline applies to anything derived (agents/multi-agent-git-contract.md stance, mirrored in the backend docstrings).",
        "When cache and store disagree: believe the store (artifacts, content hashes), then rebuild the cache. The reverse — editing evidence to match a registry row — would violate append-only.",
      ],
    },
    {
      heading: "Upsert semantics on re-discovery",
      body: [
        "Discovery is idempotent on identity: the same family re-qualifies to the SAME strategy_id, so re-runs UPDATE counts/versions/evidence rather than forking duplicate rows (deterministic id = upsert key).",
        "by_lifecycle counters are aggregated from these rows server-side (summary.by_lifecycle) — the census rail's chip vocabulary is exactly this aggregation's key set; new backend states appear as new chips automatically.",
        "A no-op discovery changes nothing — not an error, not a staleness signal; the verdict line reports what the backend counted.",
        "Concurrent writers: registry transitions are state-machine guarded — an illegal jump raises regardless of who attempts it (LifecycleError), so two agents promoting concurrently cannot produce a half-state; the loser gets the error.",
      ],
    },
    {
      heading: "What the registry deliberately does NOT store",
      body: [
        "Raw trade payloads — evidence artifacts hold payload + content_hash; the registry stores scores and pointers.",
        "Mutable history — append-only stores carry history; the row is a current-view projection (updated_at moves, past values are not overwritten in the evidence chain).",
        "Invented fields — anything absent upstream renders '—'/NOT_RECORDED downstream. 'No row in the registry row-set means no row, never an inferred default' is the display contract (API.md registry shape).",
        "Live authority — ACTIVE is a state transition recorded through approve_for_live, not a field the UI can flip; require_validation_gate re-checks eligibility wherever trades would be authorized.",
      ],
    },
  ],
  params: [
    {
      name: "id format",
      value: "STRAT-<sha256(fingerprint)[:10].UPPER>",
      meaning: "Deterministic upsert key — rediscovery updates, never forks.",
      ref: "discovery.py::_id",
    },
    {
      name: "version",
      value: "canonical_version()",
      meaning: "Content-derived immutable version per definition.",
      ref: "models.py",
    },
    {
      name: "cache rebuild",
      value: "POST /api/experience/self-heal",
      meaning: "Rebuild derived intelligence from immutable stores.",
      ref: "debug routes / experience",
    },
    {
      name: "census",
      value: "summary.by_lifecycle",
      meaning: "Server-side aggregation feeding the lifecycle rail chips.",
      ref: "API.md /api/research/summary",
    },
  ],
  faq: [
    {
      q: "Why does re-running discovery update my row instead of creating a new one?",
      a: "Identity is deterministic from the context fingerprint — same family, same strategy_id — so the upsert updates counts/version/evidence in place. A genuinely different fingerprint (any axis changed) mints a new id.",
    },
    {
      q: "Registry says score 0.72 but score_payload is empty after self-heal — lost?",
      a: "Self-heal rebuilds from stores; an empty payload after rebuild means the authoritative stores had no score for that row (never fabricated). Read the gate evidence artifacts — they, not the cache column, are the record.",
    },
    {
      q: "Can two agents upsert the same row concurrently?",
      a: "Upsert is keyed on deterministic id; state transitions inside are machine-guarded (illegal jumps raise for everyone). The store serializes persistence — you get either the updated row or a LifecycleError, not a torn write.",
    },
  ],
};
