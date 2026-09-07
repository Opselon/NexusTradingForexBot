# FINAL PRODUCTION-READINESS AUDIT — NSE / Nexus Scalp Engine

**Agent:** Hermes-FinalAudit | **Date:** 2026-09-07 07:45–09:05 IST
**HEAD at audit close:** `bb5305e0` (repo is LIVE multi-agent: 77+ commits landed during the audit window; every conclusion below was verified against the file ON DISK at read time and re-checked after concurrent commits).

---

## VERDICT (one paragraph)

The repository is **internally coherent at HEAD**: 24/24 core subsystem modules import, the offline runtime smoke **boots a REAL LiveEngine end-to-end and returns overall PASS (41 checks)**, safety gates are fail-closed and reachable, and the honest-status discipline (unproven = labeled unproven) is broadly respected. Two audit findings were **fixed** (event-gate reachability; a foreign-agent artifact race was correctly handled by fail-closed, not by us). The remaining credibility gap is **evidence, not code**: the champion shows a measured NEGATIVE paper record (PF 0.38 on 158 executed trades → NO DEMONSTRATED EDGE must stand), news model-value is UNPROVEN (dataset news columns are all-zero), paper/demo parity is INSUFFICIENT_DATA by design, and two implemented-but-unwired gates (MarketEntryGate, calendar event-gate → policy entry enforcement) remain honest gaps until wired.

---

## EVIDENCE MATRIX

