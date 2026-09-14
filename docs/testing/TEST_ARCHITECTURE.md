# NSE Test Architecture — Radical Reconstruction (2026-09-14)

> Owner: NSE-Swarm main-mind (test reconstruction). This file is the canonical
> tier contract. `beforePush.ps1`/`check_local.py`/`gate_parity.py` and the
> GitHub workflows all consume the manifests named here. Changing a tier means
> changing this file AND the manifest AND the gate-parity contract together.

## The question this system answers

> "Can we trust this change not to silently break NSE?"

Not "is CI green." Every Tier-1 file must name a realistic defect it catches;
the ones that cannot are demoted, merged, rewritten, deleted-with-replacement,
or converted to a runtime check. Registry of what the gate actually protects:
`docs/testing/critical_regressions.md` (CR-001..CR-036). Machine evidence:
`docs/testing/test_inventory.json` (481 files scored), measured costs in the
runs under `scratch/recon/` (this branch) and the CI timing table in
`docs/testing/CI_COST_REPORT.md`.

## Tier model

| Tier | Manifest | Runs on | Budget | Content |
|---|---|---|---|---|
| **T0** static | (ruff/mypy/`check_workflows`/`gate_parity`/`classify_changes`) | every PR + push | seconds–~2 min | lint, format, types, CI self-integrity |
| **T1** PR-critical | `tests/critical_suite.txt` (143 files) | PR + every push (ubuntu via `ci.yml:quality`; Windows skew via `tests-os.yml`) | **≤5 min, measured 2.1 min local / ~6 min CI incl. install** | deterministic P0 production-truth: risk math, execution/idempotency, DB economic integrity, model artifact, market data, PAPER/LIVE, startup |
| **T2** extended | `tests/extended_suite.txt` | main push (`ci.yml:extended`) + release SHA (`release.yml:gates`) | ~15–20 min | high-value but wall-expensive DB boot-heal batteries (measured 1252s/730s/428s) |
| **T3** cross-platform | `tests/windows_skew_suite.txt` (PR) + full `critical_suite.txt` on Windows/macOS (main/schedule) | PR: Windows skew subset; main push + Monday 07:17 + dispatch: full | PR ≈3–4 min/leg | OS-specific defects (paths/CRLF/launcher/installer); macOS is advisory-drift, never ships |
| **T4** release/forensic | `tests/slow_suite.txt` + heavy-ci arms + `run_mutations.py` + `e2e_client` | nightly (01:17/02:33) + ci-tests branch + release dispatch | 30–60 min | chaos, mutation campaign, docker client journeys, PG provider matrix |

## Why the old shape was wrong (measured, not asserted)

- The same `critical_suite.txt` ran **three times per PR** (ubuntu + Windows +
  macOS), both OS legs **required** merge checks. GitHub run 34866523254
  (2026-09-14 main push): ubuntu CI 6 min, Windows 16 min, macOS 5–14 min.
  Merge latency was set by the slowest non-Linux torch install re-running an
  already-proven manifest.
- `docs_only`/lane machinery (`classify_changes.py`) existed and was *sanity-run*
  but consumed by **zero** jobs.
- `qa-deep-assurance.yml` fired a Windows runner + full torch install on every
  PR/main push to run an ~8-second battery (not even a required check).
- `heavy-ci` arms re-ran `integration/` files the integration arm already
  covered (heavy-vs-heavy duplication), and Tier-2-worthy-but-valuable tests had
  **no lane at all** outside ci-tests/dispatch.

## What changed

1. **Tier-1 cost curation.** The three files at 65% of the old critical-suite
   wall time (`test_bug276_unique_constraint_shadow` 1252s,
   `test_perf_deadletter_skeleton_repro` 730s,
   `test_news_db_truncation_honesty` 428s aggregate worker-seconds) moved from
   T1 → T2, where T2 now **really runs** (main push + release SHA). Nothing was
   dropped; the PR lane got 5.2x faster (measured 662s → 127s local).
2. **29 tests promoted INTO the PR gate** (forensic PROMOTE-GATE minus one
   already-present, plus 4 new recon batteries; revert of
   `test_packaged_db_and_mode_bug146_149` measured, see "Remaining risks"),
   aggregate promoted cost ~20 worker-seconds: risk circuit breakers,
   reversal-bypass,
   margin enforcement, order-write uncertainty, pending recovery, DB durability
   + boot-trust, execution idempotency, PAPER/LIVE boundary/lineage/replay/
   marketplace, model producer/champion-writer/label/leakage, market-data
   integrity. Critical-path coverage of the dangerous behaviors went **up**.
