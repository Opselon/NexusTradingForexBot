# Forensics & Recovery — Index

> **Purpose:** Canonical navigation for the 2026-09 recovery/forensics chain (read-only evidence, not duplicated content). For repository hygiene and provenance, this directory is the single entry point — the root forensic markdown files remain at the repository root and are linked here, not copied.

## How to use this index

1. **Start** at `agents/skill.md` (architecture map) and `README.md` (project overview).
2. **Then** come here for recovery timeline, document purpose, source commit, and current status.
3. **Each** row below points to the existing file at its real path (relative to repo root).

## Recovery timeline (key commits)

| Order | Commit | Title | Role |
|-------|--------|-------|------|
| 1 | `979624fc` | `docs(forensics): 6 independent stash audits (one agent per stash)` | Provenance: 6 independent stash audits |
| 2 | `29a8ebb9` | `Nexus-Main: ruff format heal on forensic_recovery_20260904/agent3-stash2-report.md` | Formatting heal for audit output |
| 3 | `345932e3` | `docs(forensics): recovery-kit 979624fc — baseline + stash matrix + artifact/contract/readiness + security + main integration` | Kit: bundled baseline + matrix + artifact/contract/readiness + security + integration |
| 4 | `f1ef7cb9` | `docs(forensics): final disposition — drop 6 audited stashes, Pages state, cleanup conclusion` | Disposition: drop of 6 audited stashes, Pages state snapshot |
| 5 | `796e0bfb` | `docs(forensics): postmortem PARTIALLY_TRAINED + continuation + final model conclusion — 34x10 worker 25032 still hot` | Postmortem: worker 25032 still hot |
| 6 | `d995bd65` | `fix(ml): live engine online trainer + CLI train-model default off champion path (P0 fix follow-up)` | Current baseline `HEAD` (2026-09-04) — P0 wave follow-up |

> **Provenance dir:** `forensic_recovery_20260904/` — 24 tracked files (`agent*-report.md` + `stash-{0..5}.patch` / `-tracked.patch` / `-parent.txt`), 9.5 MB; classified as **EVIDENCE** (archived raw stash exports).

## Top-level forensic / recovery documents

| File | Status | Purpose | Source commit / relationship |
|------|--------|---------|-------------------------------|
| [`FORENSIC_BASELINE.md`](../../FORENSIC_BASELINE.md) | **HISTORICAL** | Pre-recovery baseline snapshot (pre-`979624fc`) | Superseded by `recovery-kit 345932e3`; keep as baseline evidence |
| [`FORENSIC_RECOVERY_REPORT.md`](../../FORENSIC_RECOVERY_REPORT.md) | **EVIDENCE** | Summary of the 6-stash recovery operation | Parent of `forensic_recovery_20260904/` reports |
| [`STASH_INTEGRATION_MATRIX.md`](../../STASH_INTEGRATION_MATRIX.md) | **REFERENCE** | Decision matrix: 0× INTEGRATE, 2× DUPLICATE (stash@{3,4}), 3× OBSOLETE (0,1,5), 1× CONFLICTING (stash@{2}) | Built from read-only `/tmp/stash-{0..5}.patch` verification; no further stash integration needed |
| [`STASH_FINAL_DISPOSITION.md`](../../STASH_FINAL_DISPOSITION.md) | **HISTORICAL** | Final drop sequence for the 6 audited stashes | Companion to `CLEANUP_FINAL_CONCLUSION.md` |
| [`CLEANUP_FINAL_CONCLUSION.md`](../../CLEANUP_FINAL_CONCLUSION.md) | **REFERENCE** | Disposition conclusion + Pages/model accounting | Closing report of the recovery chain |
| [`GITHUB_PAGES_FINAL_STATE.md`](../../GITHUB_PAGES_FINAL_STATE.md) | **REFERENCE** | Pages deployment verification after P3 | References `site/_site` state at `4261c3d2`/`a5e2ccc4` |
| [`MAIN_INTEGRATION_REPORT.md`](../../MAIN_INTEGRATION_REPORT.md) | **REFERENCE** | Integration report for the recovery-kit into `main` | Consolidated view of `345932e3` contents |
| [`RECOVERED_AGENT_WORK_SUMMARY.md`](../../RECOVERED_AGENT_WORK_SUMMARY.md) | **EVIDENCE** | Summary of agent work recovered during the operation | Supporting evidence, not guidance |
| [`CONTRACT_AUDIT_REPORT.md`](../../CONTRACT_AUDIT_REPORT.md) | **ACTIVE** | Contract audit (production contracts, current guidance) | Remains **ACTIVE** — not purely forensic; keep prominent |
| `MODEL_ARTIFACT_FORENSICS.md` | **EVIDENCE** | Model artifact forensics (near duplicate of next) | See `MODEL_…` family below |
| `MODEL_READINESS_REPORT.md` / `MODEL_RETRAIN_POSTMORTEM.md` / `MODEL_CONTINUATION_REPORT.md` / `TEAM_FINAL_MODEL_CONCLUSION.md` | **EVIDENCE/HISTORICAL** | Model lane forensics chain | Historical evidence of T70D/model work; not current runbook |
| `SECURITY_AUDIT_SEC_CAPITAL_DATA_BROKER.md` | **REFERENCE** | Security audit finding snapshot | Keep as reference, not active policy |

