# Lane 07 — Model / 70D Train-Serve Parity Current-State Audit

**Wave:** 2026-09-14 | **HEAD:** `9431edd2` (nse/master-active-scalper-wave @ origin/main) — lane start, `git status --porcelain` clean at HEAD before lane writes.
**Mode:** read-only forensics (git read-only; artifact DBs opened `mode=ro`). Only write: this file.
**Executed evidence:** all rows marked VERIFIED below come from commands run in this session (sqlite `mode=ro` probes, torch/numpy artifact reads, live code probes, `./.venv/Scripts/python.exe -m pytest` — 62 passed, output file `$LOCALAPPDATA/Temp/lane07_parity_tests.txt`).

---

## 1. Which bundle serves

- Config default + runtime default: `configs/base.yaml:41`, `src/nexus_scalp/configuration/config.py:81`, `runtime_config.py:146/883` → `artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt` (VERIFIED by grep at HEAD).
- On-disk serving bytes: sha256 = `c9982ddde1755591112da679ba82bfca1eeff4def1700f9a7481ca18525dd9ce` (VERIFIED — recomputed; matches `manifest.json model_sha256` and the governed registry fingerprint). `verify_artifact_integrity()` executed against the real artifact → `status=VERIFIED` (VERIFIED).
- Registry CHAMPION row (`artifacts/audit.db.experience_model_registry`, newest by `registered_at`, read-only query, VERIFIED):

| field | value |
|---|---|
| id | 4156 |
| model_id | `primary_scalp_scalp_v1_50d` |
| artifact_path | `artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt` |
| artifact_fingerprint | `c9982ddde1755591` |
| feature_schema_id / feature_dimension | **`scalp_v1` / 50** ← mislabeled (see §2) |
| registered_at | 2026-09-09T23:36:13Z ("registry truthfulness sync: live Champion row") |

- Status census (VERIFIED): 12 CHAMPION / 9 CANDIDATE / 4 CHALLENGER / 1 ARCHIVED rows. The 9 CANDIDATE rows are `scalp_v3@70D` (latest 2026-09-13T20:35Z — BUG-271-era governed re-registrations).
- **Conclusion:** the engine serves the governed 70D `c9982ddd` bundle; boot trust anchor (`model_bundle_store._verify_champion_registry_binding`, compares serving sha16 vs newest CHAMPION row fingerprint) currently PASSES — sentinel probe executed with real row + real bytes → `MATCH` (VERIFIED).

## 2. Dimensions — scalp_v3 70D canonical vs on-disk 50D drift (Appendix-R)

- Appendix-R (deep audit 2026-09-09) defect class: 50D bytes on disk under the governed-70D path (the `bb1f0afe` drift). **Byte-level drift is CLOSED**: on-disk `model.pt` tensor shapes `input_projection (128, 70)`, `classifier (3, 32)` (VERIFIED via torch load), sha = governed `c9982ddd`; the 2026-09-11 re-land (`bb1f0afe`) was restored 09-11 15:07 (BUG-257 record; taskboard wave-3 re-verification).
- Meta (VERIFIED by read): `num_features 70`, `feature_schema_id scalp_v3`, `feature_schema_dimension 70`, `model_head_classes 3`, `num_classes 3`, `label_contract triple_barrier_3class_v1`, `smoke false`, `production_eligible true`, `seq_len 32`, clip `[-5, 5]`.
- Runtime: `effective_feature_dim` derives from the loaded bundle (scaler width 70 → 70D assembly + scalp_v3 binding; `live_engine.py:244-284`). Process *bootstrap* schema is still scalp_v1@50 (`FEATURE_SCHEMAS.active` = scalp_v1/50, VERIFIED via probe) but the bundle-driven effective contract overrides it.
- **RESIDUAL DEFECT (registry truthfulness, not serving safety):** the governing CHAMPION row labels the 70D artifact `scalp_v1@50D` (id 4156 and all its ancestors). The boot anchor only compares the *fingerprint*, so this mislabel cannot poison serving bytes, but `ChampionSync.evaluate_champion_registry_sync` compares row schema/dim against the engine's **class-level** `FEATURE_SCHEMA_ID/FEATURE_DIM` (= bootstrap scalp_v1/50, because it does not use the bundle-derived effective values) → the row "agrees" with the engine and the sync NOOPs forever; the mislabel is self-perpetuating. Prior OBS-TRACE-2 audit already recorded the same shape ("scalp_v3-labeled experience rows carry 50-value feature snapshots"). The 2026-09-13 Docker provisioner BUG-269 fixed the *fresh-mint* twin of this class; the registry *metadata* twin stays open.

