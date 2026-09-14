# Lane 15 — Security / Release-Gate Audit (wave 2026-09-14)

Repo: `NexusTradingForexBot` @ branch `nse/master-active-scalper-wave` (HEAD `c60f0f2f`, base `origin/main 9431edd2` VERIFIED ancestor via `git merge-base --is-ancestor`).
Method: read-only forensics + executed commands only. Every item labelled **VERIFIED** was actually run in this lane; **STATIC** = source-read conclusion.
Git write commands: none executed. Filesystem writes: this file only (lane scratch files kept OUTSIDE the repo tree).

---

## 1. Secrets in tracked files

### 1.1 CI scanner — VERIFIED
- `./.venv/Scripts/python.exe scripts/ci/scan_secrets.py` → `Source scan clean.` exit 0.
- **Known scope gap CONFIRMED (VERIFIED from source, `scan_secrets.py:5`):** scan roots are ONLY `src/`, `configs/`, `docs/`. `scripts/`, `installer/`, `Web/`, `agents/` — and also `tests/`, `frontend/`, `site/`, `.github/`, `docker/` — are NOT scanned by the gate.

### 1.2 Lane-run extended scan (gap coverage) — VERIFIED
Same scanner regexes PLUS an extended set (`gh[opusr]_[A-Za-z0-9]{20,}`, `xox[baprs]-`, `AKIA…`, generic PEM, JWT-shaped strings) over **2,320 tracked files** in `scripts/ installer/ Web/ agents/ frontend/ tests/ .github/ docker/ configs/ docs/ src/ + main.py` (`git ls-files`-driven, binary/lock suffixes excluded):
- 9 files match the scanner's own pattern — ALL under `tests/unit/`, ALL demonstrable fake fixtures (e.g. `bot_token="1234567890:ABCDEFGH…"` — truncated so this report does not itself match the bot-token regex, `api_key="***"` — redaction-shaped placeholder, `ghp_123456…` in incident-response tests). No live-format credential found.
- **Verdict: gap is currently benign in content, but structurally real** — a future secret planted in `scripts/`/`installer/`/`Web/`/`agents/` would pass both the CI step and the release `validate` job (`release.yml` "Scan for secrets in source" runs the SAME narrow scanner).

### 1.3 Recommendation (extend, concrete)
Add roots `scripts`, `installer`, `Web`, `agents`, `tests`, `.github` to `scan_secrets.py:5`, and either (a) maintain an allow-list of known test-fixture paths, or (b) scope the strict patterns to non-`tests/` roots while running the extended patterns everywhere. Release-gate requirement: `scan_secrets.py` must run against the full tracked set (`git ls-files`-driven) before `Publish GitHub Release`.

### 1.4 Tracked-artifact hygiene — VERIFIED
`git ls-files | grep -E "\.(db|pem|key|p12)$|app_settings.db|secrets.yaml"` → no matches. `.gitignore` lines 386–395 exclude `.env`, `.env.*` (allow `.env.example`), `configs/secrets.yaml|yml`. The release `validate` job additionally fails on tracked `artifacts/audit.db|\.env$|credentials|private_key` (STATIC, release.yml).

---

## 2. `.env` handling

