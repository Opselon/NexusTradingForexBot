# Download / Install Integrity Verification (INSTALL-VERIFY)

Truth table for the download → install → activation → first-run pipeline of
`installer/install.ps1`, the hardening applied for the INSTALL-VERIFY task,
and the remaining risks. Status values used throughout:

| State | Meaning |
|---|---|
| **AVAILABLE** | Every stage ran; artifacts verified; install is complete and usable |
| **DEGRADED** | Install usable but through a weaker path (ZIP fallback, unhashed third-party asset, partial progress awaiting resume) |
| **BLOCKED** | Download/verification/stage failed after retries; installer stopped with a precise reason; nothing corrupt was installed |
| **UNKNOWN** | No stage evidence in the current session (e.g. state file written standalone); never a fake AVAILABLE |

The machine-readable `install_state` field is written into
`%NexusHome%\state\install.json` on every stage flush and at final state.
Drivers and support tooling must read that field, not infer from exit codes alone.

## 1. Download stage truth table

| Condition | Verification performed | Result |
|---|---|---|
| Artifact downloads, SHA256 pin configured, digest matches | streaming SHA-256 on the `.partial` file **before** atomic move | **AVAILABLE** — artifact lands, telemetry `outcome=ok, verification=sha256` |
| Artifact downloads, digest MISMATCH (tampered/corrupt/truncated-then-padded) | SHA-256 compared to pin | **BLOCKED** — throw `integrity: SHA256 MISMATCH`, partial deleted, retried, never installed |
| Artifact downloads, digest string is not 64 hex digits | digest syntax check | **BLOCKED** before trusting the pin |
| Artifact truncated mid-transfer, `-MinBytes` floor configured | size floor on partial | **BLOCKED** — "truncated download suspected", retried |
| Artifact is 0 bytes | empty check (always) | **BLOCKED** — retried |
| HTTP error status / timeout / connection reset | 3 attempts, exponential backoff + jitter (1s, 2s, 4s, cap 15s) | **BLOCKED** after attempts exhausted — "Download failed after N attempts" |
| Artifact downloaded with NO pin configured | empty check only; digest recorded in telemetry | **DEGRADED** — provenance `verification=empty-check` with `actual_sha` recorded; see risks R1/R4 |
| Download interrupted (process killed) | `.partial-<pid>` staging + atomic `Move-Item` | destination never contains a partial file; stale partials are cleaned on the next attempt |

Every download attempt appends to `$Script:DownloadTelemetry` and the bounded
installer log: `{url_host, bytes, attempts, expected_sha, actual_sha,
verification, outcome}`. The array is persisted to `state/install.json` as
`download_telemetry`. It contains no secrets and is never network-sent.

## 2. Install (source acquisition) truth table

| Condition | Result |
|---|---|
| `git clone` (SSH → HTTPS) succeeds | **AVAILABLE** path: real clone, incremental history, `source_mode=git` |
| Existing valid checkout found | update-in-place: fetch → pin precedence (Commit > Tag > Branch) → ff-only → stash-restore. Re-run with unchanged origin is a verified no-op (same HEAD) |
| `-Commit` pin requested, HEAD differs from pin after checkout | **BLOCKED** — verify stage fails with `commit pin integrity: HEAD (...) does not match the requested commit (...)` |
| `-Commit` pin would roll HEAD backwards (ancestor) | Refused without `-ForceCommit` (warning, checkout untouched); applied with `-ForceCommit` |
| Clone fails → ZIP fallback acquired, validated (`pyproject.toml` marker, zip-slip guard), atomically moved | **DEGRADED** — `source_mode=zip` recorded in state, explicit user warning printed; git metadata initialized so future updates work |
| ZIP corrupt / not an archive / traversal entries | **BLOCKED** — extraction rejected, no residue |
| Existing install dir is a broken stub | moved aside to `engine.broken-<ts>` (never destroyed), fresh acquisition |
| All acquisition paths fail | **BLOCKED** — "Failed to acquire the Nexus repository (tried git SSH, git HTTPS, and ZIP archive)" |

Source-pinning hierarchy: **Commit** (exact, verified against HEAD post-install)
> **Tag** (exact ref fetch + detached checkout) > **Branch** (`main` default,
fast-forward only). The `main` floating default is the documented convenience
path; every reproducible/verified install should pass `-Commit <sha>`.

## 3. Runtime asset downloads (uv, PortableGit, Node)

| Asset | Pinning | Verification | State if unverifiable |
|---|---|---|---|
| PortableGit (`git-for-windows` release asset) | exact release tag `v2.56.0.windows.1` | SHA256 pin when `$Script:GitPortableAssetSha256` is configured (digest pin hook wired into `Invoke-NexusDownload`); otherwise version-pinned + post-extract functional probe (`git.exe` exists, `--version` runs, Git Bash MSYS child probe) | **DEGRADED** |
| uv installer (astral.sh / GitHub mirror) | latest (dynamic script) | TLS + child-process verification that `uv.exe` runs (`--version`) before use; installer output captured on failure | **DEGRADED** (see risk R1) |
| Node portable zip (optional) | `latest-v22.x` resolved from nodejs.org index | post-extract `node --version` probe | **DEGRADED** (optional component) |