## 3. Schema hash + scaler binding

- `feature_schema_hash()` executed at HEAD → `235b8fccc96b7e0e`, identical to meta + manifest + dataset build report `ds_70d_clean_m1_20260904` (VERIFIED). `scalp_v3` registered dim = 70 (VERIFIED via `FEATURE_SCHEMAS.resolve`), so `_artifact_meta_coherence`'s schema-identity gate (registered id + dim==artifact_dim) passes.
- Scaler binding: recomputed `model.scaler.npz` sha256 = `b3c65b654aa32718a53e3a5e4383c969830f55521d27fe6fad5734a9b341aeff` == `manifest.json scaler_sha256` (VERIFIED — P0-2 scaler binding intact). Widths 70/70, no degenerate std (0 cols with std ≤ 0 or non-finite), so `ScalerBundle.is_ready()` = True and the passthrough-degradation branch is not live.
- Pilot provenance: `artifacts/model_generation/pilots/pilot_20260905_000649_report.json` binds model_sha256 `c9982ddd…`, scaler_sha256 `b3c65b65…`, dataset sha `3ae687ea…`, git `6599e4d5`, 34 folds × 10 epochs, seed 42, class_count 3, input_dim 70, behavioral PASS (VERIFIED by read).

## 4. BUG-272 drift sentinel + BUG-271 fingerprint supersession — do they close the BUG-257 remainder?

