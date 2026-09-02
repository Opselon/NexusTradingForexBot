# =============================================================================
# Nexus Scalp Engine — RUNTIME TEST SUITE RUNNER
# =============================================================================
# Usage:  .\tests\runtime\run_runtime_tests.ps1 [-ReleaseRoot release\v9.0.0\windows\x64]
#
# Runs every runtime test that exercises REAL packaged artifacts. Each test
# is invoked DIRECTLY with explicit named parameters (PowerShell 7 splatting
# of [string[]] into a child script mangles named args, so we avoid splats).
# =============================================================================
param([string]$ReleaseRoot = "")

$ErrorActionPreference = "Stop"
$Here = $PSScriptRoot
$V = (Select-String -Path (Join-Path $Here "..\..\pyproject.toml") -Pattern '^version = "([^"]+)"').Matches[0].Groups[1].Value
if (-not $ReleaseRoot) { $ReleaseRoot = Join-Path $Here "..\..\release\v$V\windows\x64" }
$ReleaseRoot = (Resolve-Path $ReleaseRoot).Path

$Cli = Join-Path $ReleaseRoot "cli\NexusScalpEngine-CLI.exe"
$Exe = Join-Path $ReleaseRoot "portable\NexusScalpEngine.exe"
$Setup = Join-Path $ReleaseRoot "NexusScalpEngine-$V-win-x64-setup.exe"
$failed = @()

foreach ($name in @("CLI", "ENGINE", "HEALTH", "NO_PYTHON", "INSTALLER", "REPAIR")) {
    switch ($name) {
        "CLI" {
            if (-not (Test-Path $Cli)) { Write-Host "SKIP CLI (missing)" -ForegroundColor Yellow; continue }
            Write-Host "`n===== RUNTIME: CLI =====" -ForegroundColor Cyan
            & (Join-Path $Here "test_packaged_cli.ps1") -CliExe $Cli
        }
        "ENGINE" {
            if (-not (Test-Path $Exe)) { Write-Host "SKIP ENGINE (missing)" -ForegroundColor Yellow; continue }
            Write-Host "`n===== RUNTIME: ENGINE =====" -ForegroundColor Cyan
            & (Join-Path $Here "test_packaged_engine.ps1") -Exe $Exe
        }
        "HEALTH" {
            if (-not (Test-Path $Exe)) { Write-Host "SKIP HEALTH (missing)" -ForegroundColor Yellow; continue }
            Write-Host "`n===== RUNTIME: HEALTH =====" -ForegroundColor Cyan
            & (Join-Path $Here "test_health_runtime.ps1") -Exe $Exe
        }
        "NO_PYTHON" {
            if (-not (Test-Path $Cli) -or -not (Test-Path $Exe)) { Write-Host "SKIP NO_PYTHON (missing artifacts)" -ForegroundColor Yellow; continue }
            Write-Host "`n===== RUNTIME: NO_PYTHON =====" -ForegroundColor Cyan
            & (Join-Path $Here "test_no_python_dependency.ps1") -Exe $Exe -CliExe $Cli
        }
        "INSTALLER" {
            if (-not (Test-Path $Setup)) { Write-Host "SKIP INSTALLER (missing $Setup)" -ForegroundColor Yellow; continue }
            Write-Host "`n===== RUNTIME: INSTALLER =====" -ForegroundColor Cyan
            & (Join-Path $Here "test_installer.ps1") -SetupExe $Setup
        }
        "REPAIR" {
            if (-not (Test-Path $Exe)) { Write-Host "SKIP REPAIR (missing)" -ForegroundColor Yellow; continue }
            Write-Host "`n===== RUNTIME: REPAIR =====" -ForegroundColor Cyan
            & (Join-Path $Here "test_repair.ps1") -Exe $Exe
        }
    }
    if ($LASTEXITCODE -ne 0) { $failed += $name }
}

if ($failed.Count -gt 0) {
    Write-Host "`n[RUNTIME] FAILED: $($failed -join ', ')" -ForegroundColor Red
    exit 1
}
Write-Host "`n[RUNTIME] ALL RUNTIME SUITES PASSED" -ForegroundColor Green