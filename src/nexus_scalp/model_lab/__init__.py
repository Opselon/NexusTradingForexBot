"""NEXUS MODEL DEVELOPMENT LAB — research lab root (CHG-0047).

ISOLATION CONTRACT (user model-lab brief 2026-09-02):
  * Everything in this package is research-only. No module here may import
    the production serving path (application/live_engine, signals/policy)
    for decision-making, may write under artifacts/models/scalp (the
    Champion's home), or may register anything into a production-active
    lifecycle state.
  * All lab artifacts live under artifacts/models/research/ (teachers/,
    students/, candidates/, rejected/, datasets/, experiments/).
  * The single public surface is ModelLab (facade) + the `lab` experiments
    it drives. Verdict vocabulary: improved | unchanged | regressed |
    inconclusive | candidate — never "profitable"/"production ready".
"""
