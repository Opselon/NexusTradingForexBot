# AGENT HANDOFF — Trade Availability Forensics (zero-trade investigation)

- **Agent:** Hermes-Forensic-02
- **Role:** Deep runtime forensics & trade-availability investigator
- **Task:** Why did the bot not execute trades during 2026-08-19 01:13-02:21Z (host 04:43-05:51 Iran)?
- **Starting HEAD:** 617c23a (origin/main)
- **Ending HEAD:** 617c23a (no commits made — forensic-only; working tree left untouched)
- **Branch:** main

## Commits
None. The dirty working tree contains another agent's in-progress work:
- `src/nexus_scalp/model_lifecycle/integrity.py` + `models.py` + `tests/unit/test_model_lifecycle_phase10.py` (BUG-110 fix: class count from classifier.head, not input_projection width; test_aihub_01..13)
- `src/nexus_scalp/features/liquidity_runtime.py` + `tests/unit/test_liquidity_runtime_integration_phase18.py` (SSE datetime ISO-string fix, BUG-110 SSE)
- `scratch/*` (research probes from other agents)
DO NOT stage/commit these files — they belong to parallel agents.

## Files (this task, uncommitted)
- `docs/TRADE_AVAILABILITY_FORENSIC_FINAL.md` — final report
- `artifacts/forensics/trade_funnel.json` — signal funnel + window + root cause
- `artifacts/forensics/rejection_reasons.json` — policy funnel reasons
- `artifacts/forensics/model_runtime_trace.json` — artifact/tensor/integrity trace
- `artifacts/forensics/config_effective_snapshot.json` — config source matrix
- `artifacts/forensics/log_db_reconciliation.json` — logs vs DB

## Key findings (VERIFIED)
1. **Session 1 (04:43-04:45 host): NO TRADING because of MODEL_INVALID — a verifier bug (BUG-110).** `integrity.py` HEAD reads `input_projection.weight[0]=128` (hidden width) as class count vs expected 4 -> false `INTEGRITY_FAILURE` -> `Champion unavailable` -> no model in runtime. The artifact is VALID: `classifier.weight (4,32)`, input (128,50), scaler (50,). Fix present in dirty tree + regression tests (40 passed).
2. **Session 2 (05:43-05:51 host): NO TRADING because 63/63 evaluations were rejected by intentional gates before Risk/Execution.**
   - ZONE_QUALITY_GATE 34 (conf < 0.60)
   - CONFIDENCE_GATE 14 (< 0.35 ranging / 0.25 trending)
   - REGIME_NO_TRADE 6, ASYMMETRIC_RR 4, SR_MARGIN 2
   - EXPERIENCE_INTELLIGENCE_GATE 3 — the only BUY_LIMIT candidates (predictive OB) killed by DEGRADED strategy lifecycle (conf*0.70 < 0.40 qualify floor). Feedback trap: DEGRADED family never trades -> never accrues experience -> stays DEGRADED.
3. Model/registry/UI all agree: primary_scalp v1.0 scalp_v1 50D (hash 9105cef7d93e23b8). No 70D active model; scalp_v3=70D registered but NOT active; no 70D artifact.
4. Exposure/execution/broker: untouched (0 attempts). Broker history (7516 deals/3639 trades) is historical, not live.
5. All 30 trading rules DISABLED. app_settings.db holds only telegram.enabled. No config overrides.
6. Logs == DB (delta +2 = startup evals; 9 test-artifact execution rows excluded). No unexplained deltas.

## Final verdict
MULTIPLE_ROOT_CAUSES: session1 = MODEL_INVALID (BUG-110, fixed in tree, uncommitted); session2 = NO_EXECUTABLE_SIGNALS (intentional gates + DEGRADED experience gate + legitimate weak signal in ranging regime).

## Unfinished / next-agent instructions (EXACT)
1. **Commit the BUG-110 integrity fix** (integrity.py + models.py + test_model_lifecycle_phase10.py) — verify no other agent already pushed it (`git log origin/main -- src/nexus_scalp/model_lifecycle/integrity.py`); if unpushed, commit with full contract body + `SHARED API CHANGED` tag (new ModelArtifactInfo fields).
2. **Commit the SSE datetime fix** (liquidity_runtime.py + its test) separately.
3. Consider (product decision, NOT a bug): re-calibrate `ai_zone_confidence_threshold=0.60` against the raw-probability confidence change of 2026-08-18 (trade-quality fix) — candidates now carry raw model prob, so 0.60 is nearly unreachable in ranging regimes (2/63 reached it).
4. Consider breaking the DEGRADED feedback trap: PREDICTIVE_LIMIT candidates are the only ones that fire, and their family is permanently DEGRADED because rejects never create experiences. Options: probationary placement, decayed penalty, or recording rejected proposals as experiences.
5. After BUG-110 lands, rerun `beforePush` and re-verify session-2-style live window shows CHAMPION VERIFIED at startup (session1's failure mode disappears).

## Runtime verification
- `pytest tests/unit/test_model_lifecycle_phase10.py` 40 passed
- `pytest tests/unit/test_experience_intelligence.py tests/unit/test_intelligence_phase09.py` 73 passed
- No full-gate run (no code changed; dirty tree has parallel work — do not run gate against mixed tree)
- Git: clean of my changes; `git status` still shows the parallel agents' modified files

## Bugs ledger
BUG-110 entry is owned by the parallel agent (do not duplicate). This report adds no new BUG-NNN.
