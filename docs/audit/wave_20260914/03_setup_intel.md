# Lane 03 — Setup Intelligence Audit (NSE wave 2026-09-14)

Base: `nse/master-active-scalper-wave` @ origin/main `9431edd2`, read-only forensics.
All VERIFIED claims below come from commands executed this session (git checks, venv probes,
test runs). Repo paths relative to root. Python: `./.venv/Scripts/python.exe`.

---

## 1. Setup-family inventory (what actually exists in code)

| # | Family | Home | Consumers | Can it fire on current wiring? |
|---|--------|------|-----------|-------------------------------|
| A | **Hunter setups** (14 types) | `src/nexus_scalp/model_generation/setup_detector.py` (`SETUP_TYPES`, L35–50) | (1) offline sample metadata via `HunterSampleMaker.analyze_row` ← `SampleFactory.build_samples` (sample_factory.py L210–214); (2) **live Market Radar** `application/live/bar_handler.py` L118–168 → `_last_market_radar` → web UI passthrough (`web/server.py` L1618, `diagnostics_state_routes.py` L719) | **Detect: yes. Act: never.** Live path is display-only (radar never calls StrategyFactory). Offline GO decision unreachable — §3. |
| B | **Hunter strategies** (15: `hunter_*_v1`) | `src/nexus_scalp/model_generation/strategy_factory.py` (`HUNTER_STRATEGIES` L61–207) | Only `HunterSampleMaker` (sample-maker wiring) + 2 test files | **No — all 15 unreachable for GO on every production data path** (§3, VERIFIED). |
| C | **Live SignalPolicy** (model-probability entries: TICK_SWEEP, PREDICTIVE_LIMIT, STAT_ARB, ICT/FVG pullback, breakout-momentum, AI-reversal) | `src/nexus_scalp/signals/policy.py` (2614 LOC, 12 `return build_nt` rejection seams; VERIFIED) | `tick_pipeline.py` L600 `evaluate_probabilities` — this **is** the live entry brain | **Yes** — this is the only setup logic that produces orders. Rejection census (coordinator DB, 2026-09-06..11): CONFIDENCE_FAIL 29.7%, REGIME_GUARDIAN 24.2%. |
| D | **RuleMatrix** (30+ DB-driven SMC/HFT rules: FVG_SNIPER_FILL, JUDAS_SWING_FADE, ORDERBLOCK_TAP_RESERVE, …) | `src/nexus_scalp/signals/rule_matrix.py` | policy.py L965–995 (filters → entries), engine-owned instance live_engine.py L969 | **Dormant by default**: seeded `is_enabled=0` for all 30+ (`audit_repository._seed_trading_rules` L3311–3316, `DEFAULT 0` L272; VERIFIED). Reenable = `/api/rules/toggle`. Two proposal shapes (RR 1.67/1.22) **collide with the RiskEngine 1.8 RR floor if enabled** (§5). |
| E | **Legacy simple setups** (BREAKOUT/TREND/RANGE/UNKNOWN) | `sample_factory.detect_setup` L106–142 | `SampleContract.metadata.setup_id` → dataset frames | Fires (ATR-ratio rules, regime-free) — but output is write-only: nothing filters or reads `setup_id` (§4.4). |
| F | **Research built-ins** — Ichimoku ×2 (`STRAT-ICHIMILI-FINAL/SPACED`) | `strategies/ichimoku.py` (registers at import L341–342), seeded via `strategies/seeder.py` ← `research/worker.py` L259 `_refresh_seed` | research pipeline backtest/OOS only | **Yes, research-only by contract** — never live (correct per architecture). |
| G | **LLM/evolution StrategyFactory** (NOT the hunter one) | `strategies/factory/orchestrator.py`; live engine attribute `self.strategy_factory` (live_engine.py L1118) is **this class**, evolved candidates go through validation gate | autonomous loop worker, maintenance kick L427–431 | Yes, research lane. Note the **name collision** with hunter `StrategyFactory` (model_generation) — grep-level audit hazard. |
| H | **candle_intelligence** entry/hold/fast-exit verdicts | `candle_intelligence/*` | none — `enabled=False` default; docstring: “NOTHING consumes `_last_candle_decision`” (config.py L23–27) | **Off by config; no consumer even when on.** Documented as deliberate. |
| I | **DecisionStabilityController** (confirmation/hysteresis for flip-floppy model argmax) | `signals/stability_controller.py` L100 | **zero production callers** — only `tests/unit/test_temporal_liquidity_phase20.py` (VERIFIED by full-tree grep) | **No. Dead call site.** The BUG it was written for (597 flips/4000 events) is unmitigated in the live path. |
| J | **MSLIE regime labels** (TRENDING/RANGING/EXPANSION/COMPRESSION/MIXED) | `mslie/regime.py` L203–215 | MSLIE vector + UI only (`regime_label` grep: no consumers outside `mslie/`; VERIFIED) | Display only. Yet another regime taxonomy — §3.3. |