- **BUG-271** (`5a9e36e3`, PR #190): `ModelLifecycleRegistry.supersede_champion_on_governed_replace` (registry.py:260+) wired at the `_register_active_model(replaced=True)` choke point (live_engine.py:1714-1733). One queued statement: new-fingerprint row → CHAMPION, stale champion → ARCHIVED (append-only); promotable-only (CHAMPION/CANDIDATE/ARCHIVED); refusals explicit (`EMPTY_FINGERPRINT`, `NO_CHAMPION_ROW`, non-promotable successor). Out-of-process rewrite still fails closed (no governed row carries attacker bytes). VERIFIED by code read + `tests/unit/test_bug271_champion_retrain_fingerprint.py` green at HEAD.
- **BUG-272** (`9431edd2`, PR #192): `model_lifecycle/champion_sentinel.py` — pure `evaluate_champion_drift` mirroring the boot anchor's sha16 comparison (MATCH/DRIFT/INERT), I/O `probe_champion_drift` reading the newest CHAMPION row off the tick path; wired in `MaintenanceCycle` at 900 s cadence with a **two-consecutive-sighting** rule (absorbs governed-writer queue races) → CRITICAL log + Telegram, ALERT-ONLY (enforcement stays at boot, INV-015). VERIFIED by code read + executed probe on real registry/artifact → `MATCH` + `tests/unit/test_bug257_champion_drift_sentinel.py` green.
- **Do they close the BUG-257 remainder?** Functionally yes for the two named gaps: governed writes are attributable and no longer self-kill at next boot (BUG-271 closes the false-refusal kill-switch); out-of-process between-boot rewrites are now *detected within ≤2 sentinel cycles* instead of only at the next boot (BUG-272 closes the detection gap). Remaining OPEN pieces of BUG-257, by design: (a) the **writer identity is still unattributed** (sentinel flags, does not identify); (b) sentinel is **alert-only** — trading continues on drifted bytes until the next cold boot unless an operator halts (explicit governance choice, documented in module docstring); (c) the §2 registry-metadata mislabel. So: *remainder closed at the detection/attribution level; identification + auto-halt remain open, deliberately.*

## 5. live/inference.py trained-mode gate + bar-ts sequence fixes

- **Trained-mode gate (REPLAY-DEFECT-A):** `LiveSequenceService.maybe_build_sequence_tensor` returns `None` unless `state.trained_mode.startswith("sequence")` (live_sequence.py:98); `rebind_from_meta` binds mode from the artifact meta (live_engine.py:505). Champion meta has **no `trained_mode` key** (VERIFIED by read) → mode `"2d"` → the 2D path always runs, matching how the champion was trained. Gate + `bar_ts is None → return None` both present. VERIFIED green: `test_replay03_2d_trained_never_builds_sequence`.
- **Bar-ts plumbing:** inference.py:252-265 parses `fv.timestamp_utc` into `_bar_ts` and passes it (never `None` by intent; the raw tick ts is floored to its minute downstream).
- **REPLAY-04:** minute-floor `ts_us -= ts_us % 60_000_000` present (live_sequence.py:113-121); `test_replay04_sub_minute_ticks_collapse_to_one_bar_entry` green (VERIFIED run).
- **REPLAY-DEFECT-B** (post-gap window rebuild one-bar-late, wave-1 pinned RED): **CLOSED** — the sticky `gap_invalid` starvation gate was removed and the boundary bar is now appended like any fresh bar (live_sequence.py "RE-ARM ON FIRST FRESH BAR" / "STICKY GATE REMOVED" comments, landed `7fbb8d34`), matching `SequenceBuilder`'s rows 40..47 window. `test_replay01_post_gap_window_rebuilds_like_dataset` (asserts `live == dataset == 47`) is **green at HEAD** (VERIFIED run; wave-1's "pinned RED" status is stale).
- Test battery executed at HEAD (VERIFIED): `test_agent5_replay_gap_parity.py`, `test_live_sequence_characterization.py`, `test_bug271_…`, `test_bug257_…sentinel`, `test_champion_bundle_recovery_contract.py`, `test_model_load_integrity.py`, `test_agent3_champion_registry_sync.py` → **62 passed, 0 failed**.

## 6. streaming_replay clip-route decision (wave-2 question)

- Coded route today (`streaming_replay.py:582-589`, VERIFIED): `np.clip((x - mean) / (std + 1e-8), -5.0, 5.0)` in f64 — i.e. replay **does** clip. Trainer route `WalkForwardTrainer._transform_features` (walk_forward_trainer.py:1766-1771) clips `[-5, +5]`; live route `ScalerBundle.transform` (live_engine.py:196-200) clips `[-5, +5]` in f32.
- Wave-2's "replay route unclipped, owner decision owed" premise was **retracted by wave-3** (taskboard TASK-REPLAY-SHADOW-VALIDATION wave-3 row): the wave-2 probe had omitted the clip on its own offline leg; re-probe with the actual coded transform → max prob diff 1.79e-7, **0 argmax flips over 99,946 rows**; the "0.88% flips" figure is formally retracted.
- **Is it still open? No — no owner is needed.** Trainer, live, shadow `scale_like_champion` (CHG-0046 D6) and streaming_replay all clip to [-5,+5]; the contract is consistent. Only residual deltas: f32-vs-f64 and the `+1e-8` eps, both measured immaterial (≤1.8e-7). This lane independently confirms the code state; the recorded probes are the quantitative source.
- Still-open replay-parity deviation from the same waves (different axis, NOT clip): **REPLAY-DEFECT-C** — mid-price convention (replay feeds mid=(bid+ask)/2, offline dataset feeds mid=close) diverges 4/10 liquidity dims (idx 60/61/65/66), max prob divergence 4.2e-3, 0 argmax flips on 246 bars; decision owned by dataset/replay contract owners, still unclosed.

## 7. News-dims constant problem — quantified from artifacts (read-only)

- Champion scaler (VERIFIED by numpy read): `mean[50..59] = 0.0` and `std[50..59] = 0.001` (the std floor) for **all 10 news slots**; total floored columns = **11/70**: feat_9 (`rapid_reversal_spike`) + feat_50..59. Liquidity slots 60..69 are real (std 0.057–2.70).
- Root cause on record (`artifacts/model_generation/datasets/70d_clean_build_report.json`, VERIFIED): `"news_family": {"status": "FEATURE_DISABLED", "reason": "no real news coverage over bars window 2026-05-01..2026-08-17 (news db covers 2026-08-21..2026-09-06); neutral zeros per contract — synthetic news forbidden"}` → the training frame's news block is identically zero over all 99,946 rows (Agent-3 wave-2: "all 10 dims ≡ 0.0 in the artifact … 0 analyzed articles inside the bars window").
- **What the model receives:** z-news = (x−0)/0.001, then clipped to ±5. Any live news value with |x| > 0.005 saturates to ±5.0 (VERIFIED projection path `build_news_10` → transform/clip). Training never produced a non-zero value in these columns.
- **Weights-level evidence (VERIFIED, new probe):** `input_projection` absmax per block — base `0.1745`, liquidity `0.1535`, **news `0.11947` vs the untouched `nn.Linear` init bound 1/√70 = 0.11952**: no news column moved beyond (decay-shrunk) init bound, consistent with zero gradient ever flowing through them; base/liquidity clearly exceed it.
- **Serving consequence:** live news adds **no information** the champion can use — worse, with `news.enabled: true` (configs/base.yaml:82-83) and news.db now populated (post 2026-08-21), live news states *saturate dead inputs to ±5*, a strictly out-of-distribution perturbation of a trained path (train-time value ≡ 0). Directional effect unknown but unbounded in principle per-slot; at minimum it is a documented fidelity hole, not a zero-effect no-op. Fix class: news-coverage retrain prerequisite (Agent-3 §6, MLFix §9.2), not a serving-code patch.

## 8. Economic usefulness — numbers on record (no fabrication; provenance per row)

| Evidence | Numbers | Provenance |
|---|---|---|
| Strategy-factory research (all 4,330 runs, 2026-08-21..08-27) | 3,766 REJECTED / 561 INCONCLUSIVE / **3 VALIDATED**; OOS expectancy mean **−0.1104R**, median −0.1211R, range [−1.3047, +1.0979]; 564/4330 runs OOS>0 | `artifacts/audit.db.research_runs` (executed read-only; count 4330 matches task brief) |
| The 3 VALIDATED runs | RUN-1E1222 +0.0268R OOS (n=39 fam. samples), RUN-168007 +0.0718R (n=45), RUN-5241A7 +0.1308R (n=25), ds_4da4f5c6b1e365bd | same table — **predate the P0-4 split-integrity gates (relended 09-11); tiny samples; strategy families, NOT the champion model** |
| Champion walk-forward record | 34 folds, 26,947 trainable rows, gate `EVIDENCE_WRITTEN`; `benchmark_70d_liquidity.json`: `trainable_rows 0`, walk-forward is the self-asserted string `"PASS (purged walk-forward completed)"` | `artifacts/model_generation/three_model/retrain_70d_liquidity_result.json` — exactly the P0-4 contamination pattern the 09-11 reland retired (challenger string-gate now refused); **no honest OOS economic metric is recorded for the serving weights** |
| 70D multi-seed economics probe (2026-09-11, 4-fold/24k-row/3-seed) | OOS acc 0.5622/0.5634/0.5629 vs majority 0.5634; bal-acc ≈0.333 vs majority 0.709; net expectancy **mean −0.482R, std 0.366, range [−0.836, −0.104]**; trades/seed 3–22 < 30 evidence floor; `economics_quotable: false` | `artifacts/model_generation/pilots/agent4_multiseed_20260911_124252.json` (VERIFIED by read) |
| Agent-3 wave-2 verdict | all arms lose after friction: **−0.55..−0.8R/trade, PF 0.18–0.32**; model-free Kruskal: base informative, liquidity marginal, news zero; "genuinely weak signal, not a plumbing defect" | `docs/forensics/t70d_data_signal_forensics_2026-09-11_wave2.md` §1/§6 |
| 8-cell benchmark (2026-08-31, scalp_v2 M5, 2,946 rows) | A–D all REJECTED (calibration ece 0.19–0.23 > floor 0.15); arm E CHALLENGER_ELIGIBLE (oos_acc 0.8238 but **balanced_acc 0.3456 ≈ NO_TRADE collapse**); arms F–H REJECTED | `artifacts/model_generation/three_model/model_benchmark_report.json` (VERIFIED by read; pre-P0-4 era) |
| Brier / calibration gates from prior waves | **No Brier value recorded on any champion artifact** (grep across artifacts JSON + report docs); Brier appears only as a *gate target* ("ECE ≤ 0.05 target, Brier reported", MLFix F4/F6) — not executed for `c9982ddd`; pilot report carries behavioral stats only (mean_max_prob 0.4122, logit_std 0.2704) | `docs/forensics/MLFix.md:197/208`, `artifacts/model_generation/pilots/pilot_20260905_000649_report.json` |
| Live decision-quality context | CONFIDENCE_FAIL blocked 29.7% of 1,469 paper signals (436) — model outputs rarely clear entry thresholds | COORDINATION.md master probe (lane 07 corroborates direction only) |

## 9. Defect register (this lane)

1. **REG-METADATA-MISLABEL (LOW-MED, open, pre-existing):** governing CHAMPION row (id 4156) declares `scalp_v1@50D` for the 70D `c9982ddd` artifact; champion_sync uses class-level bootstrap dims (active schema scalp_v1/50) for its truth comparison, so it NOOPs the contradiction forever instead of healing it. Fingerprint safety unaffected; provenance/UI/retrain identity is wrong. Owner: model-lifecycle/champion_sync.
2. **NEWS-DEAD-INPUTS (MED, open, prerequisite-class):** 10/70 scaler slots constant (mean 0, std floored 1e-3) + feat_9 dead (11/70 floored total); training saw zero news; live with news enabled can saturate the dead slots to ±5 (OOD perturbation). Fix = news-coverage retrain (Agent-3 §6), not serving patch.
3. **ECON-EVIDENCE-GAP (HIGH for usefulness, open):** no honest OOS economic metric on record for the serving bundle; the only quantified probes say weak/negative expectancy with insufficient trade evidence (`economics_quotable: false`); Brier/ECE gates never run for the champion. Walk-forward gate record for 70d_liquidity is the retired self-asserted string.
4. **REPLAY-DEFECT-C (OPEN, owned by dataset/replay contract owners):** mid-price convention divergence on 4 liquidity dims (documented, not silently aligned). Clip-route (wave-2 question) is RESOLVED-NO-CHANGE.
5. **BUG-257 residual-by-design:** writer identity unattributed; sentinel alert-only (no auto-halt). Boot anchor + supersession + sentinel close the detection/attribution remainder; auto-halting on live drift is a governance decision not yet made.

Closed/verified-this-pass: Appendix-R byte drift (restored, anchor + sentinel guard), REPLAY-DEFECT-A (trained-mode gate), REPLAY-04 (bar floor), REPLAY-DEFECT-B (fixed `7fbb8d34`, test green at HEAD — wave-1 "RED pinned" record stale), BUG-271/272 landing (tests green), scaler/schema-hash bindings (hashes match), BUG-269 docker twin (landed `baf737ab`).

## 10. Verdict

**Is the serving model correct?** YES — bytes: the served artifact is the governed 70D `c9982ddd` champion, integrity VERIFIED, meta/manifest/dataset/pilot hashes coherent, 3-head 70D tensor matches declared contracts.
**Is train-serve parity intact?** YES at the inference-parity level: same schema hash, same scaler, same clip route in trainer/live/replay, 2D serving matches 2D training, sequence gates inert-but-correct, 62-parity battery green at HEAD. Registry *metadata* is mislabeled (defect 1) — a truthfulness defect, not a parity/safety one.
**Is it economically informative?** NO. Zero honest positive-economics evidence for the champion; the probes on record (Agent-3/Agent-4, Sept 2026) show weak-to-negative OOS expectancy below evidence floors, news inputs dead, and the walk-forward "gate" string retired by P0-4 without a re-run replacement. The serving path is trustworthy plumbing wrapped around an edge that has not yet been proven — the next retrain (now safely governable via BUG-271) must carry ≥1y data + real news coverage + executed ECE/Brier/walk-forward economics, or paper-only status should be stated explicitly.