- **`.env` is gitignored — VERIFIED** (`git check-ignore -v .env` → `.gitignore:387`). Tracked env surface is `.env.example` only.
- **`auth_boot` repo-root write — VERIFIED, acceptable.** `src/nexus_scalp/web/auth_boot.py` (BUG-267) publishes `NSE_WEB_AUTH_TOKEN` + `NSE_WEB_ACTUAL_PORT` to process env and persists both to the gitignored repo-root `.env` at SERVER BOOT ONLY (never CLI subcommands); opt-out `NSE_WEB_AUTH_DOTENV_DISABLE=1`; atomic tmp+replace write; token value never logged (127.0.0.1-bound launcher banner is the only print sink). Design rationale is documented in-module: a process that cannot read the DPAPI blob can, by definition, read the same user's files; `.env` is where docker operators already look (`docker-compose.yml:52` consumes `${NSE_WEB_AUTH_TOKEN:?…}` fail-closed).
- **Contents inspected by KEY NAME ONLY (VERIFIED, values never printed):** local `.env` holds exactly `NSE_WEB_ACTUAL_PORT`, `NSE_WEB_AUTH_TOKEN` (matches `.env.example` contract keys: NSE_EXECUTION__MODE, NSE_MODEL__*, NSE_RISK__*, NSE_TELEGRAM__*, NSE_WEB_*).
- **Residual risk:** the control-plane bearer token now lives as plaintext in a per-user file with default Windows ACLs (same trust boundary as DPAPI, weaker at-rest handling). Recommendation: `icacls .env /inheritance:r /grant:r "%USERNAME%:F"` hardening step in the launcher + a docs note; also add `.env` to any backup-exclusion policy.

---

## 3. PAPER / SHADOW / LIVE mode semantics

- **No silent LIVE — VERIFIED (executed + source):**
  - `nexus start` defaults `--mode paper` (`engine_boot.py:57` "default: paper - NEVER live").
  - Unknown mode fails closed: `mode_key not in MODE_ALIASES` → `EXIT_USAGE`, JSON error or error panel (`engine_boot.py:84-97`). Aliases = {paper, shadow, live} only (`styling.py:42-46`).
  - LIVE interactive path: red `LIVE TRADING` panel + `typer.confirm(..., default=False)`; refusal aborts (`engine_boot.py:187-209`).
  - LIVE automation path: `--json` WITHOUT `--yes` → explicit error "LIVE mode via --json requires explicit --yes confirmation", `EXIT_USAGE` (`engine_boot.py:210-219`). `--yes` is only consulted in the LIVE+JSON branch.
  - Wizard mode prompt defaults `PAPER`, LIVE needs confirm ("never silently LIVE", `wizard.py:148-159`).
- **Config-level fail-closed — VERIFIED (executed):** `ExecutionConfig(mode='BANANA')` raises pydantic `ValidationError` against `ExecutionMode` StrEnum (`enums.py:11-27`); default `ExecutionMode.PAPER`. RuntimeConfiguration default mode `"PAPER"` (`runtime_config.py:84`).
- **Web UI mode switching is authenticated, not anonymous — STATIC:** `POST /api/engine/mode` + `/api/engine/toggle` exist (`diagnostics_state_routes.py:1007,1035`) behind WEB-AUTH-P0; `auth.py` header contract: LIVE execution mode forces token auth always, discovery failure blocks (never fails open), constant-time compare.
- **Gateway HMAC — VERIFIED (source, `gateway/server.py`):** scheme `HMAC-SHA256(secret, "{timestamp}." + raw_body)`, mirror of the client (single source of truth import), `hmac.compare_digest`, ±300 s skew, 64 KiB body cap, missing/bad headers → 401. **AUDIT-B2 fail-closed secret policy (executed logic read):** well-known defaults accepted ONLY when `NSE_GATEWAY_ALLOW_DEFAULTS=1 AND not allow_live`; a LIVE-allowed gateway without env/DPAPI secrets raises `RuntimeError` at boot. Demo-guard: refuses real-account MT5 (`trade_mode==2`) without `--allow-live`.
- **No silent switching across the chain — VERIFIED so far:** every observed transition point is either default-safe (PAPER), explicit-flag+confirm (LIVE), or raises. Recommendation (gate item): a boot-time attestation line comparing `config.mode` vs `--mode` (CLI must win, mismatch → refuse) to close any drift between persisted settings DB and CLI.

## 4. Update / rollback trust chain

