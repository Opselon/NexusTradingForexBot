# DEC-0009 — Manual-installer trust model (setup.exe vs signed update manifest)

- Status: PROPOSED (decision requested from the operator/repo owner; this record
  documents the current state and options — it changes no code).
- Date: 2026-09-12 · Author: recovery/c4-signed-manifest-fetch lane (S1).
- Related: `agents/decisions/DEC-0006-*`, DEC-0008; A-7 release-security audit
  finding 1 + finding "manual path"; `docs/ARTIFACT_TRUST_CHAIN.md` §3/§7;
  `src/nexus_scalp/release/update_engine/discovery.py` §6b;
  `.github/workflows/release.yml` (Sign update manifest step);
  `scripts/release/sign_update_manifest.py`.

## 1. Plain statement of today's trust chain

**The manual `setup.exe` path is NOT in the updater trust chain.**

Two distinct install routes exist:

| Route | Signature (Ed25519) verified? | SHA-256 verified? | What anchors trust |
|---|---|---|---|
| Automatic updater (`nexus update`) — portable zip payload | **YES** — `UpdatePlanBuilder.build` §6b refuses any release whose `update-manifest.signed.json` does not verify against the embedded trust root (`signing/trusted_keys.py`, key `2026-09-root`); post-download `orchestrator.run` step 4 re-hashes the artifact against the signed digest | YES (checksum asset + signed digest + payload re-hash) | The embedded Ed25519 public key — independent of the GitHub release bytes |
| Manual download of `NexusScalpEngine-<ver>-win-x64-setup.exe` + human double-click | **NO** — `release/verify.py` has no signing check; `orchestrator._verify_payload_manifest` verifies only the *unsigned, self-hosted* `release-manifest.json` inside zips and is unconditionally SKIPPED for `-setup.exe` payloads | Only if the human chooses to compare `SHA256SUMS.txt` — and that file ships INSIDE the same release, so it is same-party (attacker-controllable) evidence | Trust in the GitHub web UI / transport only |

Consequences that follow directly from that table:

1. A compromised publisher account (or a malicious release asset replacement) can
   ship a `setup.exe` that no automated machinery will ever reject. The signed
   manifest covers only the zip payload (`sign_update_manifest.py` is invoked
   with the `NexusScalpEngine-$VERSION-win-x64.zip` artifact), so even the signed
   asset says nothing about the exe's bytes today.
2. This is *documented policy* (`docs/ARTIFACT_TRUST_CHAIN.md` §7: binaries are
   unsigned by design until a code-signing certificate is purchased), not an
   implementation accident. This record makes the boundary explicit and asks for
   a decision, per the S1 brief: **manual-installer trust is documentation +
   operator decision, not code.**
3. The automatic path's gate was fail-closed but *unsatisfiable* until this PR
   (S1): discovery now fetches `update-manifest.signed.json` in production
   (`SignedManifestResolver`), so `UPDATE_AVAILABLE` is reachable for a release
   that carries a valid signed asset.

## 2. Options for closing the manual-path gap

**Option A — Authenticode code signing (cert purchase + `signtool`/Azure TMVL).**
Sign `NexusScalpEngine.exe`, `NexusScalpEngine-CLI.exe` and the Inno `setup.exe`
in the release pipeline; Windows SmartScreen and `signtool verify /pa` become
real, user-visible checks; the existing P3 readiness gate
(`NSE_REQUIRE_CODE_SIGNING=true` in `release.yml`) already refuses to publish
unverified EXEs when enabled.
- Pros: the only mechanism that protects the *manual double-click* moment
  (nothing in a browser download runs our Ed25519 client logic); OS-level trust
  UX; long-term the industry norm for Windows installers.
- Cons: cost (OV/EV cert or cloud HSM key), process (cert issuance/rotation),
  supply-chain of the signer itself. Operator decision + budget.

**Option B — Signed-manifest verification AT INSTALL time (no cert).**
Extend `sign_update_manifest.py` to emit a second manifest row covering the
`setup.exe` bytes (or add the exe's sha256 to the signed document), and have the
Inno installer's `[Run]` post-install step (or a `nexus verify-release` the docs
tell users to run) call `verify_payload_against_manifest` against the embedded
trust root, refusing to complete on failure.
- Pros: zero purchase cost; reuses the existing trust root and verifier; closes
  the "same-party SHA256SUMS proves nothing" hole for users who run it.
