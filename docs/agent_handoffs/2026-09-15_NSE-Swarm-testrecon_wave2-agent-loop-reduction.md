# Handoff — Test-Architecture Reduction Wave 2 (2026-09-15)

**Agent:** NSE-Swarm main-mind (test-recon wave 2, continuation of #202) ·
**Branch:** `nse/test-architecture-wave2` (from `main@d8276dd4`) ·
**Status:** complete, awaiting PR.

## Mission (user brief, verbatim objective)

> Maximize engineering/agent flow speed while preserving the tests that
> protect real production behavior. Continue the evidence-driven approach —
> do not restart it. No deleting first and justifying later; no metric
> gaming; uncertain ⇒ classify/demote/document, never delete.

## What changed (all measured, this 8-core dev box)

| lane | before (#202) | after (wave 2) |
|---|---|---|
| **T0 FAST (agent loop)** | none — `fast_suite.txt` unwired, 28-file grab-bag | **25 mutation-proven P0 owners, 20–26 s wall, wired into `check_local.py` fast stage (-n 4)** |
| **T1 PR-critical** | 143 files, 127 s, 1,931 tests | **163 files, 141 s, 1,768 tests, 0 failed** (+22 CR-named owners @ +50 worker-s; −11 measured-heavy demotions @ −74 worker-s; net worker-s 542→525) |
| **T2 extended** | 3 files | **14 files, 81 s** (all demotions still run: main push + release SHA) |
| forensic verdict coverage | 110/481 files | **~340 reviewed + 64 CR-PROTECTED** (machine ledger `docs/testing/test_protection.json`) |

Deletions (5 files, §14-verified safe): 4 merged incident/forensic source
files (superset counts proven: 108=87+21 into `test_forensics`, 92=66+26 into
`test_incidents`, all unmanifested with zero CR rows) + the A4
dead-letter delegation mirror. Silent-zero-collection fix:
`tests/cli/test_docs_consistency.py` had **no test functions** (pytest collected
nothing) → real T1 test + `main()` kept for script use. CI meta-gates
(`tests/ci/*`, 5 files) promoted into T1 — they police the tier system itself
and previously ran nowhere.

## Manifest-drift root-caused and fixed

The wave-1 lanes discovered the five `test_recon_*` batteries and several
CR-named owners were cited by `critical_regressions.md` but present in NO
manifest — protected-on-paper, never-executed. All promoted. Registry cells
made truthful: CR-013 (packaged_db rewrite-pending, carried by manifested
owners), CR-036 (landed filename bug278).

## Invariants held (§2/§26/§35)

- **Zero production `src/` changes** — test architecture + CI only.
- No test deleted for "makes CI faster": every deletion lists
  reason + evidence + replacement coverage (overrides `delete_evidence_driven`).
- Nothing hidden: every demoted file still runs on main push (T2); a red T2
  blocks the release tag.
- Disclosed findings NOT papered over (all in
  `classification_overrides.json → wave2_findings_open`):
  1. `test_rejected_candidate_not_persisted::test_accepted_candidate_still_persists`
     — deterministic local RED on pre- and post-#199 trees (early-stop restore
     vs accept-persist contract; never CI-run because never manifested). Filed
     for the trainer owner; file kept on disk as the repro; NOT promoted.
  2. `test_packaged_db_and_mode_bug146_149` promotion blocked (order-dependent
     vs LEGACY_UNVERIFIED artifact) — rewrite onto bug269 provisioner bundle.
  3. `test_launcher_paper_boundary_bug212` (101.7 s) — split mandate deferred.
  4. `test_dependency_cache` kept: its cached==fresh equivalence IS the pin.

## Fixture-census recoverable-seconds map (next wave, all evidence in overrides)

Blessed-DB seed + per-test `sqlite3 Connection.backup` (~1.9k worker-s across
bug276/deadletter/migrations/platform files — the engine's own `_backup`
pattern; helper goes in `tests/helpers/db_seed.py`, module-scope seeds, write
tests get private copies, conftest autouse isolation makes this safe);
module-scoped `create_app` (350 s, live_state_contract — note create_app
implicitly boots an AuditRepository, 12.5 s cold); materialize-once git trees
(350 s, gate_bypass/check_local lanes); single-transaction news seed (300 s).
None touch production; none delete assertions.

## Files (authority map)

- `docs/testing/test_protection.json` ✳ — machine delete-protection ledger
  (PROTECTED=CR-named+rule / REVIEWED / UNREVIEWED=never-delete-without-evidence);
  rebuild with `scripts/testing/build_protection_ledger.py`
- `docs/testing/TEST_ARCHITECTURE.md` — tier table updated + **Runtime-Critical
  Test Contract** section + agent-loop commands + wave-2 delta
- `docs/testing/CI_COST_REPORT.md` — wave-2 section (§33 metrics, §16 model)
- `docs/testing/critical_regressions.md` — CR-013/036 truthfulness fixes
- `tests/fast_suite.txt` T0 · `tests/critical_suite.txt` T1(163) ·
  `tests/extended_suite.txt` T2(14)
- `scripts/ci/check_local.py` — fast stage now consumes the T0 manifest (-n 4)
- `scripts/testing/{merge_verdicts,build_protection_ledger}.py` ✳,
  `classification_overrides.json` (wave2_summary/findings)

## Reproduce

```
python scripts/ci/check_local.py                     # gate incl. FAST 20-26s
pytest $(grep -v '^#' tests/critical_suite.txt|grep -v '^$'|tr '\n' ' ') \
       -q -n auto --dist loadgroup                   # 141s / 1768 / green
pytest $(grep -v '^#' tests/extended_suite.txt|grep -v '^$'|tr '\n' ' ') \
       -q -n auto                                    # 81s / green
python scripts/ci/verify_critical_suite_manifest.py  # 163 paths all exist
python scripts/ci/gate_parity.py --json              # PASS
python scripts/ci/check_workflows.py --strict        # 0E 0W
```

## Next-agent bootstrap

1. Land the fixture-census optimizations (db_seed helper first — biggest win).
2. Rewrite the three blocked promotions (packaged_db, launcher split,
   rejected_candidate forced-improvement fixture), re-promote with measurements.
3. Execute the deferred merge groups (6 recorded with anchors in overrides).
4. Triage `wave2_findings_open` item 1 as a production question (trainer owner).
5. Remaining ~121 UNREVIEWED: keep forensics lanes until zero.
