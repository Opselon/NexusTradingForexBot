# TASK-12 HANDOFF → TASK-13 (continuous 70D monitoring)

**Agent:** AGENT-12 · **Date:** 2026-08-19 · **Branch:** main
**Verdict:** POST_70D_MONITORING_ACTIVE_WITH_WARNINGS

## What the monitor now covers (where/how)
- Engine: src/nexus_scalp/forensics/ (checks.py 34 checks, engine.py,
  deploy_gate.py, experience_gap.py, news_sources.py, telegram_report.py,
  trend.py, references.py, models.py).
- CLI: `nexus forensic [--snapshot|--deploy-gate|--trend|--gap|--report|--json]`.
- Gate evidence: artifacts/forensics/deploy_gate_result.json.
- Persisted snapshots: artifacts/forensics/forensic_health_snapshot.json
  + history.jsonl (bounded, append-only).

## Thresholds / invariants
- INV-70D-001..020 + m47xj8 (docs/POST_70D_RUNTIME_INVARIANTS.md).
- Deploy policy table in deploy_gate.py::DEPLOY_POLICY (doc:
  docs/POST_70D_DEPLOY_GATE.md). CRITICAL always blocks; UNKNOWN never
  passes; engine failure -> FORENSIC_ENGINE_UNAVAILABLE (exit 3).
- Alert throttling in engine.py::ALERT_POLICY (immediate/aggregated 15m/
  periodic 60m).
- Liquidity drift: frozen refs from LIQUIDITY_70D_GOLDEN_BASELINE.json@4455874
  (immutable; replace=True required to re-freeze).

## Known false positives / limitations
- CHECK-FCS-04 + CHECK-LIQ-01 UNKNOWN until a running engine produces live
  70D vectors (correct UNKNOWN, not a defect).
- fed news source classified UNKNOWN — health row is a stale 304 artifact;
  the fetcher treats 304 as success (verify after the next worker cycle).
- web-server endpoint /api/forensics/deploy-gate lives in the working tree
  (shared file; absorbed by the next server push — verify it landed).

## Proven findings for owners (do NOT auto-fix)
1. **CHAMPION_REGISTRY_STALE_ROWS**: 2 stale champion fingerprints
   (0872ae0b, f0f70efb) in experience_model_registry vs disk 9105cef7.
   Owner: TASK-8 governance — recommend BUG entry + registry cleanup.
2. **News 200-but-wrong**: bea/ustreasury HTTP 200, 0 articles ever.
   Owner: Hermes-News — recommend BUG entry (source adapter fix).
3. Historical anomalies (54 excursions + 1 duplicate) immutable; monitor
   flags WARNING historical / CRITICAL on new.

## Which tests protect it
tests/unit/test_post70d_monitoring_activation.py (TEST-POST70D-01..28) +
tests/unit/test_forensic_monitoring_task11.py (TEST-MONITOR-01..36).
Run: `.venv/Scripts/python.exe -m pytest tests/unit/test_forensic_monitoring_task11.py tests/unit/test_post70d_monitoring_activation.py -q`

## EXACT NEXT-AGENT INSTRUCTIONS (TASK-13)
1. Verify `/api/forensics/deploy-gate` landed in origin/main (it was in the
   working tree when TASK-12 pushed — shared-file absorption). If missing,
   re-apply the endpoint (server.py, route near /api/forensics/health).
2. Enable the periodic Telegram report ONLY after operator opt-in:
   set `forensic_report.enabled: true` in configs/base.yaml; verify one
   dry-run cycle with `nexus forensic --report --json`.
3. After the 70D series runs the engine with liquidity features ON:
   re-run `nexus forensic --snapshot` — CHECK-LIQ-01/CHECK-FCS-04 should
   leave UNKNOWN when feature_vectors rows appear. Verify the drift check
   against the frozen golden baseline.
4. When the champion registry stale rows are resolved (owner TASK-8):
   re-run the gate; CHECK-GOV-01/02 should return PASS (registry hygiene).
5. Wire `nexus forensic --deploy-gate` into release.yml CI (in addition to
   beforePush) so CRITICAL blocks CI deployment too.
6. Add trend visibility: expose latest_trend() (forensics/trend.py) on the
   dashboard API when a UI slot opens.
7. Keep the invariant registry additive; never rewrite rows; never let any
   check auto-repair — the immune system observes, classifies, alerts,
   blocks. Humans decide.