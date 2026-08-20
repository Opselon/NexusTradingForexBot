# tests/unit/test_forensic_monitoring_task11.py

- GUARDS: TASK-11 POST-70D forensic monitoring — TEST-MONITOR-01..36: the permanent invariant set (docs/POST_70D_RUNTIME_INVARIANTS.md) + continuous forensic health engine (src/nexus_scalp/forensics/).
- KEY ASSERTIONS:
  - five-level status vocabulary (CRITICAL..PASS); schema drift / deadness / flood / causal canary detection; dataset parity + model scaler contract; DB integrity; accounting divergence; duplicate outcome; impossible excursion (historical warning, new critical); experience gap; worker no-progress; news degradation; liquidity frozen/flood; shadow-attach; governance/champion identity; UI/API canonical state; web bundle; telegram; trace/correlation; silent fallback; chart wrongness; MT5 status; runtime mode; growth; queue; perf; release-preflight gate; change-impact selection; snapshot; throttling + read-only checks (136 asserts).
- PITFALLS IT ENCODES: CRITICAL is never averaged away by a worst-status merge; UNKNOWN never ranks below CRITICAL; checks are strictly read-only (engine has no mutation API); deploy gate blocks on critical.
- NOTES: 30+ monitor classes; the largest test in the slice by requirement count (1396 lines).
