# Forensics & Recovery — Index & Provenance Map

> **Purpose:** Canonical navigation for the 2026-09 recovery/forensics chain (read-only evidence, not duplicated content). For repository hygiene and provenance, this directory is the single entry point — the root forensic markdown files remain at the repository root and are linked here, not copied.

---

## 1. How to use this index

1. **Start** at `agents/skill.md` (architecture map) and `README.md` (project overview).
2. **Then** come here for recovery timeline, document purpose, source commit, and current status.
3. **Each** row below points to the existing file at its real path (relative to repo root).

---

## 2. Recovery timeline (key commits)

| Order | Commit | Title | Role |
|-------|--------|-------|------|
| 1 | `979624fc` | `docs(forensics): 6 independent stash audits (one agent per stash)` | Provenance: 6 independent stash audits |
| 2 | `29a8ebb9` | `Nexus-Main: ruff format heal on forensic_recovery_20260904/agent3-stash2-report.md` | Formatting heal for audit output |
| 3 | `345932e3` | `docs(forensics): recovery-kit 979624fc — baseline + stash matrix + artifact/contract/readiness + security + main integration` | Kit: bundled baseline + matrix + artifact/contract/readiness + security + integration |
| 4 | `f1ef7cb9` | `docs(forensics): final disposition — drop 6 audited stashes, Pages state, cleanup conclusion` | Disposition: drop of 6 audited stashes, Pages state snapshot |
| 5 | `796e0bfb` | `docs(forensics): postmortem PARTIALLY_TRAINED + continuation + final model conclusion — 34x10 worker 25032 still hot` | Postmortem: worker 25032 still hot |
| 6 | `d995bd65` | `fix(ml): live engine online trainer + CLI train-model default off champion path (P0 fix follow-up)` | Current baseline `HEAD` (2026-09-04) — P0 wave follow-up |

> **Provenance dir:** `forensic_recovery_20260904/` — 24 tracked files (`agent*-report.md` + `stash-{0..5}.patch` / `-tracked.patch` / `-parent.txt`), 9.5 MB; classified as **EVIDENCE** (archived raw stash exports).

---

## 3. Top-level forensic / recovery documents classification

| File | Status | Purpose | Notes / Lineage |
|---|---|---|---|
| [`CONTRACT_AUDIT_REPORT.md`](../../CONTRACT_AUDIT_REPORT.md) | **ACTIVE** | Contract audit (production contracts, current guidance) | **ACTIVE** — maps current 50D live vs 70D candidate contracts |
| [`MLFix.md`](../../MLFix.md) | **ACTIVE / CANONICAL** | 70D XAUUSD Model: Forensic Findings, Fix Plan & Future Roadmap | Supersedes `MLFixing.md` (§11.1 full-history reconciliation addendum, 42 KB) |
| [`MLFixing.md`](../../MLFixing.md) | **HISTORICAL / SUPERSEDED** | Earlier ML-lane handoff (done-so-far + future path) | Preserved historical precursor to `MLFix.md` (21 KB) |
| [`MODEL_ARTIFACT_FORENSICS.md`](../../MODEL_ARTIFACT_FORENSICS.md) | **EVIDENCE** | Model artifact forensics (33-pt analysis) | Root entry point; byte-identical to `artifacts/forensics/model_artifact_forensics_20260904.md` |
| [`CLEANUP_FINAL_CONCLUSION.md`](../../CLEANUP_FINAL_CONCLUSION.md) | **REFERENCE** | Disposition conclusion + Pages/model accounting | Closing report of the 6-stash disposition |
| [`STASH_INTEGRATION_MATRIX.md`](../../STASH_INTEGRATION_MATRIX.md) | **REFERENCE** | Decision matrix: 0× INTEGRATE, 2× DUPLICATE, 3× OBSOLETE, 1× CONFLICTING | Built from read-only `/tmp/stash-{0..5}.patch` verification |
| [`STASH_FINAL_DISPOSITION.md`](../../STASH_FINAL_DISPOSITION.md) | **HISTORICAL** | Final drop sequence for the 6 audited stashes | Companion to `CLEANUP_FINAL_CONCLUSION.md` |
| [`GITHUB_PAGES_FINAL_STATE.md`](../../GITHUB_PAGES_FINAL_STATE.md) | **REFERENCE** | Pages deployment verification after P3 | References `site/_site` state at `4261c3d2`/`a5e2ccc4` |
| [`MAIN_INTEGRATION_REPORT.md`](../../MAIN_INTEGRATION_REPORT.md) | **REFERENCE** | Integration report for the recovery-kit into `main` | Consolidated view of `345932e3` contents |
| [`FORENSIC_BASELINE.md`](../../FORENSIC_BASELINE.md) | **HISTORICAL** | Pre-recovery baseline snapshot (pre-`979624fc`) | Superseded by `recovery-kit 345932e3`; keep as baseline evidence |
| [`FORENSIC_RECOVERY_REPORT.md`](../../FORENSIC_RECOVERY_REPORT.md) | **EVIDENCE** | Summary of the 6-stash recovery operation | Parent of `forensic_recovery_20260904/` reports |
| [`RECOVERED_AGENT_WORK_SUMMARY.md`](../../RECOVERED_AGENT_WORK_SUMMARY.md) | **EVIDENCE** | Summary of agent work recovered during the operation | Supporting evidence, not guidance |
| [`PILOT_VALIDATION_RESULT.md`](../../PILOT_VALIDATION_RESULT.md) | **EVIDENCE** | Pilot validation result for time-bounded training | Validates pilot subset before 34x10 full run |
| [`MODEL_READINESS_REPORT.md`](../../MODEL_READINESS_REPORT.md) | **EVIDENCE** | 14-gate model readiness audit | Evidence of 70D candidate gate checks |
| [`MODEL_RETRAIN_POSTMORTEM.md`](../../MODEL_RETRAIN_POSTMORTEM.md) | **EVIDENCE** | Postmortem on partially trained worker state | Forensic trace of worker 25032 |
| [`MODEL_CONTINUATION_REPORT.md`](../../MODEL_CONTINUATION_REPORT.md) | **HISTORICAL** | Next gated sequence after postmortem | Historical handoff for ML retraining |
| [`TEAM_FINAL_MODEL_CONCLUSION.md`](../../TEAM_FINAL_MODEL_CONCLUSION.md) | **EVIDENCE** | Multi-agent model consensus summary | Evidence of 3-class vs 4-head incoherence resolution |
| [`SECURITY_AUDIT_SEC_CAPITAL_DATA_BROKER.md`](../../SECURITY_AUDIT_SEC_CAPITAL_DATA_BROKER.md) | **REFERENCE** | Security audit finding snapshot (SEC-CAPITAL / DATA-BROKER) | Keep as reference, not active policy |

