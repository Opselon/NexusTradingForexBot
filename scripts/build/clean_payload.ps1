# =============================================================================
# Nexus Scalp Engine — Package Payload Hygiene (EU-01)
# =============================================================================
# Usage:  .\scripts\build\clean_payload.ps1 -BundleDir <path\to\onedir\NexusScalpEngine>
#         .\scripts\build\clean_payload.ps1 -BundleDir <dir> -VerifyOnly
#
# A packaged release payload may contain APPLICATION files only. Every build
# step that launches the built EXE for verification (start_smoke.ps1, the
# release workflow's fresh-install smoke, `health --json`) writes runtime
# state into the payload, because a frozen process anchors its runtime
# workspace to the EXE directory (release/paths.py::get_runtime_workspace ->
# exe_dir when frozen). The smoke run therefore creates artifacts/ (SQLite
# databases), data/ (paper state), logs/ and archive/ INSIDE the bundle, and
# staging copies that verbatim.
#
# Observed in the shipped v9.0.14 installer:
#   artifacts/audit.db          52 tables, 23 foreign audit_signals,
#                               1 populated runtime_risk_state row
#   artifacts/news.db           405 foreign news_articles
#   artifacts/candle_intel.db   empty
#   data/paper_state.json       build machine paper-session state
#   archive/_hygiene_state, archive/_quarantine, logs/{error,info,warning}
# A fresh user therefore started with a stranger's trading history instead of
# a NOT_INITIALIZED database, and `nexus db status` reported
# DB_MIGRATION_PENDING on files the installer had just delivered.
#
# Run AFTER any EXE launch and BEFORE staging. -VerifyOnly turns this into a
# release gate that fails the build (exit 4) instead of cleaning, so a future
# smoke step cannot silently re-ship machine state.
#
# Preserved: artifacts/models/** (the offline/CI starter model bundle is
# intentional payload content - `nexus model-provision --status` calls it the
# DEV STARTER) and configs/** + Web/** (PyInstaller-collected application
# data). Only machine runtime state is removed.
#
# Never destructive to user data: this only ever removes content inside the
# build-owned -BundleDir, never outside it.
# =============================================================================
param(
    [Parameter(Mandatory = $true)][string]$BundleDir,
    [switch]$VerifyOnly
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $BundleDir)) {
    Write-Host "[PAYLOAD-HYGIENE] bundle directory missing: $BundleDir" -ForegroundColor Red
    exit 3
}
$BundleDir = (Resolve-Path $BundleDir).Path

function Remove-State([string]$path, [string]$label) {
    if (-not (Test-Path $path)) { return $false }
    if ($VerifyOnly) {
        Write-Host "[PAYLOAD-HYGIENE] FAIL (verify-only): runtime state present: $path" -ForegroundColor Red
        exit 4
    }
    Remove-Item $path -Recurse -Force -ErrorAction SilentlyContinue
    if (Test-Path $path) {
        Write-Host "[PAYLOAD-HYGIENE] FAILED: could not remove $path" -ForegroundColor Red
        exit 4
    }
    Write-Host "[PAYLOAD-HYGIENE] removed $label" -ForegroundColor Yellow
    return $true
}

# 1. Databases: delete the whole artifacts/ tree EXCEPT the intentional
#    model bundle (kept - see header).
$artifacts = Join-Path $BundleDir "artifacts"
if (Test-Path $artifacts) {
    if ($VerifyOnly) {
        $dbHit = Get-ChildItem -Path $artifacts -Recurse -Include "*.db", "*.db-wal", "*.db-shm", "*.db-journal" -File -ErrorAction SilentlyContinue
        if ($dbHit) {
            Write-Host "[PAYLOAD-HYGIENE] FAIL (verify-only): database files in payload:" -ForegroundColor Red
            $dbHit | ForEach-Object { Write-Host "    $($_.FullName)" -ForegroundColor Red }
            exit 4
        }
    } else {
        Get-ChildItem -Path $artifacts -Force -ErrorAction SilentlyContinue | Where-Object { $_.Name -ne "models" } |
            ForEach-Object {
                Remove-Item $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
                Write-Host "[PAYLOAD-HYGIENE] removed artifacts/$($_.Name)" -ForegroundColor Yellow
            }
    }
}

# 2. Runtime state directories.
foreach ($rel in @("data", "logs", "archive", "update", "nexus.pid")) {
    [void](Remove-State (Join-Path $BundleDir $rel) $rel)
}

# 2b. Secrets + per-user markers must never ship. These are written under the
#     user data root by the running product (release/paths.py), but a smoke run
#     that anchors its workspace to the EXE dir can drop them beside the EXE;
#     shipping any of them would (a) leak a key/secret inside a public release
#     asset and (b) make a fresh install believe the first-run wizard already
#     completed / an update lock is already held.
$secretNames = @("auth-key.txt", "auth_key.pub", "app_secrets.enc", ".first_run",
                 "release.lock", "update.lock", "settings.json.bak")
foreach ($n in $secretNames) {
    [void](Remove-State (Join-Path $BundleDir $n) $n)
}

# 3. Database sidecars wherever they live (WAL/SHM/rollback journals).
$sidecarGlobs = @("*.db-wal", "*.db-shm", "*.db-journal")
foreach ($g in $sidecarGlobs) {
    Get-ChildItem -Path $BundleDir -Recurse -Filter $g -File -ErrorAction SilentlyContinue |
        ForEach-Object {
            if ($VerifyOnly) {
                Write-Host "[PAYLOAD-HYGIENE] FAIL (verify-only): db sidecar present: $($_.FullName)" -ForegroundColor Red
                exit 4
            }
            Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
            Write-Host "[PAYLOAD-HYGIENE] removed db sidecar: $($_.Name)" -ForegroundColor Yellow
        }
}

# 4. Hard invariant: no database, journal, log file or paper-state file may
#    remain anywhere in the payload. This is what protects the end user.
$forbidden = @("*.db", "*.db-wal", "*.db-shm", "*.db-journal", "*.log",
               "paper_state.json", "nexus.pid")
$leftovers = @()
foreach ($g in $forbidden) {
    $leftovers += Get-ChildItem -Path $BundleDir -Recurse -Filter $g -File -ErrorAction SilentlyContinue
}
$leftovers = @($leftovers | Where-Object { $_.Extension -ne ".pyc" })

if ($leftovers.Count -gt 0) {
    Write-Host "[PAYLOAD-HYGIENE] FAILED: runtime state still present in payload:" -ForegroundColor Red
    $leftovers | ForEach-Object { Write-Host "    $($_.FullName)" -ForegroundColor Red }
    exit 4
}

Write-Host "[PAYLOAD-HYGIENE] PASS: payload carries no build-machine runtime state" -ForegroundColor Green
exit 0
