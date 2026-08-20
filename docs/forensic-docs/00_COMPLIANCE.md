# 00 — Compliance & Decision Record

## The conflict

The documentation brief (2026-08-20) mandates a line-by-line documentation pass over
~255 source files, adding engineering comments to every file (file-level, class-level,
function-level).

The in-repo `agents/skill.md` — which the brief itself designates as the Primary Source
of Truth — states at line 7:

> **ABSOLUTE DIRECTIVE:** READ-ONLY for codebase files. Do NOT modify any file in this
> repository except `agents/skill.md` and `agents/bugs.md`.

The MASTER MULTI-AGENT CONTRACT (v2, 61 sections; in-repo mirror
`agents/multi-agent-git-contract.md`) reinforces non-destructive collaboration rules:
preserve unknown work, no destructive git operations, additive registries only,
commits carry agent identity + verification state.

## Decision (2026-08-20, user was asked and did not respond within the time limit)

**Deliver the complete forensic documentation pass as a dedicated artifact tree under
`docs/forensic-docs/`, without modifying any codebase file.**

Rationale:

1. The brief's own authority hierarchy makes `skill.md` the source of truth; its
   READ-ONLY directive is explicit and absolute.
2. 31 files currently carry parallel-agent uncommitted WIP (git status 2026-08-20);
   editing hot-path files (live_engine, order_manager) in that environment risks
   absorbing or clobbering another agent's work (documented hazard, TASK-13).
3. The multi-agent contract mandates preserving unknown work and never rewriting
   shared files without ownership.
4. The full documentation value — purpose, layer, responsibility, dependencies,
   data flow, algorithm intent, edge cases, risk — is delivered per-file in
   `modules/`, with an aggregated architecture map, system-flow reconstruction,
   issues ledger, and verification report.

Documents produced:

- `01_ARCHITECTURE_MAP.md` — full mental model per the brief's Phase 1.
- `02_SYSTEM_FLOW.md` — runtime/ML/risk/execution/backtest data lifecycles.
- `modules/*.md` — per-file forensic documentation (438 files).
- `03_BATCH_REPORTS.md` — phase/batch reports (files completed, discoveries, inconsistencies).
- `04_ISSUES_LEDGER.md` — architectural risks, hidden bugs, technical debt.
- `05_VERIFICATION_REPORT.md` — build/test/lint status, zero-change proof.

## Scope reality vs brief

| Brief claim | Repository reality (AST-verified 2026-08-20) |
| :--- | :--- |
| 255 files | 438 Python files (288 src incl. 17 release pkg, 141 tests, 7 scripts, 2 root) + 6 Web assets + 2 config YAMLs = 446 artifacts |
| "comment every file" | All files analyzed; documentation delivered as sibling pages (above) |
| "modify code" | None (zero src/test/Web changes — see commit diff) |