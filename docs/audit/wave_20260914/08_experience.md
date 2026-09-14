# Wave 2026-09-14 — Lane 08: Experience / Behavioral-Feedback Subsystem Audit

**Repo/HEAD:** `NexusTradingForexBot` @ `nse/master-active-scalper-wave` (9431edd2), branch tip verified at session start.
**Mode:** read-only forensics (DB opens via `file:...?mode=ro`; scratch files under `%TEMP%`; zero repo writes except this file).
**Executed verification:** `pytest tests/unit/test_experience_intelligence.py tests/unit/test_exit_replay_harness.py tests/unit/test_intelligence_phase09.py -p no:cacheprovider` → **123 passed in 58.44s** (VERIFIED). Counterfactual harness run against the real ledger (VERIFIED, see §7).
Labels: VERIFIED = command executed this session · CODE = read at HEAD · STALE-DOC = doc/taskboard claim contradicted by current data.

---

## 1. Subsystem map (raw → validated → aggregated → policy)

| Stage | Owner | Status |
|---|---|---|
| Raw decision snapshot | `experience/intelligence.py::_record_decision_experience` → `ledger.record_experience` (async queue → `audit_experiences`, UNIQUE `idempotency_key`, append-only) | EXISTS |
| Raw outcome | `intelligence.record_trade_outcome` (+ `execution/terminal_outcome.py`, `experience/lifecycle.py` terminal states; BUG-169/174 writers) → `audit_experience_outcomes` (UNIQUE key) | EXISTS |
| Causality / dedup / anomaly guards | `intelligence.record_trade_outcome` (outcome-before-decision rejected; same-ticket-different-key refused — one broker ticket = one economic trade; `ledger.duplicate_count`) | EXISTS |
| Orphan repair | `outcome_recovery.py`, `outcome_recovery_sweep.py` (startup sweep, broker-truth classified), `outcome_repair.py` (manual web-trigger; UPDATEs an outcome row — the one sanctioned non-append write, corrections table records intent) | EXISTS; `audit_experience_corrections` = **0 rows** (no corrections ever taken) |
| Validation/attribution | `quality.py::OutcomeAnalyzer` (pure-function decomposition: strategy/entry/risk/management/exit/execution quality, 10 behavioral flags) | EXISTS |
| Aggregation | `evaluator.py::StrategyEvaluator` → `strategy_intelligence_registry` (derived, rebuildable via `rebuild_derived_intelligence` / startup `_startup_experience_self_heal`, runtime_loop.py:184) | EXISTS |
| Policy (live loop) | `application/live/tick_pipeline.py:71` → Phase-08 gate (`intelligence.py::evaluate_proposal`) → Phase-09 gate (`intelligence/gate.py`, WARN/suitability) → **then** RiskEngine sizing / dispatch | EXISTS and FIRING |
| Policy (offline) | `model_lifecycle/dataset.py`, `research/dataset.py` consume the ledger for training; `intelligence/evolution.py` mints candidates (operator-promoted) | EXISTS; `auto_train_enabled=False` (live_engine.py:1177) — promotion is operator-gated by design |
| Edge attribution (P0-2) | typed channel column · scheduled counterfactual · weekly edge-ledger | **MISSING — all three deliverables unimplemented** (§6) |

## 2. Is outcome feedback dead telemetry? NO — it is in the loop (VERIFIED)

Exact tokens `experience_score` / `experience_similarity` do not exist anywhere in the repo (grep across src/tests/scripts/configs/docs/agents: 0 hits) — the real seams are named differently. The causal chain is wired and demonstrably fires:

- `tick_pipeline.py:71` calls `om.experience_engine.evaluate_proposal(...)` on every entry proposal **before** risk sizing/dispatch; the Phase-09 `PreTradeIntelligenceGate.evaluate` layers on top (tick_pipeline.py:84).
- Gate effect is bounded and one-directional: DEGRADED → confidence ×0.70 (reject if <0.40); RETIRED/QUARANTINED → `ActionType.NO_TRADE` + `decision_stage=EXPERIENCE_INTELLIGENCE_GATE`; ACTIVE/VALIDATED + replay-validated + recency expectancy >0.50R → ×1.10 boost (capped 1.0). Exits are never gated (`GATED_ENTRY_ACTIONS`), no-evidence passes bit-identical, exceptions pass the original proposal through (CODE).
- **Live proof in the ledger (VERIFIED, read-only SQL):** `audit_signals` shows 26 signals `blocked_by=EXPERIENCE_DEGRADED` (2026-09-08..09-11); `audit_experience_outcomes.lifecycle_detail` shows 126 × `DEGRADED_CONFIDENCE_BELOW_THRESHOLD` + 54 × `EXPERIENCE_INTELLIGENCE_GATE` pre-dispatch rejections + 473 × Phase-09 `SUITABILITY_BELOW_THRESHOLD` (see §4 for the paralysis analysis of the last number).
- Conclusion: **not a write-only subsystem.** Feedback → lifecycle → confidence adjustment → NO_TRADE is a closed loop.

