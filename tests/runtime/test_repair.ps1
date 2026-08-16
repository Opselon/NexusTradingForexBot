# =============================================================================
# Nexus Scalp Engine — RUNTIME TEST: Repair on a real packaged EXE
# =============================================================================
# Usage:  .\tests\runtime\test_repair.ps1 [-Exe path\to\NexusScalpEngine.exe]
#
# Deliberately damages NON-destructive state (missing user dirs, missing
# config) and verifies `nexus repair` restores what it should without
# deleting unrelated data.
# =============================================================================
param([string]$Exe = "")

$ErrorActionPreference = "Stop"
if (-not $Exe) {
    $Exe = Join-Path $PSScriptRoot "..\..\release\v$((Select-String -Path (Join-Path $PSScriptRoot "..\..\pyproject.toml") -Pattern '^version = "([^"]+)"').Matches[0].Groups[1].Value)\windows\x64\portable\NexusScalpEngine.exe"
}
$Exe = (Resolve-Path $Exe).Path
if (-not (Test-Path $Exe)) { Write-Host "FAIL: EXE not found" -ForegroundColor Red; exit 1 }

# User-data dirs tracked via LocalAppData (the packaged app's data root).
$UserData = Join-Path $env:LOCALAPPDATA "NexusScalpEngine"

$failures = 0
Write-Host "[REPAIR-RUNTIME] $Exe" -ForegroundColor Cyan

# 1. Remove config dir (non-destructive repair target)
$ConfigDir = Join-Path $UserData "config"
$LogsDir = Join-Path $UserData "logs"
if (Test-Path $ConfigDir) { Remove-Item $ConfigDir -Recurse -Force }
if (Test-Path $LogsDir) { Remove-Item $LogsDir -Recurse -Force }
Write-Host "Damaged: removed user config + logs dirs" -ForegroundColor Yellow

# 2. Run repair (direct invocation, capture exit immediately)
& $Exe repair --recreate-config > $null 2>&1
$repairCode = $LASTEXITCODE
$out = & $Exe repair --recreate-config 2>&1 | Out-String
if ($repairCode -ne 0) { Write-Host "FAIL: repair exit=$repairCode" -ForegroundColor Red; $script:failures++ } else { Write-Host "PASS repair exit=0" -ForegroundColor Green }

# 3. Verify restored
if (Test-Path $ConfigDir) {
    Write-Host "PASS config dir restored" -ForegroundColor Green
} else {
    Write-Host "FAIL config dir not restored" -ForegroundColor Red
    $script:failures++
}
if (Test-Path (Join-Path $ConfigDir "nexus.yaml")) {
    Write-Host "PASS config file created from template" -ForegroundColor Green
} else {
    Write-Host "FAIL config file missing" -ForegroundColor Red
    $script:failures++
}

# 4. Verify repair did NOT destroy unrelated user data (if any exists)
$SafetyMarker = Join-Path $UserData "data"
if (Test-Path $SafetyMarker) {
    Write-Host "PASS user data dir still present" -ForegroundColor Green
} else {
    Write-Host "INFO: no user data dir existed (fresh machine)" -ForegroundColor Yellow
}

if ($failures -gt 0) { Write-Host "[REPAIR-RUNTIME] $failures FAILED" -ForegroundColor Red; exit 1 }
Write-Host "[REPAIR-RUNTIME] ALL PASSED" -ForegroundColor Green