### Taskboard claim: “unblock 9 dead setup families” (`4f57c272`, branch `hermes-subagent/subagent-sa-0-d99f3dd8`)

**LANDED (content-equivalent) — VERIFIED:**
- `git merge-base --is-ancestor 4f57c272 HEAD` → NOT an ancestor (branch SHA itself never merged).
- `git merge-base --is-ancestor b847bbcb HEAD` → **true**. `b847bbcb` (2026-09-03 03:47, “unblock 9 dead setup families in HunterSampleMaker”, relanded by a parallel lane) has byte-identical `sample_maker.py` (`git diff 4f57c272 b847bbcb -- …/sample_maker.py` empty) and the same `tests/unit/test_hunter_sample_strategy_coverage.py` + `critical_suite.txt` entry.
- At HEAD: `HunterSampleMaker.default_strategy` is `None` (sample_maker.py L80/84) and the coverage tests pass: **VERIFIED `11 passed`** (`test_hunter_sample_strategy_coverage.py` + `test_strategy_factory_registry_invariant.py` + `test_hunter_min_quality_pins_bug227.py`).
- The companion registry fix (dead RR floors: `hunter_trend_v1` tp 2.0→2.4, `hunter_range_v1` 1.6→1.8, + `test_strategy_factory_registry_invariant.py`) also landed (`git diff 4f57c272 b847bbcb` stat shows those in the landed set; registry at HEAD self-consistent — VERIFIED probe: all 15 derived-RR ≥ rr_floor).

**But the unblock is nominal:** the 9 non-SMC families remain unable to produce a GO on any production path, for a different root cause the branch also did not fix (§3). And the GO-only filter the commit message cites (“the GO-only sample filter systematically excluded those training samples”) **has no caller at all** — `hunter_gate_frame`/`build_hunter_frame` are dead API (§4.1).

**NOT landed** (sibling claim, branch `subagent-sa-0-5bd739bd` @ `872948a2`, “hunter metadata persistence”): `git merge-base --is-ancestor 872948a2 HEAD` → **false**; HEAD still reads `m["entry_reasons"] = hunter.get("reasons", ())` (sample_maker.py L253, no `tuple(...)` normalize) and `direction` fabrication `("BUY" if … > 0 else "SELL")` (strategy_factory.py L329/L339) — VERIFIED live probe: a directionless setup persists `direction=SELL` on a NO_GO. See M5.

---

## 2. Per-family contract table (entry / confirm / invalidate / SL-TP / regime)

Only families with setup semantics shown. “Invalidate” = what kills the signal before/after entry.