Side observation (CODE): because Phase-09's `PreTradeIntelligenceGate.evaluate` internally calls `evaluate_proposal` again (gate.py:125) after `tick_pipeline.py:71` already called it, every entry proposal runs the Phase-08 gate **twice** per tick — the second decision-row write dedupes at the DB (UNIQUE `exp_{request_id}`), but the gate counters double-count and the TTL-cache budget is consumed twice. Cosmetic/observational, worth a follow-up claim.

One governance gap (CODE): the gate is **hardcoded `enabled=True`** at `live_engine.py:987`; no config/env kill-switch exists (unlike news/candle-intel which are config-disabled by default). A kill-switch is required before any confidence-boost path is trusted (§8-R1).

## 3. Ledger reconciliation: 1373 / 1118 vs the taskboard's "0 outcomes" (VERIFIED)

- The live audit DB is `artifacts/audit.db` (132 MB, WAL, mtime 2026-09-14; `data/audit.db` and `data/nexus.db` are empty decoys — 0 experience tables). Counts confirmed: `audit_experiences` = **1373**, `audit_experience_outcomes` = **1118**, idempotency keys UNIQUE, 0 orphan outcomes, 255 decisions without outcomes (concentrated 2026-08-17..21, pre-P0-A era; the BUG-174 startup sweep covers new ones).
- **1118 = 163 executed + 955 non-trade terminals.** The 955 are `NOT_DISPATCHED` (879), `REJECTED_UNFILLED` (63), `CANCELED_UNFILLED` (13) — BUG-169/P0-A lifecycle rows with R=0/PnL=0 (verified: 0 non-executed rows carry nonzero R — missing stays distinguishable from zero, §19 honored).
- The executed book: 49 wins / 69 losses / 45 BE-band, **net −$2,984.45, PF 0.374 (USD basis), mean R −0.083**, span 2026-08-17 → 2026-09-08.
- **The taskboard claim is STALE-DOC, and it reconciles exactly:** filtering executed rows `outcome_timestamp < 2026-09-04` gives **n=158, net −$2,896.14, PF 0.3813, mean R −0.0822** — digit-for-digit the "158-trade paper record PF 0.381, avg R −0.082, total −$2,896" the wave-3 docs attribute to the *production host* (TASK-EXIT-LEDGER-EXPORT, BLOCKED-ON-OPERATOR). The 5 extra executed rows (09-08) postdate that audit. So this box's `artifacts/audit.db` is not the "0-row dev copy" of 2026-09-09: it now carries the production ledger (either the operator export landed — file is WAL/live, broker-history `last_synced_at` 2026-09-13T20:45, engine self-heal stamped `strategy_intelligence_registry` at 2026-09-13T20:35 — or the engine has been running here continuously). Either way, **there is one ledger, not two, and the 158-trade book is inside our 1118.** (INFERRED from numeric identity; the transfer mechanism is not recorded in the DB.)

**Paper or live?** The 163 executed rows carry real broker correlation: 12-digit MT5 tickets, `reconstruction_source=BROKER_DEALS`, 148/162 tickets resolve in `audit_broker_deals`/`_orders`/`_trades`, non-zero latency/slippage on market fills. `audit_paper_executions` = **0 rows** and paper_state.json `_ticket_counter=100001` — these are NOT paper-adapter tickets. Account stamps are mixed (`audit_account_snapshots`: LIVE 476 / PAPER 80 / '' 4717 rows; note `AuditRepository.current_account_source` **defaults to "LIVE"** — audit_repository.py:197 — so the LIVE stamp is weak evidence). MT5 demo-server tickets are indistinguishable from live ones in this schema. **Verdict: broker-account trades reconstructed from broker deal truth; whether the account is real-money or broker-demo is NOT determinable from the payload columns** — the schema simply has no per-row mode/channel column. That is exactly the P0-2 gap (§6) and a data-integrity finding on its own (§8-R2). The wave-3 label "paper record" for these rows is unsupported by the payload evidence.

