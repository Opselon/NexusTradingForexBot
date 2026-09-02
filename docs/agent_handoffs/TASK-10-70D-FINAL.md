# TASK-10 Handoff — 70D Final Forensic Verification / Release Acceptance

> Agent: AGENT-10 · TASK-10-70D-FINAL-FORENSIC · 2026-08-19
> Role: Final End-to-End Verification / Release Acceptance Engineer
> Result: **RELEASE_READY_WITH_DOCUMENTED_LIMITATIONS** (evidence below)

---

## 1. What was verified (PROVEN by executable evidence)

1. **70D FEATURE CONTRACT** — schema_contract.py canonical 70 names,
   family layout (0..49 base · 50..59 news · 60..69 liquidity), schema
   hash stable (235b8fccc96b7e0e); parity suite 14 green.
2. **NEWS FAMILY** — the 70D NEWS block is the canonical first-10
   INCLUDING `news_state` at index 59 (was silently dropped by `[:10]`);
   live + dataset producers agree. Regression tests TEST-70D-NEWS-01..05.
3. **LIQUIDITY FAMILY** — liquidity_engine returns the exact 10 named
   features at 60..69 (finite, bounded); composite assembly strict
   (50+10+10, raises on mismatch).
4. **CAUSALITY** — future-data injection probe: 0.0 diff at decision T
   (engine-level anti-leakage PROVEN).
5. **SHADOW** — shadow70 observability-only, validated-candidate gate,
   truthful NO_VALIDATED_CANDIDATE idle; 57+ tests green; chain now uses
   strict build_70d_vector (no silent pad).
6. **GOVERNANCE** — load gate rejects 70D-claim over 50D artifact
   (INPUT_DIMENSION_VALID); no auto-promotion (INV-015/016).
7. **API/UI truth** — /api/liquidity/* real values, DISABLED → {} (no
   fake), governor reports scalp_v4/70D when enabled; app.js derives
   indices from the backend schema; toggle persists via SettingsDatabase
   (INV-010/BUG-080).
8. **DATABASE** — audit v6 / news v2 / candle v2, integrity ok,
   migrations checksummed + idempotent; historical rows preserved.
9. **SECURITY** — raw exception text (`\"reason\": str(e)`) eliminated
   from the promotion API payload (test_web_security re-verified green).

## 2. TASK-10 commits (visible to all agents)

- 372c69e — news-family projection fix (news_provider.py + tests)
- 0fa1d96 — canonicalize NEWS block + schema id reconciliation
- 75843d4 — strict build_70d_vector in live shadow70 hook
- 7eb5bf2 — governor schema truth + toggle persistence test alignment
- b3c8d35 — forensic probes (causality / parity / baseline evidence)

## 3. Known limitations (PROVEN gaps — NOT hidden)

1. No real 70D dataset/scaler/model artifact → dataset-level parity and
   the fair A/B/C benchmark remain blocked on TASK-03/04 landing the
   artifact; Shadow idle by design (NO_VALIDATED_CANDIDATE).
2. Active Champion = RESTORED_CANDIDATE (bench_a_v1-derived, 50D).
   Original f0f70efb… unrecoverable from this repo (BUG-104). OPERATOR
   DECISION REQUIRED per INV-015: restore from external backup (verify
   hashes) OR approve retrain/promotion.
3. scalp_v3 (canonical 70D per TASK-03) vs scalp_v4 (TASK-02 alt layout)
   dual registration remains; shadow70 restored to scalp_v3. A single
   authoritative 70D schema id is recommended before any 70D model trains.
4. Liquidity CLI commands not implemented (API/UI driven).
5. Packaged 70D release smoke executable not performed at TASK-10 level
   (TASK-9 release pipeline owns packaging).

## 4. Exact next-agent instructions

1. Run the FULL quality gates to completion: ruff check/format, mypy src,
   full `pytest tests/unit tests/integration` (swarm gate), beforePush.
2. Operator: resolve the Champion decision (BUG-104) — restore external
   backup or authorize retrain/promotion per ModelGovernanceEngine.
3. TASK-03/04 owners: land a REAL 70D dataset + scaler + candidate from
   ONE builder run; then re-run TEST-70D-MODEL-01..25 (dataset parity +
   benchmark) and record the OOS result.
4. Reconcile scalp_v3/scalp_v4 into ONE canonical 70D id (recommend
   keeping scalp_v3 per the TASK-03 parity contract; update TASK-02
   governor SCHEMA_70D + release/model_artifacts if so).
5. Then re-run this task's probes (scratch/task10_*.py) and update
   docs/70D_FINAL_FORENSIC_ACCEPTANCE_REPORT.md release verdict.

## 5. Traceability

- TASK-ID: TASK-10-70D-FINAL-FORENSIC (AGENT-10)
- Root: HEAD b3c8d35 on `main` (parallel 70D swarm active; the tree is
  a shared workspace — DO NOT reset/clean unknown WIP; contract §1)
- Evidence: scratch/task10_1..3 + docs/70D_FINAL_FORENSIC_BASELINE.md +
  docs/70D_FINAL_FORENSIC_ACCEPTANCE_REPORT.md