/**
 * Handbook — candidate discovery from experience (cross-cutting topic).
 * Compiled from src/nexus_scalp/research/discovery.py + models.py floors.
 * DISCOVERY_* constants mirror discovery.py; the test re-reads the source.
 */
import type { HandbookEntry } from "./types";
import type { ScoringTranslate } from "./scoring";

/** Mirrors discovery.py::MIN_FAMILY_SAMPLES. */
export const MIN_FAMILY_SAMPLES = 20;
/** Mirrors discovery.py::MIN_DISCOVERY_EXPECTANCY_R. */
export const MIN_DISCOVERY_EXPECTANCY_R = 0.1;
/** Mirrors discovery.py::SMALL_SAMPLE_FLOOR (mirrors models.py). */
export const SMALL_SAMPLE_FLOOR = 8;
/** Mirrors models.py::MIN_EVIDENCE_SAMPLES. */
export const MIN_EVIDENCE_SAMPLES = 20;

/** Identity translator (en): English fallback with {var} interpolation only. */
const identity: ScoringTranslate = (_key, fallback, vars) =>
  vars
    ? Object.entries(vars).reduce((s, [k, v]) => s.split(`{${k}}`).join(String(v)), fallback)
    : fallback;

/**
 * Single source of the entry: every user-visible string goes through t() with
 * the English only as fallback; keys live in features/research/i18n.ts.
 */
