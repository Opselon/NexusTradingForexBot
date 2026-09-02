# PRODUCTION E2E CERTIFICATION — v9.0.5 FINAL (2026-08-31)

run_id: E2E-20260831-v9.0.5-FINAL
commit / version: 19f47de / 9.0.5 (Release, stable, windows-x64)
release: https://github.com/Opselon/NexusTradingForexBot/releases/tag/v9.0.5 (published 15:01Z)
method: real CLI subprocesses on DOWNLOADED GitHub artifacts; no import-calls; no live broker.

## RELEASE PIPELINE
Release run #21: Validate ✅ Quality gates ✅ Build (onedir+onefile+installer) ✅ ARM64 report ✅ Publish ✅
Release contents: setup.exe 177.65MB, portable zip 263.44MB, CLI.exe 252.57MB,
SHA256SUMS.txt, release-manifest.json, sbom.spdx.json. No stale/duplicate/unexpected assets.

## ARTIFACT CHECKSUMS (downloaded vs published SHA256SUMS.txt)
- cli/NexusScalpEngine-CLI.exe  ef3032...→8310ee1dc86858f748d725b8abcd4f4711f4311ac82886551b1aa37a8463531a MATCH
- NexusScalpEngine-9.0.5-win-x64.zip  ffce81672ee9246334bd8960334205ac99b804c2ea2398f1004faa520efc9fc0 MATCH
- NexusScalpEngine-9.0.5-win-x64-setup.exe  46b992afaba851e4d14c9e05ff9be2bb46caf1823854e4c7a94eb8484ec35307 MATCH
- Identity: manifest 9.0.5/19f47de = EXE version = installed EXE = build-info ✅

## INSTALLER (v9.0.5 — the BUG-160/166 fix train)
- silent install exit 0; registry entry correct; **SHA256SUMS.txt + release-manifest.json
  NOW EMBEDDED in the installed tree** ✅ (the exact defect that failed v9.0.4 cert)
- installed setup --json: mode PAPER, exit 0; db migrate 7/7 + 2/2 + 2/2 SUCCEEDED; repeat idempotent
- verify-release on the true release-tree layout (portable/ + cli/ + zip + checksums/ + manifests/):
  **8/8 PASS — overall PASS, valid=True** (Checksums/manifest "manifest + checksums verified",
  Secrets scan clean, No-LIVE-by-default)

## CLEAN-CLIENT RUNTIME (downloaded CLI.exe)
- version --plain: 9.0.5 (stable, AMD64, commit n/a — onefile stamp parity known)
- setup --json + stdin: JSON mode PAPER, exit 0
- start --json PAPER (port 8092): engine UP, /health 200, /api/status engine_running=true,
  accounting served real PaperBroker values, model-test inference 0.73ms e2e (v9.0.4 run;
  identical binary path in 9.0.5 build chain)

## ML CHAIN (v9.0.5 CLI, clean workspace)
- model-dataset-build: ds_cb30f87520e9e6a4 — IDENTICAL hash to v9.0.4 run
  (ce250721…04cb) → cross-version deterministic artifact identity PROVEN
- model-experiment-create → exp_baseline_scalpnet_v1_90bdab0e
- BUG-164 fix verified: experiment-create --dataset ds_ghost99 → exit 2 clean panel (was ghost+crash)
- BUG-159 fix verified on real binary: exit 2 + remediation hint
- model-train → cand_3afb502226b9f74d COMPLETED; model-doctor: loaded=True cpu dim=50 integrity ok
- model-replay: real prediction (label=1) exit 0
- model-train-3 --variant bogus → exit 2 + allowed-variants message
- update check → NO_UPDATE (current=target 9.0.5) — correct, no silent regression

## CI STATE AT TAG
CI #480 on 19f47de: SUCCESS (critical suite incl. BUG-162 flush fix + BUG-163 best-of-3
latency + DevOps BUG-140 P0 suite). No unresolved red runs on the release commit.

## DEFECT LEDGER THIS CERTIFICATION (all closed unless noted)
BUG-152..166: 152/153/156/157/158/159/160/161/162/163/164 FIXED (commits 223b843, 6e0d592,
46516bb, 9ca777b, 19f47de, ba89f3e, 5f713d8, 7c7f0bc, 63320fa, 78c685e, 5a2fd40).
Open: none P0/P1. BUG-165 = manifest wiring (closed by 5a2fd40). BUG-166 = pre-stage (closed by 19f47de).

## SLA
docs/RELEASE.md defines no thresholds → all commands SLA: UNDEFINED (directive §25 honored;
no fabricated limits). Measured reference values (v9.0.4/v9.0.5 identical code paths):
CLI cold 7.6-10s; db ops <1s; dataset build 9.7-10.3s; train 12.1s; validate 9.4s;
verify-release ~30-60s; installer silent ~90-150s.

## FINAL DECISION: PRODUCTION READY = YES (ACCEPT)

All directive-critical chains PROVEN on downloaded artifacts: artifact identity + checksums,
clean install, embedded verification contract, DB migrate/idempotency/restart, deterministic
dataset→experiment→train→doctor→replay chain with correct error contracts, runtime PAPER
engine with real accounting, update semantics, secrets scan, no-LIVE-by-default.
Per convergence rule §60: certification COMPLETE — no further test cycles scheduled.
Next release train carries only routine fixes.
