# Artifact Trust Chain — Build · Sign · Distribute · Verify · Load · Serve · Update · Rollback

Status: **IMPLEMENTED (P0/P1/P2), evidence-graded.** This document is the
operator-facing contract for the update/artifact trust chain landed by the
P0 security mission (commits `37072a86`, `b1ece040` + absorbed follow-ups).

## 1. Trust root (P0)

The **Ed25519 signature over the canonical update manifest is the trust
root**. SHA-256 checksums (`SHA256SUMS.txt`, asset metadata) are integrity
evidence whose authority comes from being COVERED by a signature. A
compromised publisher account/token that replaces the payload AND its
checksum can never install anything — there is no valid private-key
signature.

* Crypto: **PyNaCl (libsodium) Ed25519** — standard, audited; no custom
  cryptography (`src/nexus_scalp/release/signing/update_manifest.py`).
* Canonicalization: signing payload = `"nse-update-manifest" ⊕ schema
  version ⊕` key-sorted compact JSON of every manifest field except
  `signature`. Same manifest ⇒ same canonical bytes ⇒ same signature
  (deterministic, test-proven).
* Manifest fields (schema 1): `schema, key_id, version, platform,
  architecture, artifact_name, artifact_sha256, artifact_size,
  release_id, signature`.
* Verification order: parse/validate fields → check `signature` present →
  look up `key_id` in the EMBEDDED trust root
  (`release/signing/trusted_keys.py`) → verify Ed25519 → (download stage)
  hash the exact payload bytes and compare to the SIGNED digest + size.
* Fail-closed codes: `MANIFEST_MALFORMED`, `MISSING_SIGNATURE`,
  `UNKNOWN_KEY`, `SIGNATURE_INVALID`, `DIGEST_MISMATCH`, `SIZE_MISMATCH`.
  **No silent unsigned fallback exists.** A release without a valid signed
  manifest is `SECURITY_BLOCKED` at plan time (TEST-UP-SIG-13..15).

## 2. Keys

* PRIVATE key: 32-byte Ed25519 seed, hex. **Never in the repository.**
  Operator escrow: `%USERPROFILE%\.nse-secrets\NSE_UPDATE_SIGNING_KEY.txt`
  (generated offline). CI receives it ONLY via the
  `NSE_UPDATE_SIGNING_KEY` GitHub Actions secret. The signing script
  (`scripts/release/sign_update_manifest.py`) fails the release when the
  secret is absent — an unsigned update manifest cannot be published.
* PUBLIC key(s): versioned in `src/nexus_scalp/release/signing/trusted_keys.py`,
  embedded in every client, selected by `key_id` in the manifest.
* ROTATION: add the new `key_id → public key` to `trusted_keys.py`, publish
  manifests signed by the new key (clients verify per-`key_id`), keep the
  old key only while older clients may still receive its manifests, then
  remove it. Rotation never weakens verification: unknown `key_id` is
  always `UNKNOWN_KEY` → REJECT.

## 3. Release pipeline (build → sign → publish)

`release.yml` order: build payloads → checksums/manifest/SBOM → **Sign
update manifest (Ed25519 trust root)** → client-side re-verify with the
embedded public key → embed release manifest → verify-release → upload/publish
(includes `manifests/update-manifest.signed.json` as a release asset).

Production signing checklist (operator):

1. Configure the `NSE_UPDATE_SIGNING_KEY` Actions secret (repo Settings →
   Secrets → Actions) with the escrowed seed.
2. Tag the release; the workflow signs + verifies + publishes.
3. If the secret is missing the release FAILS at the signing step — this is
   the policy, not an accident.

## 4. Update flow (verify → activate)

`UpdatePlanBuilder.build()` resolves the payload digest (spec 12 rules) and
then verifies the signed manifest against it — metadata-only at plan time;
`SafeDownloader` + SHA-256 verification remain for transfer integrity; the
signed manifest is the authority. The Inno installer continues to embed
`SHA256SUMS.txt` + `release-manifest.json` (BUG-166 pre-stage) — these are
integrity layers INSIDE the trusted payload, not the trust root.