| Family | Entry condition | Confirmation | Invalidation | SL/TP logic | Regime compatibility |
|---|---|---|---|---|---|
| A+B Hunter (per setup → §Appendix table) | detector geomean quality ≥ strategy `min_quality` (0.58–0.62) AND setup-specific signal present | **none** — single-bar snapshot, no confirm-bar concept (unlike I) | quality decays next bar (no state); radar `SETUP_READY/WATCHING/NO_SETUP` state | SL = `atr × atr_stop_mult` (price units) **except** LIQUIDITY_SWEEP/NY_OPEN_SWEEP where `stop_hunt_depth_atr` (an ATR *ratio*) leaks into the price-unit column (M4 unit bug); TP = stop × tp/stop-mult; RR 1.5–2.75 | `regime_ok=("TRENDING","RANGING")` (all but `hunter_range_v1`=`("RANGING",)`) — **never satisfiable on production data (§3)**; `hunter_london_v1` additionally session-gated LONDON |
| C SignalPolicy | model prob ≥ `confidence_threshold` 0.35 (+0.10 survival, +0.10 range penalty) + per-channel feature rules | tick_sweep needs pierce+OFI-flip+velocity>5; guardian ACTIVE freezes; stability controller **not wired** (family I orphan) | 12 rejection seams incl. guardian (MACRO_NEWS_FREEZE/HIGH_SPREAD_CHOP/FREEZE_ALL), re-entry $0.50 lock, exposure MAX_TOTAL_EXPOSURE=1 | structural levels + `atr × atr_sl_buffer_multiplier` (1.5), TP floor `min_risk_reward_ratio` 1.8 (1.2 if conf≥0.95) | reads live `RegimeType` enum natively — the only family aligned with the producer taxonomy |
| D RuleMatrix | per-rule feature triggers (fvg_bullish_active, disp<-0.30, …) | first-match-wins, no confirm | `evaluate_pre_trade_filters` blockers (sweep-confirm, spread-squeeze>$0.25, rejection-wall…) | **hardcoded per rule** ($1.5/$2.5, 1.67/1.22 RR) — bypasses the ATR model, and < RiskEngine 1.8 floor when enabled | per-rule `regime_state` checks; all disabled by default |
| E legacy detect_setup | 0.5·ATR breakout / 3-bar trend / 0.8·ATR range | none | none | none (metadata only) | none (regime-agnostic) |

---

## 3. THE headline dead-wire: regime taxonomy mismatch makes every Hunter GO unreachable (VERIFIED)

`StrategyFactory._evaluate_one` (strategy_factory.py L302–304):
`regime = str(row.get("regime","UNKNOWN")).upper(); if regime not in strat.regime_ok → NO_GO`.

Executed probe (venv):
```
hunter accepts: ['RANGING', 'TRENDING']
live emits    : ['HIGH_SPREAD_CHOP','MACRO_NEWS_FREEZE','RANGING_MEAN_REVERSION','TRENDING_MOMENTUM','VOLATILITY_EXPANSION']   # RegimeType (features/regime_classifier.py L53-58)
intersection  : []
```

Who writes `row["regime"]`:

| Data path | regime key present? | Value seen | Evidence |
|---|---|---|---|
| Live radar rows (`rec` from `_build_retrain_record`, live_engine.py L308–416) | **no** — record = `feat_*` + OHLC + `spread` + `atr_m1` only | n/a (radar never evaluates strategies anyway) | code read + probe |
| Online-retrain buffer (`bar_handler.py` L193 → `live_engine._trigger_async_online_fine_tune` L3710 `pl.DataFrame(records)`) | **no** | — | code read |
| Offline datasets (`DatasetFactory.build` ← `doctor.py` L1302, `benchmark.py` L156/168, `schema_v2.py` L224; frames come from CSV/parquet bars + computed `feat_*`; `compute_60d_frame` adds no regime — VERIFIED `'regime' not in` source) | **no** → `str(row.get("regime","UNKNOWN"))` = `"UNKNOWN"` | `UNKNOWN` | VERIFIED run: 60-row realistic frame → `regime values: ['UNKNOWN']`, `entry_decision values: ['NO_GO']` |
| Experience/live regime strings that *would* arrive if plumbed | — | `TRENDING_MOMENTUM`/`RANGING_MEAN_REVERSION`/… (StrEnum `.value`s, e.g. `bar_handler.py` L43–49, `shadow_recorder.py`, `experience/retriever.py` L175) | VERIFIED run: `regime='TRENDING_MOMENTUM'` → `NO_GO ('REGIME_NOT_OK(TRENDING_MOMENTUM)',)` |
| Unit-test fixtures | **yes — and only them** | hand-written `"TRENDING"`/`"RANGING"` (tests/integration/test_model_generation.py L46; test_hunter_sample_strategy_coverage.py L35/52/71) | code read |

