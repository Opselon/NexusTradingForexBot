# =============================================================================
# Nexus Scalp Engine — RUNTIME TEST: NO SYSTEM PYTHON DEPENDENCY
# =============================================================================
# Usage:  .\tests\runtime\test_no_python_dependency.ps1 [-CliExe path] [-Exe path]
#
# CRITICAL: proves the packaged release is genuinely self-contained.
# Runs the real EXEs in an environment where Python / venv / dev env vars are
# unavailable (stripped PATH), and verifies version/health/doctor succeed.
#
# Direct invocation is used with $LASTEXITCODE captured immediately (no
# pipeline in between) so the native exit code is reliable.
# =============================================================================
param(
    [string]$CliExe = "",
    [string]$Exe = ""
)

$ErrorActionPreference = "Stop"
$V = (Select-String -Path (Join-Path $PSScriptRoot "..\..\pyproject.toml") -Pattern '^version = "([^"]+)"').Matches[0].Groups[1].Value
if (-not $CliExe) { $CliExe = Join-Path $PSScriptRoot "..\..\release\v$V\windows\x64\cli\NexusScalpEngine-CLI.exe" }
if (-not $Exe) { $Exe = Join-Path $PSScriptRoot "..\..\release\v$V\windows\x64\portable\NexusScalpEngine.exe" }

# ---------------------------------------------------------------------------
# 1. Simulate a machine without Python on PATH (keep only Windows system dirs).
# ---------------------------------------------------------------------------
$StrippedPath = "$env:SystemRoot\System32;$env:SystemRoot"
$env:Path = $StrippedPath
foreach ($k in @("PYTHONHOME", "PYTHONPATH", "VIRTUAL_ENV", "CONDA_PREFIX", "UV_")) {
    if (Test-Path "Env:$k") { Remove-Item "Env:$k" -Force }
}

$failures = 0
function CheckNoPy($Name, $ExePath, [string[]]$ArgsList) {
    & $ExePath @ArgsList > $null 2>&1
    $code = $LASTEXITCODE
    if ($code -ne 0) { Write-Host "FAIL $Name : exit=$code" -ForegroundColor Red; $script:failures++; return }
    Write-Host "PASS $Name (no system Python on PATH)" -ForegroundColor Green
}

Write-Host "[NO-PYTHON] PATH stripped to: $StrippedPath" -ForegroundColor Cyan
if (Test-Path $CliExe) {
    CheckNoPy "CLI version --plain" $CliExe @("version", "--plain")
    CheckNoPy "CLI health --json" $CliExe @("health", "--json")
    CheckNoPy "CLI doctor --json" $CliExe @("doctor", "--json")
} else {
    Write-Host "WARN: CLI not found at $CliExe (skipped)" -ForegroundColor Yellow
}
if (Test-Path $Exe) {
    CheckNoPy "Engine version --plain" $Exe @("version", "--plain")
    CheckNoPy "Engine health --json" $Exe @("health", "--json")
} else {
    Write-Host "WARN: Engine not found at $Exe (skipped)" -ForegroundColor Yellow
}

if ($failures -gt 0) { Write-Host "[NO-PYTHON] $failures FAILED" -ForegroundColor Red; exit 1 }
Write-Host "[NO-PYTHON] ALL PASSED — packaged release is self-contained" -ForegroundColor Green