function buildDiscoveryEntry(t: ScoringTranslate = identity): HandbookEntry {
  return {
  id: "topic/discovery",
  kind: "topic",
  badge: t("research.hb.discovery.badge", "FLOORS 8 / 20 · +0.10R"),
  title: t("research.hb.discovery.title", "Discovery — how a context family becomes a strategy"),
  subtitle: t("research.hb.discovery.subtitle", "Coarse fingerprints, deterministic identity, two evidence tiers, and the floors nothing may bypass."),
  source: "src/nexus_scalp/research/discovery.py, models.py",
  seeAlso: ["state/DISCOVERED", "topic/pipeline", "topic/scoring"],
  keywords: ["discovery", "family", "fingerprint", "expectancy", "floor", "tier", "sample ids", "strat"],
  sections: [
    {
      heading: t("research.hb.discovery.sec1_head", "Context families, not combinatorial slivers"),
      body: [
        t("research.hb.discovery.sec1_p1", "Discovery groups closed experiences into MEANINGFUL context families using coarse normalized ranges and a context fingerprint, so it produces pattern families rather than one strategy per tiny numerical combination (discovery.py, spec 10/11)."),
        t("research.hb.discovery.sec1_p2", "The fingerprint is six coarse axes joined by pipes: symbol | timeframe | session | regime | volatility_regime | trend_state (_context_fingerprint). Family construction stays deterministic — never strategy_id, never exact 50D equality (TASK-4 forensics note)."),
        t("research.hb.discovery.sec1_p3", "Coarseness is the anti-fragmentation control: ten buckets-per-axis would mint thousands of 3-sample \'strategies\'. family_distribution() reports largest/median/smallest family sizes and floor rejections so fragmentation is MEASURED, not assumed."),
      ],
      bullets: [
        t("research.hb.discovery.sec1_b1", "Axis 1 symbol — the instrument (XAUUSD in production)."),
        t("research.hb.discovery.sec1_b2", "Axis 2 timeframe — bar granularity the experiences were sampled on."),
        t("research.hb.discovery.sec1_b3", "Axis 3 session — trading-session label attached by the experience ledger."),
        t("research.hb.discovery.sec1_b4", "Axis 4 regime — market regime classification at decision time."),
        t("research.hb.discovery.sec1_b5", "Axis 5 volatility_regime — volatility bucket at decision time."),
        t("research.hb.discovery.sec1_b6", "Axis 6 trend_state — trend classification at decision time."),
      ],
    },
    {
      heading: t("research.hb.discovery.sec2_head", "The two floors and the expectancy bar"),
      body: [
        t("research.hb.discovery.sec2_p1", "SMALL_SAMPLE_FLOOR = 8: below eight samples a family is skipped entirely — no candidate, no tier, no record. This is the absolute floor (_id path: \'continue  # below the absolute discovery floor\')."),
        t("research.hb.discovery.sec2_p2", "MIN_DISCOVERY_EXPECTANCY_R = 0.10: the family\'s mean finite realized R must reach +0.10R. Families with negative or merely non-negative expectancy are not proposed as candidates."),
        t("research.hb.discovery.sec2_p3", "MIN_FAMILY_SAMPLES = 20: the standard floor. Families from 8..19 samples still DISCOVERED, but tagged tier=SMALL_SAMPLE (TASK-4 two-tier discovery) — \'validation gates independently require the evidence floor, so no threshold weakening is introduced here\'."),
        t("research.hb.discovery.sec2_p4", "_safe_mean computes expectancy over FINITE values only — a single NaN/Inf in the ledger cannot poison the mean into a fake qualification."),
      ],
    },
    {
      heading: t("research.hb.discovery.sec3_head", "Deterministic identity"),
      body: [
        t("research.hb.discovery.sec3_p1", "strategy_id = \'STRAT-\' + sha256(fingerprint).hexdigest()[:10].upper() — two runs over the same family produce the same id, and the id itself encodes which context axes define the family."),
        t("research.hb.discovery.sec3_p2", "strategy_version comes from candidate.canonical_version(): a content-derived immutable version, so an edited definition is a DIFFERENT version, not a silent mutation of the old claim."),
        t("research.hb.discovery.sec3_p3", "Deterministic identity is what makes the registry re-discoverable: re-running discovery after a data append updates counts and versions without duplicating a family into two competing rows."),
      ],
    },
    {
      heading: t("research.hb.discovery.sec4_head", "Discovery evidence is an instruction manual"),
      body: [
        t("research.hb.discovery.sec4_p1", "Each candidate carries discovery_evidence: samples (count), expectancy_r, win_rate, fingerprint, tier — and sample_ids: the exact idempotency keys of the family\'s economic observations."),
        t("research.hb.discovery.sec4_p2", "sample_ids is load-bearing (TASK-4): validation MUST restrict its gates to the candidate\'s OWN family. pipeline.py::_family_dataset builds that restriction; when sample_ids is absent (legacy revalidate) it falls back to the full dataset as documented legacy behavior."),
        t("research.hb.discovery.sec4_p3", "One economic trade = one economic observation: the dataset builder collapses duplicate idempotency keys and only executed+closed outcomes enter; discovery never re-counts fills (module header invariant)."),
        t("research.hb.discovery.sec4_p4", "The recorded entry/exit/risk blocks are honest placeholders of what was measured: entry_logic {direction: directional, context: fingerprint, regime_gate}, exit_logic {mode: SL_TP, risk_model: fixed_stop}, risk_assumptions {min_expectancy_r, sample_floor}. Discovery describes; validation judges."),
      ],
    },
    {
      heading: t("research.hb.discovery.sec5_head", "The discovery/validation boundary"),
      body: [
        t("research.hb.discovery.sec5_p1", "No OOS result is consulted during discovery (spec 27) — otherwise the same data would select AND validate the candidate, which is circular. Selection sees in-sample character; only the gate chain sees holdouts."),
        t("research.hb.discovery.sec5_p2", "The discovery window is recorded (first..last decision dates of the family) so every later dispute can bound WHEN the qualifying samples happened."),
        t("research.hb.discovery.sec5_p3", "feature_schema_id and feature_dimension ride the candidate from its samples — a family discovered under scalp_v1/50D can never be silently compared under a wider schema (models.py header)."),
      ],
    },
    {
      heading: t("research.hb.discovery.sec6_head", "What the Discover button actually runs"),
      body: [
        t("research.hb.discovery.sec6_p1", "Run discovery -> POST /api/research/discover -> pipeline.discover over the current research dataset: group, floor, expectancy, mint candidates, upsert registry rows. The command is additive — it cannot demote or delete existing candidates."),
        t("research.hb.discovery.sec6_p2", "A no-op discovery (no new families) is a healthy outcome: floors unchanged, ledger unchanged. The verdict line reports what the backend counted."),
        t("research.hb.discovery.sec6_p3", "Discovery is background work — pipeline spec 31/32/42 keeps it off the live tick path entirely; pressing the button never blocks trading."),
      ],
    },
  ],
  params: [
    {
      name: "SMALL_SAMPLE_FLOOR",
      value: "8",
      meaning: t("research.hb.discovery.param_small_sample_floor", "Absolute floor — families smaller than this are never candidates."),
      ref: "discovery.py",
    },
    {
      name: "MIN_FAMILY_SAMPLES",
      value: "20",
      meaning: t("research.hb.discovery.param_min_family_samples", "Standard floor — below it a candidate is DISCOVERED at tier SMALL_SAMPLE."),
      ref: "discovery.py",
    },
    {
      name: "MIN_DISCOVERY_EXPECTANCY_R",
      value: "0.10",
      meaning: t("research.hb.discovery.param_min_discovery_expectancy", "Mean realized R a family must show to be proposed as a candidate."),
      ref: "discovery.py",
    },
    {
      name: "fingerprint",
      value: "symbol|timeframe|session|regime|volatility_regime|trend_state",
      meaning: t("research.hb.discovery.param_fingerprint", "The six coarse axes defining a family; also the identity seed."),
      ref: "discovery.py::_context_fingerprint",
    },
    {
      name: "strategy_id format",
      value: "STRAT-<sha256(fingerprint)[:10].UPPER>",
      meaning: t("research.hb.discovery.param_strategy_id", "Deterministic identity — same family, same id, every run."),
      ref: "discovery.py::_id",
    },
    {
      name: "tiers",
      value: "STANDARD (>=20) | SMALL_SAMPLE (8..19)",
      meaning: t("research.hb.discovery.param_tiers", "Two-tier discovery (TASK-4); evidence floors still enforced at validation."),
      ref: "discovery.py::discover_candidates",
    },
  ],
  faq: [
    {
      q: t("research.hb.discovery.faq1_q", "Why didn\'t my 6-sample family with +0.9R get discovered?"),
      a: t("research.hb.discovery.faq1_a", "Below SMALL_SAMPLE_FLOOR=8 nothing is minted, regardless of expectancy — tiny samples are noise-prone and the floor is absolute. The family will qualify once eight executed+closed observations exist."),
    },
    {
      q: t("research.hb.discovery.faq2_q", "Is SMALL_SAMPLE a weaker validation?"),
      a: t("research.hb.discovery.faq2_a", "No. Tier labels the DISCOVERY support only. Validation independently enforces MIN_EVIDENCE_SAMPLES=20 (scoring verdict is INCONCLUSIVE below it) and every gate\'s own thresholds — no threshold is weakened by the tier."),
    },
    {
      q: t("research.hb.discovery.faq3_q", "Why is my strategy_id a hash?"),
      a: t("research.hb.discovery.faq3_a", "Because the identity IS the context family: STRAT-<hash of the six-axis fingerprint>. It makes rediscovery idempotent and lets an operator verify an id against its family without trusting the UI."),
    },
    {
      q: t("research.hb.discovery.faq4_q", "Can discovery promote anything?"),
      a: t("research.hb.discovery.faq4_a", "Never. Discovery mints DISCOVERED rows only; promotion requires gates plus an operator (lifecycle.py approve_for_live). The Discover button adds candidates, it cannot move states beyond what the machine allows."),
    },
  ],
  };
}

/** Canonical English entry (identity) — TOC, search and tests consume this. */
export const discoveryEntry: HandbookEntry = buildDiscoveryEntry();

/**
 * Translator-aware copy: call during render with the store's current t();
 * the result changes with the language — never cache it outside render.
 * Returned as an array so the handbook overlay can register whole lanes.
 */
export function discoveryTranslated(t?: ScoringTranslate): HandbookEntry[] {
  return [buildDiscoveryEntry(t)];
}