| # | Subsystem | Status | Evidence (verified at HEAD) | Blocker | Next action |
|---|---|---|---|---|---|
| 1 | **LiveEngine** | IMPLEMENTED+WIRED+TESTED (decomposition REAL) | live_engine.py **4178 LOC** (from ~6,200) + 11 extracted modules in `application/live/` (runtime_loop 444, tick_pipeline 557, decision_executor 279, maintenance 340, model_bundle_store 332, inference 319, shadow_recorder 336, hot_swap 212, runtime_mode 254, warmup 235, model_health 157); delegates are thin (verified: order_manager facade delegating to DispatchEngine S10; call sites 1:1). Runtime smoke L3 service-graph PASS | L-seam agent still landing extractions (L12/L13/L14 landed during audit); working tree carries their WIP | Let the seam wave finish; then re-run god-file census (TASK-ANTIGOD baseline) |
| 2 | **OrderManager** | IMPLEMENTED+WIRED (S1–S6 verified in doc+code) | order_manager.py **4026 LOC** (from 6,204); execution/ package: protection_ledger, position_state_machine, recovery_budget, position_intelligence, lifecycle/dispatch (S10 SAFE_MODE gate at dispatch.py:211), terminal_outcome; S6 stop-rule applied per taskboard | per-position loop sections remain (documented boundary) | Optional: S7+ only with tracker-state ownership plan |
| 3 | **AuditRepository** | IMPLEMENTED | 3406 LOC (from 2,730? net new mixins); WAL confirmed (`PRAGMA journal_mode=wal`), `integrity_check=ok`, background writer | — | — |
| 4 | **Updater** | IMPLEMENTED+DECOMPOSED+TESTED | release/updater.py **221 LOC** (from 2,646) → `release/update_engine/` (orchestrator, downloader, backup_migrate, rollback_state, safety_guards…); 11-scenario lifecycle chaos battery runs in nightly (f482c9c0) | — | — |
| 5 | **Model (champion 70d_liquidity)** | SERVING but **NO DEMONSTRATED EDGE** | manifest: scalp_v3/70D, production_eligible=True, 34 folds, dataset ds_70d_clean_m1_20260904; **live paper evidence: 158 executed closed trades, 76W/82L, PF 0.381, avg −$18.33, total −$2,896, avg R −0.082** (audit.db audit_experience_outcomes, read-only query) | Evidence is negative; that must stay visible | Keep NO-EDGE status; no gate lowering, no window tuning |
| 6 | **Confidence calibration** | IMPLEMENTED, **DISABLED (flat sizing) — correct** | No calibration artifact exists anywhere in artifacts/ (verified); `confidence_to_risk_multiplier` returns exactly 1.0 unless state==CALIBRATED; `load_bound_calibrator` enforces model-fingerprint binding (a foreign-model curve is treated as missing); RiskEngine comment + code confirm flat path | Sufficient outcome evidence for Platt fit does not exist | Collector accumulates; do NOT manufacture confidence_calibration.json |
| 7 | **Risk (breakers, sizing, concurrency)** | IMPLEMENTED+WIRED+TESTED | circuit_breakers.py (daily/weekly loss budgets + streak cooldown) consumed inside `RiskEngine.evaluate_proposal`; ConcurrencyPolicy (ac0f9b73); sizing_policy.py is the single sizing source shared with research/economics | — | — |
| 8 | **Runtime halt / kill switch** | IMPLEMENTED+WIRED, **fail-closed verified** | `runtime_safety.py`: HALTED/KILL_SWITCH persisted, release_required; boot restore-first (runtime_loop.py:101–120: trading loop NEVER starts; idles until `nexus risk release`); DEGRADED session-local (hot-path circuit + stale account + loss freeze); kill switch also blocks hedge dispatch via evaluate_proposal:304 | **AUDIT GAP (documented, not fixed):** the *runtime* Telegram-/halt arm (`RiskEngine._kill_switch_active`) is consulted only in `evaluate_proposal` (hedge path) — the standard entry path (policy→tick_pipeline gates→decision_executor→calculate_volume→dispatch_order) does not re-check it. Persisted HALT does block (boot refuse). SAFE_MODE blocks main dispatch (dispatch.py:211) | Small localized fix candidate: add a kill-switch check at decision_executor sizing stage or in dispatch. CONTESTED FILE — deferred; owner: live-engine seam agent. Registered on taskboard |
| 9 | **Economic costs (ECON v1)** | IMPLEMENTED+PARITY-TESTED | configs/execution_assumptions.json CAL-2026-09-07-A (measured from 909 real M1 bars + paper ledger); research/pipeline loads it as default; research/economics reuses `risk/sizing_policy` (single sizing source); backtest↔canonical parity harness (7fca459e) | — | — |
| 10 | **Backtest / Walk-forward** | IMPLEMENTED+TESTED | purge/embargo wired (BUG-183 defaults), walk_forward_mode blocked/expanding with identical purge semantics (foreign commit 3f8f6206 carrier), economic fold metric in R (1d101bbb) | — | — |
| 11 | **70D data contract** | IMPLEMENTED+ENFORCED | schema_contract canonical names + hash (235b8fcc…), InferenceValidator 10 rejection codes (Agent-7 TDF-2), record builder refuses non-VALID liquidity (no zero-fill), BUG-197/217 bound fixes | — | — |
| 12 | **Learning loop** | IMPLEMENTED, **DISABLED by default — verified** | `LearningConfig.enabled=False`, `OnlineFinetuneConfig.enabled=False` (dispatch never fires; throttled INFO only, bar_handler.py:275–296); champion artifact immutable between governed promotions; training-run row persisted BEFORE training (0590d6a5) for restart-safety | — | Do NOT re-enable online fine-tune (writes serving artifact; DEC-0006) |
| 13 | **Statistical promotion** | IMPLEMENTED+PINNED | statistical_promotion_policy.py (min paired samples, 10k bootstrap resamples), deterministic paired bootstrap (shadow/bootstrap.py, seed derived from run_id), policy pins test (9913f9fb), promotion remains gated (INV-015; no auto-promotion) | — | — |
| 14 | **Artifact integrity** | IMPLEMENTED, **FAIL-CLOSED PROVEN LIVE** | load_gate hash/dim/validation gates; runtime smoke RUNTIME-01 **FAILED CLOSED** mid-audit with HASH_MISMATCH when the concurrent trainer agent wrote model.pt (08:10) past its manifest — exactly the designed behavior; smoke returned overall PASS (41 checks) after the trainer's fixture alignment (ab9db747) | None — the gate worked | — |
| 15 | **Paper replay / parity** | IMPLEMENTED; parity **INSUFFICIENT_DATA — honest** | risk/paper_parity.py: returns INSUFFICIENT_DATA when either side lacks samples; refuses bias factors without paired same-signal windows (explicit note); weekly snapshot cadence in maintenance.py:37–43 | Sample count | Keep observational; no parity correction without methodology |
| 16 | **News** | INFRASTRUCTURE READY; **MODEL VALUE UNPROVEN** | Re-verified at HEAD: all 12 news columns in the canonical dataset sum to exactly 0 → the block-importance news delta **0.0000 stands**; news gate IS reachable in the live path (tick_pipeline.py:99–132, operator toggle); prompt provenance + budget + PSI drift landed (P0 wave) | Dataset has no real news history (news.db starts 2026-08-21; dataset window ends 08-18) | Collect live news rows → rebuild dataset → rerun block ablation; until then NO claim of news value |
| 17 | **Economic calendar** | IMPLEMENTED+WIRED (this audit) | **AUDIT FIX `3f8f6206`:** CalendarWorker was never constructed by the engine (API/observability only — Phase 4 violation). Fixed composition-side in maintenance.py (lazy compose on news.db, kicked via `_kick_worker("CALENDAR")`, throttled 900s, failure-isolated); live-verified providers (FF 85 events/17 future high-impact; FedFOMC 5 future meetings); event windows are config; stale→observe/block_high_impact fail-safe modes | Event-gate verdict is advisory until a policy/risk consumer enforces PRE/POST_EVENT on entries | Wire `evaluate_event_window` verdict into the entry gate (next PR; contest-heavy file — coordinate with L-seam owner) |
| 18 | **Candle intelligence** | DISABLED (intentional, evidence-backed) | Zero consumers of `_last_candle_decision` (only writer sites); 33.5k DB rows/4,505 verdicts unconsumed; enabled=false in config default + base.yaml; decision record committed (ced18ba1) | — | Re-enable only via Phase-3-Option-A design with OOS proof |
| 19 | **Drift** | IMPLEMENTED+WIRED (data-only) | drift_breaker.py reference from SERVING scaler (no fabricated reference), min_samples guard, INSUFFICIENT_DATA forbids action; model_lifecycle/feature_drift.py same; news/drift.py PSI; governance drift alerts exist; WARNING ≠ regime change (data-only by design) | — | — |
| 20 | **Telegram control** | IMPLEMENTED+WIRED, **broker-isolated, tested** | tg_command_bus: admin-chat auth + halt/resume confirmation token + update_id dedup + audit fields; command_intent routes halt/resume→RiskEngine, rollback→governance engine (load-gate verified .bak, refused honestly without one); bus never calls broker adapter (INV-010); fail-closed construction (needs token + admin + halt token); 18 control-surface tests | — | — |
| 21 | **Updater signing** | IMPLEMENTED, fail-closed | Ed25519 over canonical payload; classifications MANIFEST_MALFORMED / MISSING_SIGNATURE / UNKNOWN_KEY / SIGNATURE_INVALID — no silent fallback; embedded trust root; update engine verifies manifest + payload hash before activate (orchestrator:447, 636, 830+); TEST-ONLY key is env-gated and unreachable in prod verify | — | — |
| 22 | **CI/QA** | ENFORCED (verified in workflows) | ci.yml runs critical_suite.txt (80 entries) via xdist, critical_manifest drift gate FAILS CI on floor gaps, per-critical-file coverage floors (silence ≠ pass), coverage gate; nightly-qa: slow suite + lifecycle chaos + mutation campaign; nightly-e2e: compose boot + readiness poll + journeys | — | — |
| 23 | **Database/retention** | HEALTHY | audit.db 132MB WAL ok/integrity ok; hygiene AUDIT_ONLY default, apply_deletes=false + never in LIVE; news.db 23k articles with retention owners (NewsGate/ImpactEngine rows); calendar tables additive-CREATE on first compose (no destructive migration) | audit.db growth on long runs | Keep retention cadence; no data deletion to shrink |
| 24 | **Broker time / DST** | IMPLEMENTED+TESTED | session_time.py SESSION_SEMANTICS_VERSION=dst_aware_v1 (London/NY tz-aware, no fixed DST), governance revalidation gate requires the version (verify.py REQUIRED_SESSION_SEMANTICS_VERSION); market_entry_gate broker-clock week math via UTC weekday (no hardcoded offsets); economics phase-6 server-offset correction + rollover/swap tests (9ca824da); naive datetimes rejected in calendar contracts | — | — |