Consequences:
1. 500 elite synthetic sweep rows with no regime key → **0 GO** (VERIFIED run). All 14 setup types / 15 strategies are permanently `NO_GO(REGIME_NOT_OK(UNKNOWN))` on every real dataset, and would remain `REGIME_NOT_OK` even if the live RegimeType were plumbed verbatim.
2. The hunter “GO-only sample filter” narrative (fix message, module docstrings sample_maker.py L11–14: “the model only sees SAMPLES where a qualified setup + strategy agree”) is **false on current wiring twice over**: the filter (`hunter_gate_frame`) has zero callers, and the decision it would filter on is always NO_GO.
3. Tests green + CI green while the feature cannot fire: the fixtures invented a regime vocabulary that no producer emits. Classic test-vs-prod divergence.
4. A third taxonomy exists (`mslie/regime.py`: TRENDING/RANGING/EXPANSION/COMPRESSION/MIXED — hunter-compatible strings, but consumed only inside MSLIE/UI), and a fourth normalization in `research/context_contract.py` L120. Only MSLIE speaks the hunter strings — and its labels never reach sample rows.

**The `4f57c272`/`b847bbcb` fix was real but incomplete: it changed the strategy-selector filter; it did not change the regime gate that keeps all 14 families starved.**

---

## 4. Dead/phantom call sites inside the hunter + policy layers (VERIFIED by exhaustive greps)

