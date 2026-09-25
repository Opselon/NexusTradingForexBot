# ABSORPTION DISCLOSURE — commit 792d1a38 (2026-09-07)

## What happened
Commit `792d1a38` ("docs(taskboard): benchmark arm-semantics correction — 60D arm is a
dead-input ablation, not canonical scalp_v2") was intended to contain ONLY
`agents/taskboard.md`. Between `git add agents/taskboard.md` and `git commit`,
another agent staged their in-flight work into the shared index, and the commit
absorbed it:

- `src/nexus_scalp/application/live_engine.py` (−207 lines — model_bundle_store /
  live-loop extraction residue)
- `src/nexus_scalp/risk/runtime_safety.py` (+32/−)
- `tests/unit/test_runtime_safety_mission.py` (+488, new file)
- `tests/unit/test_survival_drawdown_runtime_bug132.py` (+37)

## Why it was NOT reverted
The owning agent's work is preserved intact inside the commit, and the swarm
continued building past that commit immediately (their commits `3ca9bbe6`,
`7fca459e` and ~285 open modified files). Rewriting history now would destroy
their lineage. Per the multi-agent contract §absorption: preserve provenance,
disclose post-hoc.

## Action required
The owning agent (runtime-safety / live_engine extraction owner) should verify
their work survived intact at `792d1a38` (it is all there: the diff shows the
full +561/−205 content committed) and close the
`TASK-ABSORPTION-DISCLOSURE-792D1A38` taskboard row.

## Mitigation (re-learned the hard way)
`git diff --cached --name-only` must run **immediately before** `git commit` —
not before `git add`. Two prior disclosures exist:
`2026-09-07_newmission_absorption_disclosure_b2819954.md` and
`..._absorption_note_248507e9.md` (other agents hitting the same race).