3. **Five new reconstruction batteries** written against verified zero-test gaps
   (TDD: `scripts/testing/mutation_check.py`, **9/9 mutations KILLED**):
   `test_recon_risk_boundary_battery`, `test_recon_wal_crash_durability`,
   `test_recon_batch_atomicity_real_failure`,
   `test_recon_duplicate_deal_across_restart`, `test_recon_requote_and_reconcile`.
4. **CI tiers wired.** `ci.yml` gained an `extended` (T2) job on main push;
   `release.yml:gates` stopped duplicating ruff/mypy/critical (release-auth
   already proved them on the SHA) and runs the T2 extended suite + release
   smoke instead; `tests-os.yml` PR lane is the Windows skew subset with macOS
   always-report; the full OS suites run on main/schedule/dispatch.
   `qa-deep-assurance.yml` PR/push triggers removed.
5. **Evidence-driven deletions** (each justified in `test_value_matrix.md`,
   replacement named): 5 mock-noop/environment-dead files removed;
   `test_playwright_e2e.py` moved to `tests/manual/` (superseded by e2e_client).
6. **Fast-failure hygiene.** All manifests path-verified
   (`verify_critical_suite_manifest` → `CRITICAL_SUITE_MANIFEST_OK: 143 paths
   all exist`); `test_hist_sim_golden.py` removed (imported a module that never
   existed on `main` → broke every full-suite collection).

## Tier-1 membership contract (append rules)

A file joins `tests/critical_suite.txt` only if ALL hold:
1. exercises real production code (not a mocked NSE internal);
2. deterministic (no wall-clock/sleep/network; controlled randomness only);
3. named defect answer ("what real bug catches it") recorded in
   `critical_regressions.md`;
4. measured cheap (per-file worker-seconds in `test_inventory.json`);
5. for P0 safety families: a KILLED mutation in `mutation_check.py` or
   `run_mutations.py`.
Never add: a file that only asserts its own mock returns a configured value,
an import-existence probe, a constant echo, or a cosmetic snapshot.

## Merge-velocity contract (event → what runs)

```
normal push (feature branch, no PR):   T0 + T1 (ubuntu ci.yml:quality)
PR to main/develop:                    T0 + T1 ubuntu + T3 Windows-skel + T3 macOS skip-report
push to main (post-merge):             T0 + T1 + T2 extended + T3 Windows-FULL + T3 macOS-FULL
merge candidate / ci-tests / dispatch: + T4 heavy-ci arms
nightly (01:17/02:33):                 T4 slow/chaos/mutation + docker client E2E
release tag v*:                         release-auth (all 11 SHA-proven) → gates(T2 extended+smoke) → build → publish
Monday 05:13 / 07:17:                   weekly CI-integrity meta-scan + OS drift sweep
```

Branch protection is UNCHANGED by this branch (all 11 required contexts still
report — Windows/macOS satisfy their contexts via the lane report, fail-closed
if pytest never ran). The `Extended Validation (Tier 2)` job is NON-required on
PRs (it doesn't run there) and is executed on the release SHA by
`release.yml:gates`, so a red Tier-2 blocks the tag. If protection is ever
edited to make the macOS PR-lane report non-green, update
`release_auth_gate.REQUIRED_MAIN_CI_CHECKS` and
`artifacts/forensics/branch_protection_required_checks.json` atomically (the
drift test `test_required_list_matches_live_branch_protection` enforces sync).

## Remaining risks / next wave

- **396 of 481 files are UNREVIEWED** (no forensic verdict survived context
  after the subagent-report salvage recovered 110). Policy: an UNREVIEWED file
  is never deleted; it inherits a tier by machine signals. Completing the
  judgment is the next wave's first task (rerun the forensics lanes →
  `scripts/testing/classification_overrides.json`).
- **Deferred merge groups** (accounting-aggregation, incidents, shadow70,
  news_bridge, observability, golden_characterization) are recorded with anchors
  but not consolidated — consolidation needs per-case triage, not blind folds.
- **`test_smoke_self.py::test_full_smoke_under_budget`** fails locally with 37
  checks (<40) but is green on CI ubuntu — a machine-artifact-tiered count. It
  was ALREADY red in the pre-reconstruction baseline (not introduced here);
  fixing means making the smoke check-complement environment-independent, not
  lowering 40. Open item, not silenced.
- **`test_packaged_db_and_mode_bug146_149.py`** promotion REVERTED after
  measurement: order-dependent (green in the xdist full-unit census, red
  standalone against this machine's `LEGACY_UNVERIFIED` artifact). Needs
  rewrite onto a provisioned starter bundle before it earns Tier-1.
- **RuleX matrix**: none exists (grep workflows+scripts = 0 hits), so §39 has
  nothing to trim; the OS-matrix policy above is the operative cross-platform
  gate. If a rule matrix is ever added, follow the T3 tiering (never full-matrix
  on a normal PR).
