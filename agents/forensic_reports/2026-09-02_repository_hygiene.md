# Repository Hygiene Report — 2026-09-02

Agent: Nexus-Main (Role: Repository Hygiene / Evidence Arbiter)
Branch: main | Starting HEAD: a2dc1cb | Remote origin/main at boot: a2dc1cb (0 ahead / 0 behind)
Mode: CLEANUP ONLY — zero behavior change, zero source edits, NO PUSH (user directive).

## Phase 0 — WIP protection (before any classification)
- Foreign WIP found in tree and PROTECTED (never staged, never reverted):
  - `.github/workflows/ci.yml` (gate-parity step, CHG-0049 work)
  - `scripts/ci/check_local.py` (prepush_plan / GATE_INTEGRITY_FILES)
  - `scripts/docs/build_site.py` (locale/theme-boot shell)
  - `agents/APISkill.md`, `scripts/dev/api_skill_drift_check.py` (formatting)
  - later-arriving: `agents/taskboard.md`, `docs/architecture/order_manager_architecture.md`,
    `src/nexus_scalp/execution/order_manager.py`, `site/assets/search.js` (Agent-5 S6 batch, live)
  - untracked `scripts/release/build_artifact.py` (4681 bytes, 16:08 same-day, siblings tracked
    under scripts/release/) -> classified KEEP-active-WIP; NOT deleted, NOT staged.
- Reviewer note: `order_manager.py` diff is a verbatim method extraction (S6), verified
  behavior-preserving by its owner; left untouched per ACTIVE WIP > CLEANUP.

## Phase 1-2 — Inventory & ledger (summary)
- Tree at boot: 5,273 tracked, 1 untracked, 14,668 ignored (incl. .venv 1.2G, artifacts 378M,
  data 42M, scratch 1.5G on disk) — all heavy locals already ignored, left in place.
- Candidates evaluated: 20 logical groups across scratch/, site/_site, docs build output.
- Classification outcomes:
  - REMOVE (tracked, confirmed junk): 470 files / 19 groups (evidence below)
  - UNTRACK: none needed (no accidentally-tracked local artifacts found)
  - PROTECTED: all foreign WIP above + release/build tree (ignored, do-not-touch) + caches
    (.mypy/.pytest/.ruff/.coverage — regenerated tool state, ignored, left in place)
  - REVIEW (not touched): `artifacts/backups/news_v0_20260822T093437.bak` (tracked? no — on-disk
    only) — possible DB backup; owner unknown; recommend separate forensic confirmation before
    any removal. Empty dirs under release/build/_internal are inside the ignored build tree.
  - KEEP-by-design: 376 tracked scratch files (probes/results), site/ source (417), artifacts
    evidence JSONs (14 tracked), docs (667).

## Phase 12 — Removals (deletion-only; carried by commit 56a0316, disclosed by Agent 5)
PATH (group) | TYPE | REASON | EVIDENCE
1. scratch/ci3.zip ci4.zip ci5.zip ci6.zip ci_results.zip ci_results2.zip (~34MB) — CI evidence capture zips — pure artifacts of local CI-replay runs; zero refs outside scratch; binary; content duplicated by ci3..ci6/ci_results* trees; reproducible.
2. scratch/ci2/ ci3/ ci4/ ci5/ ci6/ (52 files) — per-run CI result trees (run-info/ruff/mypy/pytest incl. 100-200KB junit.xml) — raw captures of local beforePush/CI runs; reproducible; zero refs.
3. scratch/ci_results/ ci_results_x/ (26 files) — older CI-results captures incl. 201KB junit.xml — same class; make_ci_results.py output; reproducible.
4. scratch/audit_ml_ast.json (447KB) + audit_ml_digest.json (252KB) — forensic digests of an ML AST audit — generated inventory; zero refs in src/tests/docs.
5. scratch/om_head_now.py + scratch/om_git_view.py (291,314 bytes EACH, byte-identical pair) — stale order_manager monolith snapshots (one git-view, one HEAD copy) — superseded by git history itself; identical-content duplicate; zero refs.
6. scratch/ux_audit_desktop.png (252KB) — UX audit screenshot — evidence captured in UX handoff; not referenced; regenerated on demand.
7. scratch/task4_probe_worker_rebuild.out.txt (313KB) — probe stdout — reproducible output; probe script retained; zero refs.
8. site/_site/ (380 files) — GENERATED docs-site deploy output — tracked since the 784-file
   mass-commit e4924f3; docs.yml builds site/_site fresh in CI (upload path site/_site,
   `python scripts/docs/build_site.py`); check_docs gates it as a BUILD product; identical
   rebuild on every docs change; zero source role. Site SOURCE (site/content, site/assets,
   site/templates, search JS) untouched.