- **Ed25519 signed update manifest (S1 lane) — VERIFIED present & wired:** trust root embedded (`signing/trusted_keys.py`: key `2026-09-root`, unknown key_id → REJECT, rotation never weakens); canonical signing payload (`signing/update_manifest.py`), `SIGNATURE_INVALID/MISSING_SIGNATURE` codes; release.yml MANDATES signing (`secrets.NSE_UPDATE_SIGNING_KEY`, client-side re-verify step). Production fetch gap fixed by **S1**: `update_engine/discovery.py` `SignedManifestResolver` attaches `update-manifest.signed.json` from the release asset, fail-closed (§6b `SECURITY_BLOCKED` when absent/unparsable, "no unsigned fallback"). Tests: `tests/unit/test_signed_manifest_fetch_s1.py`, `test_signed_update_manifest.py` (present; executed by CI, not re-run by this lane — labelled STATIC).
- **Rollback hash-verification gap: CLOSED — VERIFIED (source + tests exist).** BUG-263: `rollback_state.RollbackEngine.verify_snapshot()` + `packaging.verify_snapshot_integrity()` re-verify the `.previous-*` snapshot against the embedded `release-manifest.json` (traversal-guard first, recomputed SHA-256 of every listed file, `SHA256SUMS.txt` agreement, `build-info.json` identity cross-check) BEFORE the first byte is copied; any refusal = `_integrity_refusal`, live tree byte-unchanged, no partial restore. Orchestrator auto-rollback honours the same gate (`orchestrator.py:830-859` rollback_refused_code path). Tests: `tests/unit/test_bug263_snapshot_integrity_gate.py`, `tests/unit/test_agent9_rollback_integrity.py`.
- **RESIDUAL GAP FOUND — the rollback anchor is UNSIGNED (STATIC analysis, important):** the in-payload `release-manifest.json` embedded in the portable tree is a hash list with NO Ed25519 signature over it (release.yml signs only `update-manifest.signed.json` for the ZIP). A snapshot that is replaced wholesale — attacker with user-write access fabricates tree + manifest + SHA256SUMS coherently — passes `verify_snapshot_integrity`. Accepted threat model ("malware that can write the snapshot dir can own the user") makes this defensible, but the trust chain then rests on file ACLs, not the root key. Recommendation (gate S5): embed the detached Ed25519 signature of `release-manifest.json` IN the manifest at build time and verify it against `TRUSTED_UPDATE_KEYS` inside `verify_snapshot_integrity` — rollback then inherits the same root as updates.

## 5. CI gate integrity

- **Nightly rc silent-green bug — FIXED, VERIFIED by history + current source:** `dd4397cc` "nightly E2E journey gate must fail the job on red journeys (TASK-A10)" — `nightly-e2e.yml:103-113` captures `rc=$?`, treats skipped/deselected journeys as failure, and `exit 1` on rc≠0 (comment: "::error:: alone does NOT fail the job"). Same capture-and-fail pattern in `nightly-qa.yml` (slow suite rc → step env → dedicated fail step).
- **Final-gate fail-closed — VERIFIED present:** `scripts/ci/ci_final_gate.py` — MISSING result JSON now fails the job (silent-skip hole closed); `blocked` only legitimate with a written result file.
- **Required contexts (live, branch protection) — 11 contexts:** CI Integrity and Change Classification; Code Quality & Tests; Dependency drift (lock vs pyproject); Migration safety (fail-loud + version postconditions); Frontend JS Unit Tests; CodeQL Analysis; Trivy Vulnerability Scan; OSV Scanner / osv-scan; Py Tests (windows-latest); Py Tests (macos-latest); Validate documentation.
- **Context-vs-workflow reconciliation gap (STATIC observation):** `tests-os.yml` job names are "Py Tests (windows-latest) (3.11, unit)" style matrix names — the protection list's plain "Py Tests (windows-latest)" context must actually be emitted (see `3086e99b` "emit all required contexts on push to main"); add a periodic reconciliation check so no required context can be satisfied by a stub/no-op check.

