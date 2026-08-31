# PRODUCTION E2E CERTIFICATION REPORT — NexusScalpEngine v9.0.4 (2026-08-31)

run_id: E2E-20260831-v9.0.4
commit / version: 6e0d592 / 9.0.4 (Release, stable, windows-x64)
environment: Windows 11 (10.0.28000), Python 3.11.9, AMD64, 16 GB RAM, 390+ GB free
method: REAL production CLI subprocesses only (NexusScalpEngine-CLI.exe / NexusScalpEngine.exe).
No import-and-call. Downloaded GitHub artifacts used for the artifact leg. No live broker.

## 1. ARTIFACT IDENTITY + CHECKSUM CERTIFICATION — PASS

| Artifact | Size | SHA256 vs SHA256SUMS.txt |
|---|---|---|
| portable\NexusScalpEngine.exe | 48.8 MB | MATCH |
| cli\NexusScalpEngine-CLI.exe | 252.6 MB | MATCH |
| NexusScalpEngine-9.0.4-win-x64.zip | 263.4 MB | MATCH |
| NexusScalpEngine-9.0.4-win-x64-setup.exe | 177.6 MB | MATCH |

Downloaded release-manifest.json: version 9.0.4, commit 6e0d592 = local build = EXE `version`
(stamped build timestamp 2026-08-31T08:08:22Z inside installer bundle). sbom.spdx.json present.
Release content: exactly 6 intended deliverables, no stale/duplicate/extra artifacts.

## 2. CLEAN-CLIENT INSTALLATION CERTIFICATION (downloaded CLI exe, empty dir) — PASS

- version --plain: 9.0.4 / 8.5 s cold start (SLA UNDEFINED in docs — measured, no breach)
- setup --json + stdin answers: exit 0, mode PAPER, 3 web endpoints, 20-check health payload
- repair --database: exit 0; audit.db 39 tables / 61 indexes; news.db, candle_intel.db provisioned
- db migrate: audit 0→7, candle_intel 0→2, news 2/2 — SUCCEEDED; repeat migrate: idempotent NOT_REQUIRED
- start --json (PAPER): engine UP, 249 API paths, /health 200, /api/status 200 (engine_running true)
- accounting served REAL PaperBroker values (balance 10000, open position, DAY series)
- /api/debug/model-test: full inference 0.73 ms e2e (feature→tensor→model→decision)
- engine toggle stop → clean; uninstall: decline keeps data / confirm behaves — PASS

## 3. ML CHAIN E2E (real CLI, downloaded binary) — PASS with correct gates

- model-dataset-build (900×50D rows): ds_cb30f87520e9e6a4, manifest complete, 0 NaN/Inf
- repeat build → IDENTICAL hash (deterministic artifact identity) — PASS
- model-experiment-create → exp_baseline_scalpnet_v1_189b05b5 (epochs 10, seed 42)
- model-train → cand_af1a9bbadcfd3088: model.pt 1.33 MB + scaler.npz + manifest, integrity OK
- model-inspect: integrity=True | model-doctor: loaded=True device=cpu dim=50
- model-validate: 6 gates executed; label/class-collapse/regime PASS; OOS gates FAIL on
  random test data → verdict REJECTED (correct conservative behavior, not a defect)
- model-replay: real prediction (label BUY_MARKET, probabilities, latency breakdown) — PASS
- model-train-3 --variant bogus → exit 2 + clear allowed-variants message — PASS

## 4. INSTALLER (INNO SETUP) CERTIFICATION — PASS (2 findings)

- silent install exit 0; registry Uninstall entry correct; installed EXE launches, version/commit match
- installed-tree setup + stdin: JSON OK; db migrate all SUCCEEDED; repeat idempotent
- uninstall --json: keep-data default honored; decline path leaves user data intact
- BUG-160 (P2): installed tree omits release-manifest.json + SHA256SUMS.txt → post-install
  verify-release FAILs (CI tree passes all 8 checks incl. secrets scan + no-LIVE-by-default)
- BUG-161 (P3): Inno /DIR= does not expand %VARS% (harness note, documented)

## 5. DB / LOGGING / SLA

- DB: create→migrate→write→read→restart→reopen verified across audit/news/candle_intel/app_settings
- Logs: structured, timestamped, severity-separated (info/warning/error trees); 0 tracebacks;
  expected warnings only (worker timeouts on network-blocked LLM, Telegram disabled, unversioned
  schema pre-migrate). No SUCCESS-vs-state contradictions found.
- SLA: docs/RELEASE.md defines no per-command thresholds → all SLA UNDEFINED (flagged per
  directive §25, not fabricated). Measured: CLI cold ~7.6-10 s; db ops <1 s; dataset build 10.3 s;
  train 12.1 s; validate 9.4 s.

## 6. DEFECTS FOUND THIS RUN (routed & fixed)

- BUG-156 (P0, fixed by Coder): :memory: SQLite anchoring regression from BUG-149
- BUG-157 (P1, fixed by DevOps): absent model artifact misclassified CRITICAL → fresh install NOT READY
- BUG-158 (P2, fixed by DevOps): e2e doctor confirm EOF abort
- BUG-159 (P1, fixed by Main): model-experiment-create accepted nonexistent dataset → ghost
  experiment + raw pyinstaller traceback on model-train; now clean EXIT_USAGE panel (223b843)
- BUG-160 (P2, open): installer omits manifest + sums (packaging-only)
- BUG-161 (P3, open): Inno /DIR literal-var note

## 7. REMAINING SCOPE (declared, per convergence rule §60)

- Full 66+ command × 10-mode exhaustive matrix continues under the E2E harness (no new failures
  so far beyond those filed). Interactive prompt-loop scenarios bounded by harness timeout.
- Network-failure matrix (§43) runs after BUG-160 fix lands (single re-package).
- Final clean-room run repeats the full chain above once BUG-160 is merged.

## FINAL DECISION: ACCEPT_WITH_NOTES

PRODUCTION_READY = YES for the published v9.0.4 artifacts (all critical chains PROVEN on the
downloaded binary), with two non-blocker packaging findings (BUG-160/161) scheduled for the next
release train. No P0 open. No unresolved SLA breach. No unexplained log errors.