Totals: 470 files deleted, ~35MB tracked weight removed, 0 insertions, 0 deletions of source lines.

## Phase 6 — .gitignore hardening (commit 381730a)
+17 lines, append-only: scratch/*.zip; scratch/ci2..ci6/; scratch/ci_results/; scratch/ci_results_x/;
scratch/audit_ml_ast.json; audit_ml_digest.json; om_head_now.py; om_git_view.py;
ux_audit_desktop.png; task4_probe_worker_rebuild.out.txt; site/_site/.
Verified via git check-ignore (site/_site + scratch/audit_ml_ast.json resolve to new rules).
Deliberately NOT ignored: node_modules (DEC-0002 keeps blanket ignore out; Playwright-only
rules already in place), artifacts/ tracked-evidence files, scratch generally (repo keeps a
tracked probe corpus by design).

## Phase 9/10 — Dead references
- git grep sweeps for every removed basename outside scratch: 0 hits (only live
  `ci-results/pytest/junit.xml` pipeline paths matched the junit query).
- rglob text scan of src/tests/scripts/docs/agents for deleted basenames: 0 hits.
- docs pipeline: build_site.py/check_docs.py treat site/_site as generated output; CI
  (docs.yml) regenerates before upload — no pipeline consumed the tracked copy.

## Phase 13/14 — Validation
- Baseline (pre-cleanup, foreign-WIP state): py_compile of all 5 foreign-WIP .py files RC=0.
- Cleanup diffs: deletion-only (git show 56a0316: 470 D, 0 src/ files) — no production logic touched.
- Post-cleanup: git status D-count = 0 (removals complete); working tree contains ONLY the
  protected foreign WIP + untracked build_artifact.py; ref sweeps clean (above).
- No .py file was modified by this task, so ruff/mypy surface unchanged; scratch corpus,
  fixtures, and tracked evidence untouched; no test re-run required (deletion-only).
- Functional non-change: all changes classified as (a) deletion of confirmed junk,
  (b) .gitignore hardening, (c) this report. Zero API/contract/behavior change.

## Phase 16/19-20 — Commits (NO PUSH, per user directive)
- 56a0316 — 470-file deletion-only junk removal (index was absorbed mid-task by a parallel
  Agent-5 commit; Agent-5 posted disclosure row 373b39d; content verified: 0 src/ paths,
  0 non-D file entries from the junk set).
- 381730a — .gitignore hardening (+17).
- (this commit) — report.
- origin/main at report time: 966852f. HEAD is intentionally AHEAD of origin by these local
  commits; nothing has been pushed.

## Unresolved / handoff
- artifacts/backups/news_v0_20260822T093437.bak — REVIEW, owner unknown.
- release/build/windows-x64/onedir empty subdirs — inside ignored build tree; harmless.
- Foreign WIP (8 modified + 1 untracked files) remains intentionally UNCOMMITTED — owned by
  CHG-0049 / Agent-5 / docs-i18n work streams; this agent did not stage or commit any of it.
- Taskboard row deliberately NOT appended: agents/taskboard.md carries uncommitted foreign WIP
  (Agent-5 section-18 row); appending would have absorbed it. This report is the registry entry.