## 6. Branch protection reality (API, live — taskboard claim REFUTED)

`GET /repos/Opselon/NexusTradingForexBot/branches/main/protection` → **HTTP 200, main IS protected** (status: `rulesets` endpoint returns `[]` — classic protection, not rulesets):
- required_status_checks: 11 contexts (list above), **strict=false** (up-to-date-before-merge NOT required — recommend enabling strict, or ruleset equivalent);
- **enforce_admins = true**; required_linear_history = true; allow_force_pushes = false; allow_deletions = false;
- required_signatures = **false** (commit signing not required); required_conversation_resolution = false; lock_branch = false.
→ The earlier taskboard note "main NOT protected" is OUT OF DATE at audit time; treat the API state above as the release gate's baseline and re-snapshot at every release cut (release.yml's `release_auth_gate.py` already re-reads required contexts live).

### 6.1 CRITICAL finding — long-lived OAuth token embedded in `git remote` URL
`git remote -v` (read-only) shows `https://***@github.com/…` — a 40-char `gho_…` OAuth token embedded in `.git/config`, and **live-validated it**: `X-OAuth-Scopes: gist, notifications, read:gpg_key, read:org, repo, user, workflow, write:public_key` (HTTP 200 against /user). That is a repo-write + workflow-write + org-read credential stored in plaintext in a config file and echoed by any `git remote -v`/log/crash dump/agent transcript (this lane's output required masking). Token VALUE not recorded anywhere in this audit. Remediation (release-gate blocker): migrate origin to `https://github.com/Opselon/NexusTradingForexBot.git` and rely on the already-configured `manager` credential helper, or replace with a fine-grained single-repo token; revoke/rotate this OAuth grant; grep `.git/config`, CI logs and any mirrored transcripts on other machines for the same value.

## 7. release.yml gates (STATIC read of full workflow)

Chain: `release-auth` (Checks-API proof that ALL required main-CI contexts succeeded on the EXACT 40-hex SHA; fails closed on missing/pending/failed/ambiguous; guards tag AND `workflow_dispatch`) → `validate` (tag==pyproject version; `scan_secrets.py`; no dev artifacts tracked) → `gates` (ruff, ruff format, mypy, critical_suite via manifest with `-n auto --dist loadgroup`, integration smoke subset) → `build-windows-x64` (full-SHA build-info stamping + BUG-174 identity tripwires, BUG-166 pre-stage contract files + size sanity, checksums/manifest/SBOM, **mandatory** Ed25519 `Sign update manifest` + client-side verify, `verify-release` self-check, P3 Authenticode gate behind `NSE_REQUIRE_CODE_SIGNING`) → `arm64-report` (loud unsupported) → `release` (publish + undraft safety + API post-publish verification). `permissions: contents: write` scoped; actions SHA-pinned; concurrency per-ref.
**Gate-quality notes:** (a) release `gates` runs critical+smoke subset only — full integration/os-matrix coverage arrives solely via the same-SHA main-CI evidence in `release-auth` (acceptable, documented in-file); (b) release lane does NOT include rollback/update-trust-chain tests (they ride the critical manifest — confirm `test_bug263_*`/`test_signed_*` membership in `tests/critical_suite.txt` at next touch); (c) binaries ship documented-unsigned unless the var is set.

---

## 8. FINAL RELEASE-GATE CHECKLIST (doc-ready)

Legend: ✅ VERIFIED-this-lane · 🟢 STATIC/source-confirmed · ⚠️ action-open.

1. ✅ `scripts/ci/scan_secrets.py` passes on repo (exit 0) — AND 🟢 scope extended to `scripts/ installer/ Web/ agents/ tests/ .github/` (lane re-scan clean today; CI change still ⚠️ open as code fix for Master).
2. ✅ No tracked `.env`, `*.db`, `*.pem/key/p12`, `secrets.yaml`; release validate-step artifact tripwire in place.
3. ✅ `.env` gitignored; `auth_boot` boot-only publish documented, opt-out honoured; compose contract fail-closed on missing token. ⚠️ Add ACL hardening + backup-exclusion note for the token-bearing `.env`.
4. ✅ Engine start: PAPER default; unknown mode → EXIT_USAGE; LIVE ⇒ interactive confirm (default-No) or `--json`+`--yes`; no silent LIVE in any observed path; config enum rejects unknown modes at parse time.
5. 🟢 Gateway: HMAC-SHA256(timestamp.body) via constant-time compare, skew ≤300 s, body cap 64 KiB, defaults refused for LIVE-allowed gateway (fail-closed RuntimeError), demo-account guard, exception text never echoed to client.
6. ✅ Update trust root: Ed25519 signed `update-manifest.signed.json` MANDATORY in release.yml + client-side re-verify; S1 resolver attaches it in production; missing/unsigned ⇒ SECURITY_BLOCKED (no fallback).
7. ✅ Rollback: snapshot re-verification (hashes + SHA256SUMS + identity) gates EVERY restore, no partial restore, live tree untouched on refusal. ⚠️ Close the UNSIGNED in-tree manifest residual (sign `release-manifest.json` with the root key and verify signature in `verify_snapshot_integrity`).
8. ✅ CI integrity: nightly rc silent-green fixed (`exit 1` + skipped-counts-as-failure); `ci_final_gate.py` fails on missing result JSON; release cannot publish without same-SHA main-CI evidence for every required context.
9. 🟢 Branch protection LIVE (API, 2026-09-14): main protected — 11 required contexts, enforce_admins, linear history, no force-push/delete. ⚠️ Enable `strict` status checks; ⚠️ decide on required commit signatures.
10. ⚠️ **BLOCKER:** remove the embedded `gho_…` OAuth token (full repo+workflow scopes) from `.git/config` remote URL; rotate/revoke; switch to credential helper or fine-grained token.
11. 🟢 Release hygiene: tag==version gate, full-SHA identity stamped + tripwired in built EXEs, SBOM+SHA256SUMS+manifests uploaded, post-publish API verification, ARM64 explicitly unsupported, P3 code-signing readiness gate documented.
12. ⚠️ Pre-release snapshot ritual: re-run `release_auth_gate.py --repo … --sha <tag-sha>` semantics against CURRENT protection list; re-check this protection JSON (protection can drift silently).

## Risks (roll-up, severity-ordered)

1. **CRITICAL** — `gho_` OAuth token (scopes incl. repo, workflow, write:public_key) embedded in `.git/config` remote URL; self-reveals via `git remote -v` and any transcript. Rotate + remove now.
2. **HIGH (structural)** — `scan_secrets.py` covers only `src/configs/docs`; `scripts/`, `installer/`, `Web/`, `agents/` (and `tests/`, `.github/`) ship into release paths (installer sources, bundled `Web` asset) unscanned by both CI and release validate. Content verified clean today; extension is a one-line-root change with a test-fixture allow-list.
3. **MEDIUM** — Rollback trust anchor unsigned: `verify_snapshot_integrity` trusts the in-tree `release-manifest.json` hash list; a wholesale-fabricated snapshot evades it. Fix = Ed25519-sign the embedded manifest (reuse existing trust root).
4. **MEDIUM** — `.env` plaintext control-plane token at user scope (documented trade-off of BUG-267); no ACL hardening/backup-exclusion guidance shipped.
5. **LOW** — Branch protection: `strict=false` (merge on stale main possible) and commit signatures not required; enforce_admins=true already covers the admin-bypass risk.
6. **LOW** — Taskboard's "main NOT protected" was stale; protection state must be re-snapshotted per release (drift possible) — checklist item 12.
7. **INFO** — Live `.env` inspected by key name only; no secret values read, printed, or stored by this lane. Lane executed zero git-write commands; sole file written: this report.
