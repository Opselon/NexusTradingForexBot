# src/nexus_scalp/research/discovery.py

- PURPOSE: PHASE 09B bounded, evidence-based candidate discovery (spec
  10/11) — groups closed experiences into MEANINGFUL context families and
  proposes a strategy per family, never one per tiny numerical combination;
  never creates a candidate below a minimum support count.
- ARCHITECTURE LAYER: Research (pure; no I/O; no order authority).
- RESPONSIBILITY: deterministic context-family census and candidate
  construction with content-addressed identity, two-tier sample floors, and
  per-candidate discovery_evidence.sample_ids so downstream validation can
  restrict every gate to the candidate's OWN family (family-select
  validation, TASK-4).
- DEPENDENCIES: `research.candidates` (StrategyCandidate),
  `research.models` (ResearchSample), stdlib (hashlib, math, defaultdict).
- CONNECTS TO: pipeline.discover (worker cycle), store.research_health_
  summary (family_distribution + discover_candidates census), candidate
  registry upserts.

- KEY CONCEPTS:
  - Floors: MIN_FAMILY_SAMPLES=20 (standard discovery floor),
    MIN_DISCOVERY_EXPECTANCY_R=0.10, SMALL_SAMPLE_FLOOR=8 (absolute floor
    mirroring models.SMALL_SAMPLE_FLOOR).
  - `_context_fingerprint` (lines 45-55): coarse deterministic family key =
    symbol | timeframe | session | regime | volatility_regime | trend_state —
    deliberately NOT strategy_id, NEVER exact 50D equality (TASK-4).
  - `family_distribution` (lines 58-89): census with sizes desc,
    largest/median/smallest, families above/below floor, and samples trapped
    in below-floor families — makes fragmentation measurable.
  - `discover_candidates` (lines 92-173): groups by fingerprint; skips
    families under 8; computes finite-mean expectancy (`_safe_mean`) and
    requires >= 0.10R; builds context/entry/exit/risk tokens from the
    family; tier = STANDARD (>= 20) or SMALL_SAMPLE (8-19) — two-tier
    discovery keeps small families visible while the validation gates
    independently enforce the evidence floor (no threshold weakening).
    strategy_id = `STRAT-<sha256(fingerprint)[:10].upper()>`; version =
    canonical content version; discovery_evidence carries `sample_ids`
    (sorted idempotency keys = the exact economic observations) so the
    pipeline can restrict gates to this family.
  - `_window_str` (lines 187-191): discovery_window = first..last decision
    dates of the family.
  - One economic trade = one observation: dataset builder already dedupes
    idempotency keys; discovery never re-counts fills.
- HOT PATH / PERFORMANCE: O(samples) grouping; per-family mean is one pass;
  worker-cycle only.
- EDGE CASES & PITFALLS:
  - `discover_candidates` returns a candidate for ANY family meeting the
    floors — a family whose 0.10R came from a single outlier is still
    proposed (only the family MEAN gates); the small-sample tier exists
    precisely because the mean is unreliable, yet the expectancy gate uses
    the same mean.
  - discovery_evidence "win_rate" counts strictly positive R (line 162);
    breakeven R=0 counts as a loss here.
  - FAMILY keys mix case-normalized values as recorded in the ledger
    (regime strings like "TRENDING" vs "trending" would split one family
    into two); no normalization is applied.
  - `discovery_window` defaults to the computed range only when the caller
    passes "" (pipeline does) — an explicit empty string from other callers
    is replaced, a None would crash the f-string-free default logic.