## 4. "Lost twice → stop trading" paralysis analysis

Designed guards that work (CODE + VERIFIED):
- **No total-stop from raw streaks.** The experience layer never counts consecutive losses; it evaluates per strategy family. The 3-loss→1h freeze and survival mode are separate *designed risk gates* (`risk/runtime_safety.py`, ledger-derived, operator-releaseable) — not experience-driven.
- **Retirement floor is real:** `min_samples_retire=12` AND recency expectancy ≤ −0.20R AND expectancy ≤ 0 AND t-stat ≤ −1.65 (or normalized DD ≥ 3R with negative expectancy). Registry today: max family = 8 samples, **0 RETIRED, 0 QUARANTINED** (VERIFIED) — hard blocks are currently unreachable; `test_13_single_loss_cannot_retire_a_healthy_strategy` passes.
- Recovery/probation paths exist for RETIRED (20 new samples + positive edges) and DEGRADED is re-derived per refresh (not sticky).

**The real paralysis risk is Phase-09 suitability, not Phase-08 retirement (VERIFIED data + CODE):**
- `_suitability_score` starts 0.5, clamps expectancy contribution to −0.30, adds −0.10/−0.15 for sample counts <5, −0.20 for DEGRADED. A family with **one or two closed losing experiences** (e.g. expectancy −1R, 2 samples) lands ≈0.05–0.25 < 0.40 → **REJECT**, with no sample floor at all.
- Ledger evidence: 473 `SUITABILITY_BELOW_THRESHOLD` pre-dispatch rejections = **34.5 % of all 1373 decisions**; Phase-08 rejected only ~180 (of which every family is DEGRADED/EVALUATING-sized, 5–12 windowed samples); `EXPERIENCE_DEGRADED` blocked 26/1469 signals (1.8 %, matching the coordinator's funnel table).
- **Inverted-evidence paradox (CODE):** zero evidence ⇒ INSUFFICIENT_EVIDENCE ⇒ pass-through; *one loss* ⇒ evidence exists ⇒ reject. A family is therefore punished more at n=1–2 than at n=0 — a self-limiting trap: rejections emit `NOT_DISPATCHED` outcomes which are excluded from scoring (correct), but the family never accumulates the 5+ samples needed to escape the −0.10 small-sample penalty unless sibling blending (similarity ≥0.60) rescues it.
- Net verdict for the wave: no *absolute* stop-trading path (symbol-level activity is throttled, not halted; boost path is unreachable at current volumes — 0 VALIDATED/ACTIVE families), but the **soft-suppression curve is already the largest single rejection source after the policy/confidence gates**, and it is driven by micro-samples. That is "lost twice → trade less", beyond the designed risk gates.

## 5. MAE / MFE / slip / exit-quality population completeness (VERIFIED, executed book n=163)

| Field | Coverage | Notes |
|---|---|---|
| mae_points / mfe_points | no NULLs; 0 in 10/163 (MAE), 30/163 (MFE) | zeros plausible (no adverse/favorable excursion) — not a gap |
| mae_r / mfe_r | present; same zeros as above | risk-normalized path bounds usable for the harness |
| time_to_mae_sec / time_to_mfe_sec | **0,0 in 73/163 (45 %)** | August rows mostly unpopulated (69/123), September mostly populated (36/40) — timing capture arrived mid-stream; MAE/MFE ordering still unknown |
| slippage_points | **0 in 107/163 — by action: LIMIT 107/143 zero, MARKET 0/20 zero** | systematic: limit-order entry slip vs `expected_entry` is not being recorded → `ENTRY_CHASE`/`EXECUTION_SLIPPAGE_ANOMALY` flags can never fire for limit entries; also breaks P0-5 friction calibration |
| execution_latency_ms | 9/163 exactly 0, 20/163 <100 ms | mostly populated |
| exit_quality + full decomposition | **163/163 non-null, 0 nulls in any decomposition key** | complete for executed trades; all-zero by design for the 955 non-trade terminals |
| behavioral_flags | 69/163 flagged (PREMATURE_ENTRY 54, RISK_DEVIATION 8, …); 0 flags on any non-executed row | flags exist only where there is a trade — consistent |
| holding_duration | 3/163 zero | minor |
| Feature snapshots | 990/1373 rows stamped `scalp_v3` @ 50 dims | the EXP-PROV-1 mislabel window (2026-08-24 → 09-11, fixed at bd944fea); **readers must key on `feature_dimension`, not the id**; no dimension-70 snapshots exist in this ledger |

## 6. P0-2 edge attribution: exists? wired?

- **Typed channel column: NOT LANDED (VERIFIED).** Zero occurrences of channel/`signal_channel`/model-gated-vs-channel-only in `src/nexus_scalp/experience/*`; `PRAGMA table_info(audit_experiences)` has no such column; `intelligence.py` last touched by bd944fea (EXP-PROV-1, provenance) — no P0-2 hunks; **no `TASK-P0-2-EDGE-ATTRIBUTION` claim row exists in agents/taskboard.md**. Attribution still requires parsing free-form `entry_reason` (which today contains live float values — `"STAT_ARB_MEAN_REVERSION_SELL_LIMIT (Z: +4.32) | HTF:[...]"` — destroying cardinality: 277 strategy families from 4 nominal setup classes).
- **`scripts/forensics/exit_policy_counterfactual.py`: EXISTS (26 KB, commit 76a3da01 wave-3) but NOT WIRED (VERIFIED).** Grep across `src/`, `configs/`, `.github/workflows/`, `scripts/ci|qa|dev/`, docker: zero references outside the script itself and docs. No maintenance-cycle hook, no workflow, no timer.
- **Weekly edge-ledger in `operational_digest.py`: NOT LANDED (VERIFIED).** zero experience/edge references in the digest builder.
- Bonus VERIFIED run: `--db artifacts/audit.db` now produces REAL mode output (the wave-3 NO_DATA fail-closed behavior is obsolete). **Measurement flaw found:** the loader filters only `WHERE is_closed = 1` (line ~412), and since P0-A writes terminal outcomes for *unexecuted* decisions with R=MAE=MFE=0, the baseline is contaminated: n=1118, win_rate 4.4 %, avg_r −0.012 instead of the true trade stats n=163, wr 30 %, avg −0.083. The `pf=0.5192` happens to survive because zeros are neither win nor loss. Any grid-cell comparison from the current harness under-counts both paths toward "policy looks flat". (The flip-off cell is additionally inert because `exit_mechanism` is a never-populated candidate column — exit family comes from `exit_reason`, which `_is_flip_exit` does not read.)
- Consequence for the P0 roadmap: with 163 executed trades, no family exceeds 8 samples, so the *statistical* half of the loop (retirement, replay validation, edge-by-channel CIs) is starved — P0-5 remains the real blocker; the *plumbing* half (channel column, wiring, digest) is buildable now and was simply never claimed.

## 7. Safety properties confirmed (VERIFIED by 123-test suite + code)

Append-only ledger with rebuildable derived registry; causal filtering (`before_timestamp`, future-outcome rejection); TTL-cache + inline-refresh rate budget with registry fallback so budget exhaustion cannot leak a RETIRED family; bounded retrieval (MAX_RETRIEVAL_LIMIT 2000, top_k); hot path never blocks (queue-based writes, failure isolation — tests 27–40 cover self-heal, corruption survival, persistence-failure isolation); retention registry marks experiences/outcomes TIER_1 `never_delete=True` and no DELETE path exists in code (VERIFIED grep); position-management never gated (BUG-010); accounting is a read facade.

## 8. Safe-integration recommendations (no uncontrolled online learning)

- **R1 — Operator kill-switch, then leave the loop closed.** Add `experience_gate.enabled` (+ separate `suitability_gate.enabled`) config flags honoring the existing GATE_DISABLED passthrough; today it is hardcoded `enabled=True` with no opt-out. Keep fail-open passthrough semantics; never let the flag *tighten* defaults.
- **R2 — Fix P0-2 as the offline attribution it was scoped as.** (a) Additive typed columns on the *decision* row: `signal_channel` (canonical enum MODEL_GATED / SWEEP / ICT / STAT_ARB / RULE_MATRIX / …) derived at record time from `reason_code`+`execution_mode` — never re-derive from mutable text — plus `exec_mode` (LIVE / DEMO-BROKER / PAPER) stamped from the adapter identity at write time (today's `current_account_source` default "LIVE" must stop being a default). Schema evolution via the existing `_add_column_if_missing` protocol; backfill only in *derived* views, never the immutable rows. (b) Canonicalize `entry_reason` (strip floats/Z-scores into payload) before more families fracture.
- **R3 — Wire the counterfactual harness as report-only, after fixing its filter.** Add `AND is_executed = 1` (or a `trade_producing` flag) to `load_real_trades`, read `exit_reason` as the mechanism, then schedule it inside the maintenance cycle (pattern: `maintenance.py` weekly snapshot wiring) writing JSON to `artifacts/forensics/` + a weekly edge-ledger section in `operational_digest.py` (expectancy by channel × exit-path with the deterministic paired-bootstrap from `shadow/bootstrap.py`, run-id seed). No gate may consume harness output automatically.
- **R4 — Guard the paralysis channel explicitly (this is the live one, not retirement).** Give Phase-09 a sample floor mirroring Phase-08: below `min_samples_evaluating`-equivalent (recommend ≥5 *closed-executed* blended samples), suitability may WARN/penalize but never REJECT (floor: reject only when `retrieved_sample_count >= 5`). Add an aggregate suppression alarm: share of entries rejected by SUITABILITY/EXPERIENCE gates over a rolling window vs. configured band (today 34 % + 13 % vs. designed expectation ~2–3 %) → incident telemetry, not silent drift. Keep `NOT_DISPATCHED` rejections out of scoring (already true) so rejections cannot compound — and verify a re-arm path: when a family's rejection was the *cause* of no new samples, the sibling-blend (BUG-140) is its only escape; pin that with a regression test.
- **R5 — Freeze the online-learning boundary.** Do not auto-wire experience → model promotion (`auto_train_enabled=False` and evolution candidates stay operator-gated); do not add confidence-boost feedback into the training-set label pipeline. The only permitted online adaptation stays: derived registry refresh (rebuildable cache) + bounded confidence multipliers within the existing 0.70×/1.10× envelope. Consider disabling the ×1.10 boost until VALIDATED families can exist with real sample sizes — a boost earned on 20 samples where the book's max family is 8 is unreachable anyway, but shipping it live inverts the paralysis risk into over-trading risk.
- **R6 — Close the small data-completeness holes:** (a) record limit-order slippage vs. intended price (else slip flags/friction calibration stay dead for 88 % of fills); (b) populate `time_to_mae/mfe` uniformly (45 % zeros, Aug-only); (c) add the dead-code audit: `QUARANTINED` has no producer (only consumer/probation branches) — either wire anomalous-evidence quarantine or drop the state from the enum docs; (d) run the BUG-174 sweep flag to drain the remaining 255 pre-P0-A no-outcome decisions (sweep exists and runs at startup — confirm why these persist, likely `unknown_provenance` exclusions — log the exclusion counts).
- **R7 — Correct the registries.** Mark TASK-EXIT-LEDGER-EXPORT / "0 outcomes on dev box" rows SUPERSEDED (this box has the 158-trade production book inside artifacts/audit.db, proven by the exact PF/−$ match); record that the harness real-run baseline must exclude non-trade terminals until R3 lands, or its numbers will be cited contaminated.

## 9. One-line answers to the task questions

- Dead subsystem? **No** — outcome feedback demonstrably alters entries (26 + 126 + 54 Phase-08 rejections + 473 Phase-09 rejections in the real ledger); the boost half of the loop is currently unreachable (0 VALIDATED/ACTIVE families).
- Paralysis guards? Retirement is properly guarded (12-sample floor + significance); the unguarded path is **Phase-09 suitability rejecting on 1–4-sample evidence (34 % of decisions)** — fix via R4.
- MAE/MFE/exit-quality? Complete for executed trades (decomposition 163/163); **limit-entry slippage systematically 0** and path-timing ~45 % unpopulated.
- P0-2? Script exists, runs (REAL mode now), but is **not wired**, has a contamination bug against the P0-A ledger, and the channel column + digest section were never started.
- 1373/1118 vs "0 outcomes"? Same single ledger (`artifacts/audit.db`); 1118 = 163 executed + 955 non-trade terminals; the 158-trade "production" book is a timestamp-prefix of the 163; paper-vs-live is **undecidable from payloads** (broker-deal-backed trades; mode column missing — R2).
