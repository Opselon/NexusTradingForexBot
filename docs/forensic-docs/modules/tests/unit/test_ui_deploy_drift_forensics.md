# tests/unit/test_ui_deploy_drift_forensics.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- Forensic implementation regression coverage for verified integration/deployment defects (UI drift between registry and bundle).
- Backend: registry row with null score never breaks UI readers (`test_registry_score_never_null_for_ui`); malformed score JSON does not crash the reader.
- Frontend bundle contract: app.js score decoder survives null (`typed.score is None` — decode failure degrades to None, NO crash); news loaders FAIL VISIBLE; rules loader fail visible; rules loader CONSUMES array payloads; tab initializers wired; served app.js IS the source bundle (no stale compiled artifact).
- Release bundle freshness: fresh bundle PASSES, stale bundle FAILS (hash-compare against built assets).
- Registry API: normalized payload contract.
- 18 defs / 261 lines. Pairs with test_research_registry_null_score_bug075.py (backend half).