- Cons: it is opt-in *after* the binary already ran at least once (Inno
  pre-install has no place to verify bytes the installer itself is executing);
  SmartScreen still warns unsigned; protection strength depends on the user
  following the documented verify step. Meaningfully weaker for the true
  double-click threat model, useful as an interim.

**Option C — Status quo (documented-unsigned).**
Keep `docs/ARTIFACT_TRUST_CHAIN.md` §7 as-is: the manual path is explicitly
outside the signature trust chain; only the automatic updater is trusted.
- Pros: honest, zero work.
- Cons: the manual route remains the attack downgrade path (an adversary who
  cannot beat §6b tells the victim to "just download the installer").

## 3. Recommendation

**A is the target state; B is the interim this lane recommends approving next.**
Purchase/adopt Authenticode (Option A) as the definitive manual-path anchor.
Until then, adopt Option B's pipeline change (sign the setup.exe digest into the
signed-manifest lane and wire a post-download verify that covers it) so the
documented trust chain at least *binds* the installer's bytes to the same Ed25519
root even when enforcement is user-mediated. Do not claim in docs that the
manual path is signature-protected before one of these lands.

## 4. Operator action required (blocking the update lane to go green end-to-end)

The automatic updater's fetch wiring (S1) is now on main-track, but the lane
stays availability-dead until:

1. **Configure the repository secret `NSE_UPDATE_SIGNING_KEY`** (GitHub →
   Settings → Secrets and variables → Actions → repository secrets). Value = the
   32-byte Ed25519 signing seed in hex, the private half of the embedded public
   key `2026-09-root` in `src/nexus_scalp/release/signing/trusted_keys.py`.
   The seed must be generated OFFLINE by the operator (e.g.
   `python -c "import nacl.signing;print(nacl.signing.SigningKey.generate().encode().hex())"`
   on an air-gapped machine — the matching public key is ALREADY embedded in the
   client trust root, so any other seed requires a trust-root code change +
   release).
2. **Cut the first signed release (v9.0.12 or later).** `release.yml` hard-fails
   the sign step without the secret (`sign_update_manifest.py` refuses to publish
   an unsigned manifest), so until step 1 is done every release run dies there —
   fail-safe by design. Once v9.0.12 publishes with `update-manifest.signed.json`,
   clients behind the §6b gate can install it automatically.
3. Existing releases v9.0.3..v9.0.11 carry NO signed asset and will remain
   permanently `SECURITY_BLOCKED` to the automatic updater. That is correct
   (they predate the trust root); do not "fix" it by weakening §6b.

**Secret hygiene (non-negotiable):** `NSE_UPDATE_SIGNING_KEY` is configured ONLY
via the GitHub UI by the operator. It must NEVER appear in the repository, in
any PR, in workflow logs, in artifacts, or in chat output. `sign_manifest()`
and the sign step already log only derived public info (`key_id`, digest
prefix); keep it that way. Key rotation procedure is documented in
`trusted_keys.py` (append new `key_id`, never remove while old clients need it,
unknown `key_id` is always REJECTED).

## 5. What this S1 change deliberately does NOT do

- No install-time re-verification of the signed manifest in
  `orchestrator.run` step 4 (audit's optional "one extra call") — deferred; the
  §6b pre-download gate + SHA/digest re-hash at verify stage are intact.
- No rollback-path integrity (BUG-262 landed separately via PR #152).
- No signed manifest for `setup.exe` bytes (that is Option A/B above — decision
  pending).
- No new status enums: absent/unfetchable/malformed signed asset leaves
  `update_manifest` unset, and the pre-existing `STATUS_SECURITY_BLOCKED` +
  `signature_status` taxonomy (MANIFEST_MALFORMED / MISSING_SIGNATURE /
  UNKNOWN_KEY / SIGNATURE_INVALID / DIGEST_MISMATCH) does the work; the
  resolver only adds a distinguishable *decision detail* naming
  `update-manifest.signed.json`.
