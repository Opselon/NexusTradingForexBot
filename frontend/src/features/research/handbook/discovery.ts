/**
 * Handbook — candidate discovery from experience (cross-cutting topic).
 * Compiled from src/nexus_scalp/research/discovery.py + models.py floors.
 * DISCOVERY_* constants mirror discovery.py; the test re-reads the source.
 */
import type { HandbookEntry } from "./types";

/** Mirrors discovery.py::MIN_FAMILY_SAMPLES. */
export const MIN_FAMILY_SAMPLES = 20;
/** Mirrors discovery.py::MIN_DISCOVERY_EXPECTANCY_R. */
export const MIN_DISCOVERY_EXPECTANCY_R = 0.1;
/** Mirrors discovery.py::SMALL_SAMPLE_FLOOR (mirrors models.py). */
export const SMALL_SAMPLE_FLOOR = 8;
/** Mirrors models.py::MIN_EVIDENCE_SAMPLES. */
export const MIN_EVIDENCE_SAMPLES = 20;

export const discoveryEntry: HandbookEntry = {
  id: "topic/discovery",
  kind: "topic",
  badge: "FLOORS 8 / 20 · +0.10R",
  title: "Discovery — how a context family becomes a strategy",
  subtitle: "Coarse fingerprints, deterministic identity, two evidence tiers, and the floors nothing may bypass.",
  source: "src/nexus_scalp/research/discovery.py, models.py",
  seeAlso: ["state/DISCOVERED", "topic/pipeline", "topic/scoring"],
  keywords: ["discovery", "family", "fingerprint", "expectancy", "floor", "tier", "sample ids", "strat"],
  sections: [
    {
      heading: "Context families, not combinatorial slivers",
      body: [
        "Discovery groups closed experiences into MEANINGFUL context families using coarse normalized ranges and a context fingerprint, so it produces pattern families rather than one strategy per tiny numerical combination (discovery.py, spec 10/11).",
        "The fingerprint is six coarse axes joined by pipes: symbol | timeframe | session | regime | volatility_regime | trend_state (_context_fingerprint). Family construction stays deterministic — never strategy_id, never exact 50D equality (TASK-4 forensics note).",
        "Coarseness is the anti-fragmentation control: ten buckets-per-axis would mint thousands of 3-sample 'strategies'. family_distribution() reports largest/median/smallest family sizes and floor rejections so fragmentation is MEASURED, not assumed.",
      ],
      bullets: [
        "Axis 1 symbol — the instrument (XAUUSD in production).",
        "Axis 2 timeframe — bar granularity the experiences were sampled on.",
        "Axis 3 session — trading-session label attached by the experience ledger.",
        "Axis 4 regime — market regime classification at decision time.",
        "Axis 5 volatility_regime — volatility bucket at decision time.",
        "Axis 6 trend_state — trend classification at decision time.",
      ],
    },
    {
      heading: "The two floors and the expectancy bar",
      body: [
        "SMALL_SAMPLE_FLOOR = 8: below eight samples a family is skipped entirely — no candidate, no tier, no record. This is the absolute floor (_id path: 'continue  # below the absolute discovery floor').",
        "MIN_DISCOVERY_EXPECTANCY_R = 0.10: the family's mean finite realized R must reach +0.10R. Families with negative or merely non-negative expectancy are not proposed as candidates.",
        "MIN_FAMILY_SAMPLES = 20: the standard floor. Families from 8..19 samples still DISCOVERED, but tagged tier=SMALL_SAMPLE (TASK-4 two-tier discovery) — 'validation gates independently require the evidence floor, so no threshold weakening is introduced here'.",
        "_safe_mean computes expectancy over FINITE values only — a single NaN/Inf in the ledger cannot poison the mean into a fake qualification.",
      ],
    },
    {
      heading: "Deterministic identity",
      body: [
        "strategy_id = 'STRAT-' + sha256(fingerprint).hexdigest()[:10].upper() — two runs over the same family produce the same id, and the id itself encodes which context axes define the family.",
        "strategy_version comes from candidate.canonical_version(): a content-derived immutable version, so an edited definition is a DIFFERENT version, not a silent mutation of the old claim.",
        "Deterministic identity is what makes the registry re-discoverable: re-running discovery after a data append updates counts and versions without duplicating a family into two competing rows.",
      ],
    },
    {
      heading: "Discovery evidence is an instruction manual",
      body: [
        "Each candidate carries discovery_evidence: samples (count), expectancy_r, win_rate, fingerprint, tier — and sample_ids: the exact idempotency keys of the family's economic observations.",
        "sample_ids is load-bearing (TASK-4): validation MUST restrict its gates to the candidate's OWN family. pipeline.py::_family_dataset builds that restriction; when sample_ids is absent (legacy revalidate) it falls back to the full dataset as documented legacy behavior.",
        "One economic trade = one economic observation: the dataset builder collapses duplicate idempotency keys and only executed+closed outcomes enter; discovery never re-counts fills (module header invariant).",
        "The recorded entry/exit/risk blocks are honest placeholders of what was measured: entry_logic {direction: directional, context: fingerprint, regime_gate}, exit_logic {mode: SL_TP, risk_model: fixed_stop}, risk_assumptions {min_expectancy_r, sample_floor}. Discovery describes; validation judges.",
      ],
    },
    {
      heading: "The discovery/validation boundary",
      body: [
        "No OOS result is consulted during discovery (spec 27) — otherwise the same data would select AND validate the candidate, which is circular. Selection sees in-sample character; only the gate chain sees holdouts.",
        "The discovery window is recorded (first..last decision dates of the family) so every later dispute can bound WHEN the qualifying samples happened.",
        "feature_schema_id and feature_dimension ride the candidate from its samples — a family discovered under scalp_v1/50D can never be silently compared under a wider schema (models.py header).",
      ],
    },
    {
      heading: "What the Discover button actually runs",
      body: [
        "Run discovery -> POST /api/research/discover -> pipeline.discover over the current research dataset: group, floor, expectancy, mint candidates, upsert registry rows. The command is additive — it cannot demote or delete existing candidates.",
        "A no-op discovery (no new families) is a healthy outcome: floors unchanged, ledger unchanged. The verdict line reports what the backend counted.",
        "Discovery is background work — pipeline spec 31/32/42 keeps it off the live tick path entirely; pressing the button never blocks trading.",
      ],
    },
  ],
  params: [
    {
      name: "SMALL_SAMPLE_FLOOR",
      value: "8",
      meaning: "Absolute floor — families smaller than this are never candidates.",
      ref: "discovery.py",
    },
    {
      name: "MIN_FAMILY_SAMPLES",
      value: "20",
      meaning: "Standard floor — below it a candidate is DISCOVERED at tier SMALL_SAMPLE.",
      ref: "discovery.py",
    },
    {
      name: "MIN_DISCOVERY_EXPECTANCY_R",
      value: "0.10",
      meaning: "Mean realized R a family must show to be proposed as a candidate.",
      ref: "discovery.py",
    },
    {
      name: "fingerprint",
      value: "symbol|timeframe|session|regime|volatility_regime|trend_state",
      meaning: "The six coarse axes defining a family; also the identity seed.",
      ref: "discovery.py::_context_fingerprint",
    },
    {
      name: "strategy_id format",
      value: "STRAT-<sha256(fingerprint)[:10].UPPER>",
      meaning: "Deterministic identity — same family, same id, every run.",
      ref: "discovery.py::_id",
    },
    {
      name: "tiers",
      value: "STANDARD (>=20) | SMALL_SAMPLE (8..19)",
      meaning: "Two-tier discovery (TASK-4); evidence floors still enforced at validation.",
      ref: "discovery.py::discover_candidates",
    },
  ],
  faq: [
    {
      q: "Why didn't my 6-sample family with +0.9R get discovered?",
      a: "Below SMALL_SAMPLE_FLOOR=8 nothing is minted, regardless of expectancy — tiny samples are noise-prone and the floor is absolute. The family will qualify once eight executed+closed observations exist.",
    },
    {
      q: "Is SMALL_SAMPLE a weaker validation?",
      a: "No. Tier labels the DISCOVERY support only. Validation independently enforces MIN_EVIDENCE_SAMPLES=20 (scoring verdict is INCONCLUSIVE below it) and every gate's own thresholds — no threshold is weakened by the tier.",
    },
    {
      q: "Why is my strategy_id a hash?",
      a: "Because the identity IS the context family: STRAT-<hash of the six-axis fingerprint>. It makes rediscovery idempotent and lets an operator verify an id against its family without trusting the UI.",
    },
    {
      q: "Can discovery promote anything?",
      a: "Never. Discovery mints DISCOVERED rows only; promotion requires gates plus an operator (lifecycle.py approve_for_live). The Discover button adds candidates, it cannot move states beyond what the machine allows.",
    },
  ],
};
