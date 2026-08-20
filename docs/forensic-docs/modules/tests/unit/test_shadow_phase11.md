# tests/unit/test_shadow_phase11.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- PHASE 11 Challenger shadow trading & champion evaluation behavioral suite: shadow records persisted, champion unchanged, metrics compared truthfully.
- Basics: champion loads; challenger loads; IDENTICAL inputs → identical outputs; schema mismatch rejected; corrupt artifact rejected.
- Shadow: challenger produces shadow decision; shadow decision CANNOT submit MT5; result marked simulated (`decision is None or decision.simulated is True`); shadow outcomes persisted; champion UNCHANGED during shadow.
- Comparison: metrics compare correctly; small-sample handling (`result is None` — safely skipped, no exception).
- Promotion: promotion path only via the registry/vault gates, mirroring governance.
- Fixtures: `temp_audit_repo` + `flush`; `make_champion_ref`/`make_challenger_ref` artifact builders.
- 44 defs / 553 lines.