## Model / T70D forensics inside this directory

| File | Status | Notes |
|------|--------|-------|
| `t70d_master_forensic_report_2026-09-03.md` | **EVIDENCE** | T70D master forensic report (gap/lineage/data quality synthesis) |
| `t70d_data_quality_gap_audit_2026-09-03.md` / `t70d_gap_safe_data_audit_2026-09-03.md` / `t70d_data_lineage_audit_2026-09-03.md` | **EVIDENCE** | Gap and lineage sub-audits |
| `gap_handling_report.md` | **REFERENCE** | Gap handling contract report (shared by dataset/train/validation/OOS/live; `max_gap_us=600s`) |
| `obs_perf_resilience_2026-09-04.md` | **REFERENCE** | Observability / perf / resilience report (G29/31 aware) |

## Agent work provenance (worktrees & branches — preserved, not deleted)

* **Repo-local worktrees** (`.worktrees/subagent-sa-*`, 5): kept — see hygiene report for classification (`KEEP` / `CANDIDATE-FOR-PR` / `FORENSIC-EVIDENCE`). No `git worktree remove` was performed.
* **External temp worktrees** (`C:/Users/.../Temp/*`, `C:/tmp/*`, ~14 detached HEAD): kept, not pruned.
* **Recovery branches** `pinc-stash-rescue` + `nse/checkpoint/mt5-pipeline-stash-20260903`: kept, not deleted/merged.

## Agent/ vs agents/ — resolution

* `agents/` (lowercase) is the **canonical** registry (`skill.md`, `bugs.md`, `contracts.md`, `runtime_invariants.md`, `change_control.md`, `taskboard.md`, …).
* `Agent/` (capital) is the legacy companion set (`PROJECT_GRAPH.md`, `ARCHITECTURE_CONTRACT.md`, …) retained as history and referenced by `Agent/skill.md` via the path contract. Both directories are **kept** — no blind merge, no deletion.

## Cross-reference map (new engineer / new agent)

```
README.md → agents/skill.md → docs/forensics/README.md (this file) → historical evidence
```

* `README.md` top-level overview and quickstart
* `agents/skill.md` authoritative architecture & forensic badges (canonical map)
* `docs/forensics/README.md` recovery/forensics navigation (history → evidence → current guidance)

## Preservation rules

* Do not delete `forensic_recovery_20260904/`, `_backup_portable_*`, `artifacts/`, `scratch/`, `Agent/`, `agents/`.
* No `git reset --hard`, `git clean -fdx`, `git stash drop/clear`, `git branch -D`, `git worktree prune`, or `git push --force` on this hygiene task.
* For any consolidation that moves files, use `git mv` and update references — no history squashing.
