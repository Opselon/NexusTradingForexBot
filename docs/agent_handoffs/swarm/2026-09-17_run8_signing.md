# NSE Swarm — 2026-09-17 run8 Security

Status: **FIXED-VERIFIED (existing implementation; stale blocker closed)**.
Item: AUDIT-B5-SIGNING / UPDATE-SIG-OPERATOR.
Baseline: `2d67f4f589ab7a12df332a1fa4103b310aa39e93`.

## Pre-change audit and decision

Shared checkout is clean but belongs to foreign `release-9.0.14` at
ca45db8c; fetch reports 0 ahead / 4 behind origin/main. Its upstream was
deleted, so no pull/checkout/reset was attempted there. Work is isolated in
`/tmp/nse-swarm-r8` based on origin/main. PR250 (official models) and PR247
(first setup) were open: neither was modified. Read master contracts,
invariants, registry context, taskboard, repository state and locks.

The highest-priority security ledger item B5 says signing is absent. That
claim is stale. Existing code already performs the relevant work:

- `src/nexus_scalp/release/signing/trusted_keys.py:25-29`: embedded public root.
- `src/nexus_scalp/release/signing/update_manifest.py:187-246`: Ed25519 verify;
  `:249-298`: signature-first digest/size binding against downloaded bytes.
- `src/nexus_scalp/release/update_engine/discovery.py:427-501`: real signed
  asset fetch/attachment; `:807-825`: plan-stage verification and fail-closed
  classification. The fetch repair was already landed in 4700e482 (#163).

No duplicate implementation or test was added. This is a verification and
ledger correction cycle, not a newly implemented security fix.

## Executed evidence

1. `gh run list --branch main --commit <full baseline SHA> --limit 100`:
   CI, Security, OSV, Dependency Lock & Migration Safety, Docs, JS Tests,
   Tests (OS Matrix) all completed success. Summary workflows excluded.
2. `PYTHONPATH=src:. /tmp/NexusTradingForexBot/.venv-linux/bin/python -m pytest
   tests/unit/test_signed_update_manifest.py
   tests/unit/test_signed_manifest_fetch_s1.py -q`: **28 passed, rc0**.
   Existing tests include valid signatures, malformed/unsigned rejection,
   tamper detection, fetch failures and actual plan-builder wiring.
3. Downloaded real `v9.0.14/update-manifest.signed.json` from GitHub, verified
   with the unchanged production embedded root: **verified=true**,
   key_id `2026-09-root`.
4. Actual `UpdateDiscovery.fetch_releases` plus
   `UpdatePlanBuilder(installed_version='9.0.13', architecture='x64').build`
   fetched real checksums + signed manifest and returned
   **UPDATE_AVAILABLE / SIGNED_MANIFEST_OK**.
5. Downloaded the complete published `NexusScalpEngine-9.0.14-win-x64.zip`.
   `verify_payload_against_manifest` returned **verified=true**, size
   **271107575 bytes**, SHA-256
   `422544eaa76a49afa5639da872270f2f6b4ba72819196d501683dffd4e98d004`.
   No mocks, test roots or generated payloads used for these live probes.

Raw captured metadata/verdicts are in sibling
`2026-09-17_run8_signing_evidence.json` and mirrored under
`artifacts/swarm_agent/reports/`. The temporary downloaded ZIP was not
extracted or executed. No install, engine launch, model promotion, MT5 or
trading action occurred. The private signing key was never accessed.
Authentic publication disproves the old missing-key blocker for this
release; it does not inspect or guarantee the current secret inventory.

## New OPEN follow-up — SIGNED-RELEASE-ID (P1, release-owner)

Observed production mismatch:
- Signed manifest `release_id`: **35124718835**.
- Actual GitHub release API `id`: **390121165**.
- `.github/workflows/release.yml:459` sends `${{ github.run_id }}` as
  `NSE_RELEASE_ID`.
- `scripts/release/sign_update_manifest.py:43,67-70` stores that value as
  `release_id` without renaming it to build/run identity.
- `src/nexus_scalp/release/update_engine/discovery.py:186-199` records actual
  API release identity; `:810-814` verifies signed digest, not equality of
  those identity fields.

This is a proven identity-contract mismatch, not proof of an exploit or
an installation bypass. Repair publisher/consumer semantics together:
separate build-run identity from GitHub release identity; define backwards
compatibility for already published manifests; add fail-closed identity
contract tests after that decision. Do not blindly add equality checking:
it would reject the currently published valid release. Workflow edits are
forbidden to this cron; this item is parked with the release owner.

## Closeout gate and delivery

The canonical `beforePush.sh` started successfully but selected the broad
25-file fast suite with four xdist workers even for this docs-only diff.
After approximately five minutes it had a dead worker and no completion;
the run was explicitly stopped. Memory pressure is suspected, not proven.
No full-gate green is claimed. The 28 targeted signing/fetch tests were
re-run afterwards and passed again; `git diff --check` passed.
The evidence/report commit is retained locally; no push or merge is claimed.
Next cycle must resume this local closeout before selecting new work.

## Limits and next cycle

B5 closes ONLY absent signing/fetch. No blanket certification of all update
identity/authorization/installation boundaries. Authenticode/SmartScreen B4
remains operator-owned and is distinct from Ed25519 package verification.
No source, workflows, secrets, gateway contract or test changes.
Next role: 9 Observability; resume report landing if still pending, then
select the highest-priority actionable observability item.