## 4. Activation truth table

| Condition | Result |
|---|---|
| venv created, interpreter + site-packages health probe passes | venv stage **AVAILABLE**; old venv (if any) parked transactionally |
| Dependency install + audit passes (every declared dep verified against pyproject specs; `packages.json` written) | dependencies stage **AVAILABLE**; parked venv transaction committed |
| Dependency audit fails | previous venv restored, stage **BLOCKED**, failure enumerated per package |
| `nexus.exe`/`nse.exe` entry point missing | dependencies stage warns (repair hint); path stage throws if no launcher exists — **BLOCKED** |
| PATH/NEXUS_HOME persisted | idempotent; shim probe runs `nexus version` before PATH mutation |
| Another installer holds `state\installer.lock` | deliberate skip (well-formed frame, exit 0) — never a silent corruption window |

## 5. First-run truth table

| Condition | Result |
|---|---|
| `verify` stage: venv healthy + `nexus version` exits 0 + version-consistency (CLI version == pyproject version) + repo valid | **AVAILABLE** — "Verification passed" |
| CLI version != pyproject version (stamped build identity / stale metadata) | **BLOCKED** — version mismatch enumerated as a verification problem |
| Config template drift (newer template in checkout) | informational only; user config is never overwritten |
| No `live.yaml` yet | warning: engine will boot with defaults until configured (PAPER by default) |
| Stage failure mid-install | per-stage ledger records `ok=false` truthfully; `install_state=BLOCKED` in `state/install.json`; re-run resumes from the durable stage ledger |
| All stages complete | `state/install.json` records `install_state=AVAILABLE`, `repo_head`, `commit_pin`, `source_mode`, `git_tracking`, `download_telemetry` |

## 6. Hardening implemented (this task)

1. **SHA256 verification gate** — `Invoke-NexusDownload` gained
   `-ExpectedSha256` / `-MinBytes` parameters. Verification happens on the
   `.partial` file BEFORE the atomic move; mismatch/undersize/missing = hard
   failure with retry, never a silent install. (`Get-FileSha256`,
   `Test-DownloadIntegrity` helpers.)
2. **PortableGit digest pin hook** — setting
   `$Script:GitPortableAssetSha256` fail-closes the git stage on mismatch.
3. **Download telemetry** — per-download record (host, bytes, attempts,
   expected/actual SHA256, verification mode, outcome) persisted in
   `state/install.json:download_telemetry` and the installer log.
4. **Truthful availability states** — `install_state`
   (AVAILABLE/DEGRADED/BLOCKED/UNKNOWN) + `commit_pin` + `repo_head` in
   `state/install.json`; the full-install `-Json` summary frame now reports
   `install_state` and `repo_head` instead of a blanket `ok=true`.
5. **Commit-pin integrity gate in verify** — the verify stage fails when HEAD
   does not satisfy a requested `-Commit` pin.
6. **Test seam made explicit** — `NEXUS_TEST_REPO_HTTPS` / `NEXUS_TEST_REPO_SSH`
   env overrides for offline lifecycle tests (documented, never set in prod).
7. **Tests** — `tests/installer/test_download_integrity.py` (10 tests): digest
   match/mismatch/malformed, truncation floor, empty payload, HTTP 404 retry,
   partial-residue check, telemetry shape, state-ledger fields, and a
   Python-vs-PowerShell manual checksum probe + download→verify→extract
   round-trip. All offline (localhost origin).

## 7. Remaining risks (honest gaps)

- **R1 — uv installer script is irm|iex'd unpinned.** The astral.sh/GitHub
  installer scripts are dynamic content; TLS protects transport but not
  content pinning. Full fix: pin uv to an exact release and verify the
  downloaded `uv.exe` against the official SHA256 (the download gate already
  supports it), dropping the dynamic installer scripts.
- **R2 — default `main` branch float.** Unpinned installs track `main`.
  The commit-pin gate makes pinned installs verifiable, but the canonical
  one-liner remains floating by design.
- **R3 — repository source is trust-on-first-clone.** Git content is
  authenticated by the remote's TLS + commit hashes, but the installer does
  not pin an expected repository HEAD digest for unpinned branch installs.
- **R4 — unhashed third-party assets degrade to version-pins + functional
  probes** (Node index, uv) rather than digest pins.
- **R5 — telemetry is local-only** (state file + log). There is no network
  telemetry upload by design (privacy); central install analytics would be a
  separate, opt-in feature.