---

## 4. Model / T70D forensics inside this directory

| File | Status | Notes |
|---|---|---|
| `t70d_master_forensic_report_2026-09-03.md` | **EVIDENCE** | T70D master forensic report (gap/lineage/data quality synthesis) |
| `t70d_data_quality_gap_audit_2026-09-03.md` | **EVIDENCE** | Gap analysis on XAUUSD M1 dataset |
| `t70d_gap_safe_data_audit_2026-09-03.md` | **EVIDENCE** | Gap-safe sequence builder verification |
| `t70d_data_lineage_audit_2026-09-03.md` | **EVIDENCE** | Lineage from MT5 ticks to feature frames |
| `gap_handling_report.md` | **REFERENCE** | Gap handling contract report (shared by dataset/train/validation/OOS/live; `max_gap_us=600s`) |
| `obs_perf_resilience_2026-09-04.md` | **REFERENCE** | Observability / perf / resilience report (G29/31 aware) |

---

## 5. Agent work provenance (worktrees & branches)

* **Repo-local worktrees** (`.worktrees/subagent-sa-*`, 5):
  * `subagent-sa-0-8c5a9a11` (`15d97ca2`): **CANDIDATE-FOR-PR** (contains 2102 lines of valuable docs-enhance CSS/JS, candidate for separate docs PR)
  * `subagent-sa-0-9b8b7568` (`adf2d687`): **FORENSIC-EVIDENCE** (PARTIALLY_TRAINED postmortem)
  * `subagent-sa-1-b0a24c30` (`dcdc229f`): **FORENSIC-EVIDENCE** (70D 3-class vs 4-head audit)
  * `subagent-sa-2-628685f3` (`d3d8e11e`): **FORENSIC-EVIDENCE** (contract audit 70D/32/3 vs 50D/4)
  * `subagent-sa-3-fa4df021` (`9c6a2370`): **FORENSIC-EVIDENCE** (STASH_INTEGRATION_MATRIX triage)
* **External temp worktrees** (`C:/Users/.../Temp/*`, `C:/tmp/*`, ~14 detached HEAD): **DISPOSABLE-BUT-PRESERVED** until owner confirmation.
* **Recovery branches** (`pinc-stash-rescue` + `nse/checkpoint/mt5-pipeline-stash-20260903`): **KEEP-PRESERVED** per hard safety rules.

---

## 6. Agent/ vs agents/ — resolution & canonical ownership

* [`agents/`](../../agents/) (lowercase) is the **CANONICAL, ACTIVE** registry:
  * `skill.md` (authoritative master map, §1–§20)
  * `bugs.md` (public bug ledger, BUG-001…)
  * `contracts.md` (active contracts)
  * `runtime_invariants.md` (active system invariants)
  * `change_control.md` (CHG registry)
  * `taskboard.md` (active taskboard)
  * `locks.yaml` (concurrency locks)
* [`Agent/`](../../Agent/) (capital A) is the **HISTORICAL COMPANION** directory:
  * Contains `PROJECT_GRAPH.md`, `ARCHITECTURE_CONTRACT.md`, `AGENT_REASONING_PROTOCOL.md`, `DATABASE_MIGRATION_STATUS.md`, `TEST_OPTIMIZATION_REPORT.md`, and `skill.md` alias.
  * Explained by [`Agent/README.md`](../../Agent/README.md).
  * Both directories are **kept as-is** — no blind merge, no deletion.

---

## 7. Cross-reference map (new engineer / new agent)

```
README.md → agents/skill.md → docs/forensics/README.md (this file) → historical evidence
```

* `README.md` top-level overview and quickstart
* `agents/skill.md` authoritative architecture & forensic badges (canonical map)
* `docs/forensics/README.md` recovery/forensics navigation (history → evidence → current guidance)

---

## 8. Preservation rules

* Do not delete `forensic_recovery_20260904/`, `_backup_portable_*`, `artifacts/`, `scratch/`, `Agent/`, `agents/`.
* No `git reset --hard`, `git clean -fdx`, `git stash drop/clear`, `git branch -D`, `git worktree prune`, or `git push --force` on this hygiene task.
* For any consolidation that moves files, use `git mv` and update references — no history squashing.