Anti-rollback policy is UNCHANGED (downgrade requires explicit
`--allow-downgrade`; revoked/draft releases never install; version
comparison deterministic).

## 5. Model artifact load path (P1)

`model_lifecycle/load_integrity.py::verify_artifact_integrity` runs in
`ModelBundleStore._load_or_create_bundle` BEFORE weights become the serving
bundle (boot, hot-swap, promotion, rollback all funnel through it):

| Status | Meaning | Default |
|---|---|---|
| `VERIFIED` | digest matches `manifest.json`/`model.meta.json` | serve |
| `LEGACY_UNVERIFIED` | artifact has NO integrity metadata (pre-trust-chain bundle) | **reject**; explicit `allow_legacy_unverified_artifacts` engine opt-in only, always observable |
| `MISSING_METADATA` / `HASH_MISMATCH` / `LOAD_REJECTED` | manifest declared but unreadable / bytes != digest / artifact missing | **reject (fail closed)** |

Every load logs `[ARTIFACT_INTEGRITY]` with status, artifact name (never
absolute paths), expected/actual digest prefixes — integrity status is
observable on every model load (P2).

Fine-tune / collapse-recovery persists rebind sidecars to the NEW digest
atomically (tmp+replace) BEFORE the serving swap
(`LiveEngine._refresh_artifact_integrity_metadata`), so the pair
ACTIVE_ARTIFACT ⇔ ACTIVE_MANIFEST ⇔ ACTIVE_HASH never diverges; a failed
sidecar rebind refuses the activation.

## 6. torch.load guard (P2)

`scripts/ci/check_torch_load_safety.py` walks the AST of every file under
`src/` and fails CI when a real `torch.load(...)` call lacks
`weights_only=True` (comments/strings cannot false-positive). Every
production site is converted; the allowlist is intentionally EMPTY.

## 7. Windows code-signing readiness (P3, evidence-graded)

Current state: production executables are **Authenticode-UNSIGNED** (no
certificate purchased — by instruction). Repository-side preparation:

* Inno Setup `SignTool` support is declarative in the `.iss` (commented
  stage) — enabling it requires only `NESignTool=` + certificate
  configuration at build time; no code change.
* Unsigned status is documented here and in `docs/RELEASE.md` §9; nothing
  claims signed that is not verified (`signtool verify /pa` is the only
  acceptable proof, and it is NOT wired because there is no certificate).
* When a certificate exists: build → `signtool sign /fd SHA256 /tr <TSA>`
  the EXEs → `signtool verify /pa` (must PASS) → package → publish. A
  release must FAIL if `NSE_REQUIRE_CODE_SIGNING=1` is configured but
  verification fails; until then the requirement flag stays unset.

## 8. ONNX / distribution optimization (P3, evidence-graded verdict: NOT YET JUSTIFIED)

Measured context: the serving model is ScalpNet (dual-path MLP/TCN+MHA,
267k parameters, ~0.25 ms single-thread CPU inference at L=32×70 after the
OBS-PERF pin; engine already ships torch for training). Evidence:

* Inference latency is NOT a bottleneck (model forward is a fraction of the
  e2e tick budget; the latency tracer measures it separately).
* Package size is dominated by torch either way — the engine RETRAINS
  online on-device, so torch cannot be removed from the distribution.
* Migration would add an exporter/ORT dependency plus a second serialization
  contract for no measured startup/latency/attack-surface win.
* Reversibility rule: any future spike must be additive (separate artifact
  kind + loader), never replacing the live serving path.

## 9. Test map (attacker model)

`tests/unit/test_signed_update_manifest.py` (TEST-UP-SIG-01..16) and
`tests/unit/test_model_load_integrity.py` (TEST-INT-01..15) cover: valid
signed update, changed payload with matching checksum, tampered manifest
fields, wrong-key signature, unknown key, missing signature, malformed
manifests, size/digest mismatch, plan-level accept/reject, weights modified
after manifest, missing metadata, atomic sidecar rebind, interruption
before activation, unsafe `torch.load` detection (incl. comment immunity).
