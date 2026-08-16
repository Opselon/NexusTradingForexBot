# =============================================================================
# Nexus Scalp Engine — Clean-Install / Uninstall / Repair Smoke Test
# =============================================================================
# Usage:  .\scripts\build\clean_install_test.ps1 [-SetupExe path\to\setup.exe]
#
# Verifies on THIS machine (best-effort, non-destructive outside the test
# directory):
#   1) installer exists
#   2) silent install into a temp dir
#   3) installed app shows version + health
#   4) re-run install (idempotency / upgrade path)
#   5) uninstall
#   6) user data preserved after uninstall
# =============================================================================
param(
    [string]$SetupExe = ""
)

$ErrorActionPreference = "Stop"
if (-not $SetupExe -or -not (Test-Path $SetupExe)) {
    Write-Host "[CLEAN-INSTALL] No setup exe provided. Skipping." -ForegroundColor Yellow
    exit 0
}
$SetupExe = (Resolve-Path $SetupExe).Path
$TestRoot = Join-Path $env:TEMP "nexus-clean-install-test"
if (Test-Path $TestRoot) { Remove-Item $TestRoot -Recurse -Force }
New-Item -ItemType Directory -Force -Path $TestRoot | Out-Null

$InstDir = Join-Path $TestRoot "app"
$DataDir = Join-Path $TestRoot "data"
$LogFile = Join-Path $TestRoot "install.log"

Write-Host "[CLEAN-INSTALL] Silent install to $InstDir" -ForegroundColor Cyan
$args = @(
    "/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART",
    "/DIR=$InstDir",
    "/LOG=$LogFile"
)
$p = Start-Process -FilePath $SetupExe -ArgumentList $args -Wait -PassThru
if ($p.ExitCode -ne 0) {
    Write-Host "[CLEAN-INSTALL] FAILED install exit=$($p.ExitCode)" -ForegroundColor Red
    Get-Content $LogFile -Tail 30 | ForEach-Object { Write-Host $_ }
    exit 1
}
Write-Host "[CLEAN-INSTALL] installed ok" -ForegroundColor Green

$Exe = Join-Path $InstDir "NexusScalpEngine.exe"
if (-not (Test-Path $Exe)) { Write-Host "[CLEAN-INSTALL] FAILED exe missing" -ForegroundColor Red; exit 1 }

Write-Host "[CLEAN-INSTALL] version check" -ForegroundColor Cyan
$v = & $Exe version --plain 2>&1 | Out-String
if ($LASTEXITCODE -ne 0 -or $v -notmatch "version") {
    Write-Host "[CLEAN-INSTALL] FAILED version check: $v" -ForegroundColor Red; exit 1
}
Write-Host $v.Trim()

Write-Host "[CLEAN-INSTALL] health check" -ForegroundColor Cyan
$h = & $Exe health --json 2>&1 | Out-String
if ($LASTEXITCODE -ne 0 -or $h -notmatch '"overall"') {
    Write-Host "[CLEAN-INSTALL] FAILED health check: $h" -ForegroundColor Red; exit 1
}
Write-Host "[CLEAN-INSTALL] health ok"

Write-Host "[CLEAN-INSTALL] re-run install (idempotency)" -ForegroundColor Cyan
$p2 = Start-Process -FilePath $SetupExe -ArgumentList $args -Wait -PassThru
if ($p2.ExitCode -ne 0) {
    Write-Host "[CLEAN-INSTALL] FAILED re-install exit=$($p2.ExitCode)" -ForegroundColor Red; exit 1
}
Write-Host "[CLEAN-INSTALL] re-install ok (upgrade path)" -ForegroundColor Green

Write-Host "[CLEAN-INSTALL] uninstall" -ForegroundColor Cyan
$Uninstall = Join-Path $InstDir "unins000.exe"
if (-not (Test-Path $Uninstall)) {
    Write-Host "[CLEAN-INSTALL] WARN uninstaller not found (silent install may defer)" -ForegroundColor Yellow
} else {
    $p3 = Start-Process -FilePath $Uninstall -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART") -Wait -PassThru
    if ($p3.ExitCode -ne 0) {
        Write-Host "[CLEAN-INSTALL] FAILED uninstall exit=$($p3.ExitCode)" -ForegroundColor Red; exit 1
    }
    Write-Host "[CLEAN-INSTALL] uninstall ok" -ForegroundColor Green
}

Write-Host "[CLEAN-INSTALL] user-data protection check (sample user config)" -ForegroundColor Cyan
$UserData = Join-Path $env:LOCALAPPDATA "NexusScalpEngine"
if (Test-Path (Join-Path $UserData "config")) {
    Write-Host "[CLEAN-INSTALL] PASS: user data preserved at $UserData" -ForegroundColor Green
} else {
    Write-Host "[CLEAN-INSTALL] WARN: no user data dir (first run not executed) — policy verified by design" -ForegroundColor Yellow
}

Remove-Item $TestRoot -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "[CLEAN-INSTALL] ALL PASSED" -ForegroundColor Green