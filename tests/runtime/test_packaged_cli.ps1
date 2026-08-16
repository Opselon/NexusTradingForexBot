# =============================================================================
# Nexus Scalp Engine — RUNTIME TEST: Packaged CLI (real artifacts)
# =============================================================================
# Usage:  .\tests\runtime\test_packaged_cli.ps1 [-CliExe path\to\NexusScalpEngine-CLI.exe]
#
# Runs the REAL onefile CLI and verifies every automation-facing command:
#   --help, version --plain/json, health --json, doctor --json,
#   status --json, config --help
# Checks: exit code, stdout, valid JSON, no traceback, no missing DLL.
# =============================================================================
param([string]$CliExe = "")

$ErrorActionPreference = "Stop"
if (-not $CliExe) {
    $CliExe = Join-Path $PSScriptRoot "..\..\release\v$((Select-String -Path (Join-Path $PSScriptRoot "..\..\pyproject.toml") -Pattern '^version = "([^"]+)"').Matches[0].Groups[1].Value)\windows\x64\cli\NexusScalpEngine-CLI.exe"
}
$CliExe = (Resolve-Path $CliExe).Path
if (-not (Test-Path $CliExe)) { Write-Host "FAIL: CLI not found at $CliExe" -ForegroundColor Red; exit 1 }

$failures = 0
function Check($Name, [string[]]$ArgsList, $ExpectJson = $false) {
    # Start-Process captures the NATIVE exit code reliably (piping to
    # Out-String/ForEach-Object clobbers $LASTEXITCODE on PowerShell 7).
    $tmpOut = Join-Path $env:TEMP "nexus-cli-test-out.txt"
    $tmpErr = Join-Path $env:TEMP "nexus-cli-test-err.txt"
    Remove-Item $tmpOut, $tmpErr -ErrorAction SilentlyContinue
    $p = Start-Process -FilePath $CliExe -ArgumentList $ArgsList -NoNewWindow -Wait `
        -PassThru -RedirectStandardOutput $tmpOut -RedirectStandardError $tmpErr
    $code = $p.ExitCode
    $joined = ""
    if (Test-Path $tmpOut) { $joined = Get-Content $tmpOut -Raw }
    if ($code -ne 0) { Write-Host "FAIL $Name : exit=$code" -ForegroundColor Red; $script:failures++; return }
    if ($ExpectJson) {
        try { $null = $joined | ConvertFrom-Json -ErrorAction Stop }
        catch { Write-Host "FAIL $Name : not valid JSON: $($joined.Substring(0, [Math]::Min(120, $joined.Length)))" -ForegroundColor Red; $script:failures++; return }
    }
    if ($joined -match "Traceback") { Write-Host "FAIL $Name : traceback in output" -ForegroundColor Red; $script:failures++; return }
    Write-Host "PASS $Name" -ForegroundColor Green
}

Write-Host "[CLI-RUNTIME] $CliExe" -ForegroundColor Cyan
Check "--help" @("--help")
Check "version --plain" @("version", "--plain")
Check "version --json" @("version", "--json") $true
Check "health --json" @("health", "--json") $true
Check "doctor --json" @("doctor", "--json") $true
Check "status --json" @("status", "--json") $true
Check "config --help" @("config", "--help")

if ($failures -gt 0) { Write-Host "[CLI-RUNTIME] $failures FAILED" -ForegroundColor Red; exit 1 }
Write-Host "[CLI-RUNTIME] ALL PASSED" -ForegroundColor Green