---

## AUDIT FIXES COMMITTED (mine only)

1. **`3f8f6206` fix(calendar): compose CalendarWorker into the live maintenance cycle** — proven Phase-4 defect (calendar existed but was unreachable from the runtime, API-only). Localized fix in maintenance.py (live_engine.py was CONTESTED by the L-seam agent and untouched); +2 wiring tests.
2. **Taskboard/registry rows** (c0624f06 and follow-ups) — audit results + absorption disclosures.

Absorptions during the audit window disclosed per contract (`3f8f6206` carried a foreign factory-kick + walk-forward files; disclosed in docs/agent_handoffs/ and by the parallel owner's own row 825a2611).

## AUDIT FINDINGS REGISTERED BUT NOT FIXED (contested / needs owner)

- **Runtime kill-switch (Telegram /halt) does not gate the standard entry dispatch** (only persisted-boot HALT + SAFE_MODE + hedge path do). Fix is ~5 lines in decision_executor or dispatch, but both files are actively owned by the L-seam agent → deferred, registered on taskboard with owner/action.
- **MarketEntryGate** (broker-clock market state, weekly open guard, Friday cutoff) is implemented+tested but **not wired** into the live entry path — same implemented≠wired class as the calendar finding; owner: execution agent (a8aeb35f).

## HONEST-STATUS VERIFICATION (mission red lines — all held)

- NEWS MODEL VALUE: **UNPROVEN** — re-verified dataset news columns all-zero at HEAD; no synthetic evidence generated.
- Champion: **NO DEMONSTRATED EDGE** (PF 0.381 paper evidence left visible, no metric tuning).
- Parity: **INSUFFICIENT_DATA**, no bias factor invented.
- Calibration: **flat sizing** (no artifact exists; binding enforced; nothing fabricated).
- Learning loop/online fine-tune: **disabled by config default**, serving-artifact write path unreachable.
- Promotion: **gated** (statistical policy + governance; no bypass found).
- Artifact trust: **fail-closed proven in the wild** (HASH_MISMATCH refused boot mid-audit).
- No foreign WIP overwritten; no `git add .`; staged lists checked; no forbidden git operations used.

## SHORTEST PATH TO PRODUCTION-CREDIBLE

1. **(Owner: L-seam agent, ~5 lines)** Gate standard-entry dispatch on the runtime kill switch (close the /halt gap) — then Telegram halt is fully authoritative.
2. **(Owner: execution agent)** Wire MarketEntryGate verdict into the entry decision; wire the calendar event-gate verdict (PRE/POST_EVENT) as an entry blocker per EventGatePolicy — config windows already typed.
3. **(Owner: news mission, data-bound)** Accumulate live news rows → rebuild the 70D dataset → rerun block ablation; only then re-evaluate the policy news-gate (Phase 4C). Until then the model must run with news effectively unproven (it already contributes zero).
4. **(Owner: research/training)** With replay+economics parity landed, run the protected-OOS candidate evaluation; if the negative edge persists, keep the champion in PAPER/SHADOW only — do not pursue LIVE until OOS expectancy is positive under CAL-2026-09-07-A costs.
5. **(Owner: ops)** Paper/demo parity: collect paired same-signal samples; the moment both sides have data, the snapshot flips from INSUFFICIENT_DATA to MEASURED automatically — no code change needed.

**Bottom line:** the system currently says what it is: a coherent, fail-closed, well-instrumented PAPER/SHADOW research engine with honest gate discipline — not yet a proven-profitable live trader, and it refuses to pretend otherwise.
