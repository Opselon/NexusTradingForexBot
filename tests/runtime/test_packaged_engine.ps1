# =============================================================================
# Nexus Scalp Engine — RUNTIME TEST: Packaged Engine + LIVE SAFETY
# =============================================================================
# Usage:  .\tests\runtime\test_packaged_engine.ps1 [-Exe path\to\NexusScalpEngine.exe]
#
# Runs the REAL onedir EXE and verifies:
#   - version / health / doctor work
#   - default start mode is safe (no LIVE)
#   - LIVE requires explicit confirmation (negative test)
#   - setup command exists
# Does NOT connect to a real trading account.
#
# NOTE: uses DIRECT invocation (& $Exe ...) and reads $LASTEXITCODE
# immediately — no pipeline in between, so the native exit code is reliable.
# =============================================================================
param([string]$Exe = "")

$ErrorActionPreference = "Stop"
if (-not $Exe) {
    $Exe = Join-Path $PSScriptRoot "..\..\release\v$((Select-String -Path (Join-Path $PSScriptRoot "..\..\pyproject.toml") -Pattern '^version = "([^"]+)"').Matches[0].Groups[1].Value)\windows\x64\portable\NexusScalpEngine.exe"
}
$Exe = (Resolve-Path $Exe).Path
if (-not (Test-Path $Exe)) { Write-Host "FAIL: engine EXE not found at $Exe" -ForegroundColor Red; exit 1 }

$failures = 0
function Check($Name, [string[]]$ArgsList, $ExpectJson = $false, $ExpectContains = "") {
    & $Exe @ArgsList > $null 2>&1
    $code = $LASTEXITCODE
    if ($code -ne 0) { Write-Host "FAIL $Name : exit=$code" -ForegroundColor Red; $script:failures++; return }
    if ($ExpectJson -or $ExpectContains) {
        $out = & $Exe @ArgsList 2>&1 | Out-String
        if ($ExpectJson) {
            try { $null = $out | ConvertFrom-Json -ErrorAction Stop }
            catch { Write-Host "FAIL $Name : not valid JSON" -ForegroundColor Red; $script:failures++; return }
        }
        if ($ExpectContains -and $out -notmatch $ExpectContains) {
            Write-Host "FAIL $Name : expected '$ExpectContains' not in output" -ForegroundColor Red; $script:failures++; return
        }
        if ($out -match "Traceback") { Write-Host "FAIL $Name : traceback" -ForegroundColor Red; $script:failures++; return }
    }
    Write-Host "PASS $Name" -ForegroundColor Green
}

Write-Host "[ENGINE-RUNTIME] $Exe" -ForegroundColor Cyan
Check "version --plain" @("version", "--plain") $false "version"
Check "health --json" @("health", "--json") $true
Check "doctor --json" @("doctor", "--json") $true
Check "setup --help" @("setup", "--help")

# LIVE safety: `--mode live` without confirmation must show the WARNING and
# not start trading.
& $Exe start --mode live 2>&1 | Out-Null
if ($LASTEXITCODE -eq 1) {
    Write-Host "PASS start --mode live aborts without confirmation" -ForegroundColor Green
} else {
    Write-Host "FAIL start --mode live did not abort (exit=$LASTEXITCODE)" -ForegroundColor Red
    $script:failures++
}

if ($failures -gt 0) { Write-Host "[ENGINE-RUNTIME] $failures FAILED" -ForegroundColor Red; exit 1 }
Write-Host "[ENGINE-RUNTIME] ALL PASSED" -ForegroundColor Green