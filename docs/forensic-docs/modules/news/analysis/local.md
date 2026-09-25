# src/nexus_scalp/news/analysis/local.py

- PURPOSE: Local news analysis engine — NO API KEY REQUIRED. Robust
  deterministic/rule-based path: entity extraction, topic classification,
  novelty, directional hypothesis, importance scoring, XAUUSD + USD
  relevance — the authoritative fallback when external AI is absent.
- ARCHITECTURE LAYER: Analysis (rule-based domain logic).
- RESPONSIBILITY: all STAGE 3-6 local analysis consumed by the pipeline.
- DEPENDENCIES: models (entities/topics/impacts/enums), re, observability.
- CONNECTS TO: NewsAnalysisPipeline (extract_entities, classify_topics,
  assess_novelty, xauusd_relevance, usd_relevance, importance_score,
  directional_hypothesis).
- KEY CONCEPTS:
  - Dictionaries: _CURRENCIES (incl. DXY/DOLLAR/STERLING/SWISSIE),
    _ASSETS (GOLD/XAUUSD/SILVER/OIL/TREASURIES/10Y), _INSTITUTIONS
    (FED/FOMC/ECB/BOE/BOJ/SNB/PBOC/BLS/BEA/CFTC/USTREASURY),
    _MACRO_CONCEPTS token->topic (CPI/PCE/NFP/PMI/GDP/RATES/YIELDS/…).
  - Direction lexicons: _BULLISH_XAUUSD / _BEARISH_XAUUSD phrase lists
    ("safe haven", "rate cut", "dovish", "easing", "gold surges", …);
    _BULLISH_FX_ASSET / _BEARISH_FX_ASSET per-currency phrase lists.
  - `_GOLD_NEGATIVES` (line 262): "golden state", "gold medal", "golden
    gate", "goldman" — context matters: a medal story is NOT an XAUUSD
    event (docstring lines 16-19).
  - `extract_entities` (line 287): _count_occurrences per dictionary token
    over upper-cased title+summary+body; canonical entity dict keyed by
    canonical name; relevance = count/total*3 clamped; is_primary when
    count >= 3.
  - `classify_topics` (line 328): macro-token hits + entity hints (FED/ECB/
    BOE -> CENTRAL_BANK; XAUUSD -> COMMODITIES); empty -> OTHER.
  - `assess_novelty` (line 349): duplicate -> REPETITION; updated-
    published > 15min -> UPDATED; else NEW.
  - `xauusd_relevance` (line 362): gold mention = 0.20 base; driver topics
    add fixed weights (USD 0.18, BOND_YIELDS 0.20, INFLATION 0.20, …);
    driver keywords + entity-driven driver credit up to +0.25; price-action
    verbs +0.15; driver-only baseline (calibration 2026-08-17 — previously
    `else: return 0.0` zeroed ~93% of driver headlines on live DB):
    driver_mass>=3 -> >=0.55, ==2 -> >=0.40, ==1 -> >=0.25. Capped 1.0.
  - `usd_relevance` (line 478): USD/DOLLAR +0.5, six topics +0.12 each,
    FED/BLS/BEA/USTREASURY +0.15.
  - `directional_hypothesis` (line 504): bull/bear phrase counts; when
    relevance >= 0.25 builds the XAUUSD impact (strength =
    min(1, |b-s|*0.2 + 0.2 + relevance*0.3), confidence 0.5+relevance*0.3,
    horizon via _horizon, mechanism via _mechanism); driver-only direction
    inference for articles with no gold token (2026-08-17 calibration,
    threshold lowered to 0.25); per-FX-asset impacts for entities/text;
    NEUTRAL when no direction evidence.
  - `importance_score` (line 580): source_priority*0.25 + topic class
    (+0.12 high / +0.04 else) + institution mentions (+0.08 each) + macro
    release markers (+0.10); thresholds 0.7 CRITICAL / 0.5 HIGH / 0.3
    MODERATE / 0.12 MINOR / else TRIVIAL.
  - `_horizon` (line 622): GEOPOLITICS/WAR/MONETARY_POLICY/CENTRAL_BANK ->
    POLICY; INFLATION/EMPLOYMENT/BOND_YIELDS -> MACRO; default MACRO.
  - `_count_occurrences` (line 644): word-boundary regex
    (?<![A-Z0-9])TOKEN(?![A-Z0-9]) on upper-cased text — case-exact.
- HOT PATH / PERFORMANCE: runs in the worker (off tick). Each occurrence
  check compiles a fresh regex per call — fine at article scale, but the
  keyword-coverage scan (analysis/keywords.py) needed the precompile fix.
- EDGE CASES & PITFALLS: phrase lists are uppercase-matchable but must
  appear literally ("GOLD ROSE" matches "gold rose" after .upper()); a
  token like "GOLD" inside "GOLDEN" is excluded by the A-Z0-9 boundaries;
  the beloved-context negatives only suppress in _GOLD_NEGATIVES usage —
  headline-level lexicon hits elsewhere are not negative-aware.