| Item | Location | Status |
|---|---|---|
| `hunter_gate_frame` (the GO-only filter) + `build_hunter_frame` | sample_maker.py L162/L215 | **Zero callers in src/ and tests/** — exported in `model_generation/__init__` but wired to nothing. |
| `best_strategy_for` | strategy_factory.py L354 | Zero callers outside its own module. |
| `validate_setup_type` | setup_detector.py L636 | Zero callers anywhere (“for tests/docs” — no test uses it). |
| `hunter_pullback_v1` | advertised in detector `compatible_strategies` (setup_detector.py L330, L395) | **Phantom — not in `HUNTER_STRATEGIES`.** Detector hints are decorative anyway: `evaluate()` selects strategies by the factory's own `setup_types` and never reads `setup.compatible_strategies` (only consumer = `to_contract`/UI). Detector↔factory lists disagree for 6 of 14 setups (VERIFIED diff probe): e.g. IMPULSE (detector: impulse only; factory adds momentum_v1), COMPRESSION_BREAK (factory adds breakout_v1), OTE (factory swaps pullback→smc). UI advertises strategies the evaluator doesn't use. |
| `EntryDecision.risk_fraction = 0.005` | strategy_factory.py L229 | Written into sample metadata; **no consumer** — sizing authority is `RiskEngine`/`RiskConfig.risk_per_trade_pct=0.5` (% units; same economics, no owner conflict, dead shadow contract). |
| `SetupDetection.filters` (`min_atr`, `session`, `trend_aligned`) | setup_detector.py (all detectors) | Computed, serialized to contract/UI; `_evaluate_one` never enforces them. Write-only evidence. |
| `stop_distance`/`tp_distance` | strategy_factory.py L314–319 → sample `price_context` | **Unit bug (M4):** `stop_dist = setup.factors.get("stop_hunt_depth_atr") or atr*mult` mixes an ATR-ratio (0.25) with a price distance (1.8) in one column. VERIFIED: same sweep, atr=2.0 → `stop_dist=0.25` (ratio path) vs `1.8` (fallback path). RR gate survives (scale-invariant), but any consumer treating stop_distance as price (the intended live SL) would size 7× too tight. Nothing consumes it yet — bug is latent, not yet poisoning. |
| `direction` fabrication | strategy_factory.py L329/339 | `direction=None` never emitted: a 0/missing direction factor is stamped `SELL`. VERIFIED probe: directionless CHOCH → `NO_GO … 'SELL'`. Fix authored (872948a2) but NOT landed. |
| `DecisionStabilityController` | signals/stability_controller.py | No production callers (family I). |
| Live `self.strategy_factory` name | live_engine.py L1118 | Resolves to `strategies.factory.orchestrator.StrategyFactory` (evolution), NOT hunter factory. Anyone grepping “strategy_factory.evaluate” for live hunter wiring will find a false negative — the hunter `StrategyFactory` has **zero** live-path callers. |
| `evaluation_regime_performance(regime_col="regime")` | validation.py L291 | Reads the dataset `regime` column — so the same missing-regime root cause silently degrades per-regime validation to a single `UNKNOWN` bucket. |

---

## 5. Scoring analysis: compositional evidence vs binary AND-wall

**Verdict: binary AND-walls almost everywhere; the only compositional layers are post-entry (management), not entry.**

1. **Hunter detector quality** — weighted **geometric mean** with hard-zero absorption (`_quality`: any term ≤0 ⇒ q=0, setup_detector.py L106–108). Documented as deliberate “all conditions must line up” selectivity — but geometric aggregation *is* an AND-wall wearing a score costume: partial evidence contributes nothing, and floor 0.55 then compounds it (a setup needs *uniformly* strong factors; one mediocre 0.5 factor caps q ≈ 0.72^(Σw)).
2. **Hunter strategy gate** — textbook AND-wall: six independent checks append strings to `reasons`; **any** non-empty ⇒ NO_GO (L292–330). Two of the six are degenerate: `RR_BELOW_FLOOR` compares `atr_tp_mult/atr_stop_mult` (a registry constant) against the same entry's `rr_floor` — **tautological** (either permanently inert for all rows, or permanently fatal — the latter killed trend_v1/range_v1 until b847bbcb; still provides zero information about the *actual setup*). `NO_DIRECTION_ALIGNMENT` fires iff direction==0, which every detector already guarantees ≠0 — inert.
3. **SignalPolicy** — sequential fail-fast: ~12 `return build_nt(...)` gates (VERIFIED count), plus additive confidence penalties (range +0.10, survival +0.10, god-mode ×0.85) applied to a single scalar gate. Not compositional; DB census shows the wall starves the engine (CONFIDENCE_FAIL+REGIME_GUARDIAN ≈ 54% of signals).
4. **RuleMatrix** — filter chain, first blocker wins; entry rules bypass the wall entirely (early `return rule_proposal` at policy L990) — inconsistent strictness between rule-created and model-created proposals (a rule entry still faces RiskEngine's RR gate — see §6 row 2).
5. **Genuinely compositional (for contrast)**: `execution/lifecycle/scoring.py` `_calculate_adaptive_evidence_scores` (baseline 0.40 + bounded ±evidence, normalized — VERIFIED read L182–280), `experience/quality.py` weighted scores, `marketplace/scoring.py`. The architecture knows how to accumulate evidence — the *entry* path simply never applies it.

---

## 6. Duplicate/overlapping threshold table (same condition, multiple rejecting owners)

| Condition rejected | Gate 1 | Gate 2 | Gate 3 | Gate 4+ | Worst-case effect |
|---|---|---|---|---|---|
| **Spread too wide** | `SignalPolicy.max_spread_atr_ratio=0.18` → `SPREAD_ATR_RATIO_EXCEEDED` (policy L61, L734–735) | `AlgoConfig.max_spread_pct_of_tp=0.15` (config L109; policy C3-a L746–757) | `AlgoConfig.spread_session_percentile=70` (config L110; policy C3-b L759–783) | `RiskConfig.max_spread_points=60` abs (config L55; risk_engine L416); **regime classifier** chop-enter $0.25/exit $0.18 (regime_classifier L139–140) ⇒ HIGH_SPREAD_CHOP ⇒ guardian FREEZE (24.2% of rejects); RuleMatrix `spread>0.25` (L575); **hunter** per-strategy `max_spread_atr` 0.30/0.32/0.35/0.40 | 6–7 owners of one physical quantity, **incompatible units** (ratio-ATR, ratio-TP, percentile, absolute points, absolute $) and no cross-reference. At XAUUSD M1 ATR≈$2: $0.25 spread ⇒ regime chop-freeze (binds first) while policy 0.18·ATR=$0.36 would pass and hunter 0.30·ATR=$0.60 would pass. The strictest gate silently owns the outcome; tuning any one is theater unless the others are modeled. |
| **RR below floor** | `AlgoConfig.min_risk_reward_ratio=1.8` (config L96) → policy `ASYMMETRIC_RR_LIMIT` (L1324–27) | `RiskEngine(min_risk_reward_ratio=1.8)` independent ctor default (risk_engine L52, gate L436) — same number, two definitions, drift-prone | `SignalPolicy.min_allowed_rr=1.10` (L70) TP-nudge floor (L634 `min()` blends it into 1.8) | `scoring.py` L571 `planned_rr` hardcoded 1.8 fallback; **hunter rr_floor 1.5–2.2** per strategy (tautological); RuleMatrix hardcoded RR **1.67 / 1.22** proposals | Enabling RULE_FVG_SNIPER_FILL (1.67) today produces proposals that pass policy but are **rejected at RiskEngine** (< 1.8) — duplicated gates in *disagreement*, latent if/when the DB toggle is flipped. |
| **Quality/confidence floor** | `HUNTER_MIN_QUALITY=0.55` **defined 3×**: setup_detector L53, sample_maker L38, `TIER_C_MIN` L41 | 15 per-strategy `min_quality` 0.58–0.62 | policy `confidence_threshold=0.35` + penalties | `AlgoConfig.ai_zone_confidence_threshold=0.60` zone-quality gate (policy L1237–44) — semantically the same “is the zone good enough” decision as hunter min_quality, different units, no bridge | 0.55 triplication already drifted from intent (#192-era BUG-227 needed a pin test — test_hunter_min_quality_pins_bug227.py exists because the constant had no single owner). |
| **Session/killzone** | hunter `session_gate="LONDON"` + feat_17 fallback (strategy_factory L306–351) | RuleMatrix `RULE_LONDON_NY_KILLZONE_ONLY` (seeded disabled) | policy session-continuation checks + `AlgoConfig` maintenance window; calendar/gate.py news windows | detector session bonuses (×1.15 sweep; london/ny gating) | Four session opinions; only two enforce. |
| **Re-entry distance** | policy `$0.50` hardcoded (L1197) | standalone fallback `atr×0.50` (L1207) — same lockout, two unit systems, different outcomes by whether order_manager was passed | | | |
| **Risk sizing** | `EntryDecision.risk_fraction=0.005` (fraction) | `RiskConfig.risk_per_trade_pct=0.5` (percent) | | | Same economics represented twice; the hunter one has no consumer (§4). |
| **Displacement floor** | policy `max(0.15, atr×0.12)` (L416) | detector `_detect_breakout_pullback` `>0.05` ratio (L367) | regime classifier `price_trend_threshold=0.0010`, `rv_*` bands | | |

---

## 7. Minimal-fix proposals (architecture-preserving; each is ≤ ~15 LOC at one seam)

**M1 — Regime adapter (revives all 14 families; smallest possible seam).**
Add one pure function in `strategy_factory.py`:
```python
_REGIME_NORMALIZE = {
    "TRENDING_MOMENTUM": "TRENDING",
    "VOLATILITY_EXPANSION": "TRENDING",
    "RANGING_MEAN_REVERSION": "RANGING",
    "MIXED": "RANGING",  # per governance
    "HIGH_SPREAD_CHOP": "CHOP",
    "MACRO_NEWS_FREEZE": "FREEZE",
}
```
and use `regime = _REGIME_NORMALIZE.get(regime, regime)` at L302. *Plus* one line in `_build_retrain_record` (`rec["regime"] = regime_state.regime_type.value if regime_state else "UNKNOWN"` — caller already holds `self.om._last_regime_state`) so live/buffer rows carry the key; offline builders then inherit regime from any frame that has it. Preserves: `regime_ok` tuple contract, frozen dataclasses, hunter string taxonomy (optionally extend `regime_ok` membership from `RegimeType` via the adapter). Guard with one registry test that the adapter's domain covers all `RegimeType` values + one `UNKNOWN` fail-closed assertion (UNKNOWN stays NO_GO — never fabricate eligibility). This is the single change that makes the “9 dead families unblocked” claim finally true.
Do **not** merge `hunter_range_v1` regime semantics beyond the map (RANGING-only stays).

**M2 — Delete or honestly wire the dead filter API.**
`build_hunter_frame`/`hunter_gate_frame`/`best_strategy_for`/`validate_setup_type`: mark deprecated in docstring + `# DEAD-CALLSITE(wave03)` (architecture decision pending product: if GO-gated datasets are wanted, wire via a **default-off** `SampleFactory(hunter_gate=…)` kwarg so goldens/CI are untouched); otherwise remove in a follow-up after a taskboard reservation. Zero behavior change now.

**M3 — Kill the phantom `hunter_pullback_v1` hint** (2 list edits in setup_detector.py L330/L395 — remove the id) **and** extend `test_strategy_factory_registry_invariant.py` with the detector↔factory cross-consistency check my probe ran (6/14 currently differ — assert the *intended* direction: factory owns selection, detector hints must be a subset or removed entirely; cheapest is deleting the hints from `to_contract` and the dataclass field in a later cleanup).

**M4 — Fix the stop_distance unit leak**: replace L314–316 with `stop_dist = atr * strat.atr_stop_mult` always (the `stop_hunt_depth_atr` factor has no evidence value for SL sizing — RR gate is constant anyway), or normalize explicitly (`stop_dist = max(atr*mult, stop_hunt_depth_atr*atr)` if depth is meant to widen the stop). One-line change; pinned by a unit assertion `0.9·ATR ≤ stop_distance ≤ …` in price units. Latent-bug class (INV: distances in price units).

**M5 — Land the authored `872948a2` persistence fix** (entry_reasons tuple-normalize + never fabricate SELL on directionless setups; its 12-test file `tests/unit/test_hunter_metadata_persistence.py` ships with it). It applies cleanly in spirit to HEAD's L253/L329/L339; verify pair-completeness per the repo's known revert hazard (producer + consumer + tests together).

**M6 — Threshold-SSOT for the two money-gates (spread, RR).** Don't merge the gates (each layer legitimately owns a *different* unit), but: (a) RiskEngine ctor should read `AlgoConfig.min_risk_reward_ratio` instead of restating 1.8 (one-line default); (b) any RuleMatrix proposal must be minted through the RR floor helper (raise the hardcoded 1.67/1.22 or accept RiskEngine rejection loudly with a rule-id-tagged metric) before anyone toggles a rule on; (c) add a `runtime_invariants.md`/contracts row naming the *binding* spread owner (today: regime classifier $0.25 enters chop, so it is de-facto strictest). Optional follow-up: a single `GateProvenance` stamp on rejections so funnel analytics (§5 coordinator census) can attribute per-gate without archaeology.

**M7 — AND-wall observability before philosophy change.** The geometric-mean + any-reason design is documented intent (precision hunter). Preserve it; just emit per-gate NO_GO counters where the hunter layer is later wired live (M1+M2), so the same “54% invisible starvation” blind spot that hid behind SignalPolicy's census cannot recur silently in the sample lane.

Recommended sequence: M1 (revive) → M5+M3+M4 (correctness) → M6 (drift-proofing) → M2+M7 (decision + observability). None of them touches the live tick path (hunter layer remains offline/UI-scoped until M2's product decision), consistent with INV-001.

---

## Appendix — 14 setup types: entry/factory mapping (VERIFIED from source)

| Setup | Trigger features (feat idx) | compatible (detector hints) | factory accepts | session gate |
|---|---|---|---|---|
| LIQUIDITY_SWEEP | 48 swept, 15 sweep_sig, 14 stop_hunt/ATR, ×1.15 london/ny bonus | sweep_v1, london_v1 | + smc_v1 | — |
| ORDER_BLOCK | 27 type, 46 bos, 47 equil≈0.5, fib 49 bonus | ob_v1, smc_v1 | same | — |
| FVG | 26 fvg_sig, 6 CLV, 40 htf | fvg_v1, smc_v1 | same | — |
| BREAK_OF_STRUCTURE | 46 bos, 29 breakout, 8 displacement, 40 htf | bos_v1, trend_v1 | + smc_v1 | — |
| CHOCH | 28 choch_sig, 6 CLV | choch_v1, reversal_v1 | same | — |
| OTE_PULLBACK | 49 fib∈[.5,.6], 47 equil, 40 htf | ote_v1, **pullback_v1 (phantom)** | ote_v1, smc_v1 | — |
| TREND_CONTINUATION | 7 momentum, 41 h1, 40 h4, 35 ema21 | trend_v1, momentum_v1 | same | — |
| BREAKOUT_PULLBACK | 29 breakout, 10 room>0.05, 40 htf | breakout_v1, **pullback_v1 (phantom)** | breakout_v1 | — |
| IMPULSE | 8 displacement, 24 vol_z/2, 7 momentum | impulse_v1 | + momentum_v1 | — |
| RANGING_FADE | 12 compression, 6 CLV-edge, 40 htf-flat | range_v1 | range_v1 (**RANGING-only**) | — |
| OVERSOLD_BOUNCE | 34 rsi<0, 1 lower_wick×1.5, 4 pinbar | reversal_v1 | reversal_v1 | — |
| COMPRESSION_BREAK | 12 compression, 29 breakout, 8 displacement | compression_v1 | + breakout_v1 | — |
| LONDON_BREAKOUT | 17 london≠0, 29 breakout, 8 displacement | london_v1 | london_v1 | LONDON |
| NY_OPEN_SWEEP | 18 ny≠0, 48 swept, 14 depth | sweep_v1 | sweep_v1 | — |

All 14 share: `q ≥ min_quality` (geomean AND-wall) → `REGIME_NOT_OK` (dead-wire §3) → `SPREAD_TOO_WIDE` → `SESSION_GATE` → tautological `RR_BELOW_FLOOR` → GO.

---

### Verification ledger (commands executed this session)
- `git merge-base --is-ancestor {4f57c272,b847bbcb,872948a2} HEAD` → false / true / false.
- `git diff 4f57c272 b847bbcb -- sample_maker.py` → empty (content-equivalent relanding).
- pytest: coverage+registry-invariant suites → **8 passed**; + BUG-227 pins → **11 passed**.
- Adapter probes: regime set-intersection `[]`; 500 elite no-regime rows → 0 GO; `TRENDING`→GO / `TRENDING_MOMENTUM`→NO_GO; full offline `SampleFactory` on synthetic 50D frames → regime `['UNKNOWN']`, entry_decision `['NO_GO']`; direction fabrication → `SELL`; stop_distance 0.25 vs 1.8 unit split; detector↔factory hint diff 6/14.
- Greps (zero-caller claims): `hunter_gate_frame`, `build_hunter_frame`, `best_strategy_for`, `validate_setup_type`, `risk_fraction` (consumers), `DecisionStabilityController` (production), `compatible_strategies` (enforcement), `regime` producers into sample frames, rule seeds `is_enabled` DEFAULT 0.
