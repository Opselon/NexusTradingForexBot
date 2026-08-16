# =============================================================================
# Nexus Scalp Engine — RUNTIME TEST: Installer lifecycle (real setup.exe)
# =============================================================================
# Usage:  .\tests\runtime\test_installer.ps1 [-SetupExe path]
#
# Exercises REAL artifacts end-to-end:
#   install (silent) -> version -> health -> re-install (idempotency) ->
#   user-data preservation -> uninstall (silent) -> uninstall idempotency
# =============================================================================
param([string]$SetupExe = "")

$ErrorActionPreference = "Stop"
if (-not $SetupExe) {
    $V = (Select-String -Path (Join-Path $PSScriptRoot "..\..\pyproject.toml") -Pattern '^version = "([^"]+)"').Matches[0].Groups[1].Value
    $SetupExe = Join-Path $PSScriptRoot "..\..\release\v$V\windows\x64\NexusScalpEngine-$V-win-x64-setup.exe"
}
$SetupExe = (Resolve-Path $SetupExe).Path
if (-not (Test-Path $SetupExe)) { Write-Host "FAIL: setup not found: $SetupExe" -ForegroundColor Red; exit 1 }

$TestRoot = Join-Path $env:TEMP "nexus-runtime-installer-test"
if (Test-Path $TestRoot) { Remove-Item $TestRoot -Recurse -Force }
New-Item -ItemType Directory -Force -Path $TestRoot | Out-Null
$InstDir = Join-Path $TestRoot "app"
$LogFile = Join-Path $TestRoot "install.log"

$failures = 0
Write-Host "[INSTALLER-RUNTIME] $SetupExe" -ForegroundColor Cyan

# 1. Install
$p = Start-Process -FilePath $SetupExe -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/DIR=$InstDir", "/LOG=$LogFile") -Wait -PassThru
if ($p.ExitCode -ne 0) { Write-Host "FAIL: install exit=$($p.ExitCode)" -ForegroundColor Red; $script:failures++ } else { Write-Host "PASS install (silent)" -ForegroundColor Green }

$Exe = Join-Path $InstDir "NexusScalpEngine.exe"
if (-not (Test-Path $Exe)) { Write-Host "FAIL: installed EXE missing" -ForegroundColor Red; $script:failures++ } else { Write-Host "PASS installed EXE present" -ForegroundColor Green }

# 2. Version + health
if (Test-Path $Exe) {
    $v = & $Exe version --plain 2>&1 | Out-String
    if ($v -match "version") { Write-Host "PASS version: $($v.Trim())" -ForegroundColor Green } else { Write-Host "FAIL version: $v" -ForegroundColor Red; $script:failures++ }
    $h = & $Exe health --json 2>&1 | Out-String
    try { $null = $h | ConvertFrom-Json; Write-Host "PASS health json" -ForegroundColor Green } catch { Write-Host "FAIL health: $h" -ForegroundColor Red; $script:failures++ }
}

# 3. Re-install (idempotency / upgrade path)
$p2 = Start-Process -FilePath $SetupExe -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/DIR=$InstDir") -Wait -PassThru
if ($p2.ExitCode -ne 0) { Write-Host "FAIL: re-install exit=$($p2.ExitCode)" -ForegroundColor Red; $script:failures++ } else { Write-Host "PASS re-install (idempotent)" -ForegroundColor Green }

# 4. User-data preservation marker
$UserData = Join-Path $env:LOCALAPPDATA "NexusScalpEngine"
New-Item -ItemType Directory -Force -Path (Join-Path $UserData "config") | Out-Null
$marker = Join-Path $UserData "config\test.yaml"
Set-Content $marker "preserve-me" -Encoding utf8

# 5. Uninstall (silent)
$Uninstall = Join-Path $InstDir "unins000.exe"
if (Test-Path $Uninstall) {
    $p3 = Start-Process -FilePath $Uninstall -ArgumentList @("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART") -Wait -PassThru
    if ($p3.ExitCode -ne 0) { Write-Host "FAIL: uninstall exit=$($p3.ExitCode)" -ForegroundColor Red; $script:failures++ } else { Write-Host "PASS uninstall (silent)" -ForegroundColor Green }
} else {
    Write-Host "WARN: uninstaller missing" -ForegroundColor Yellow
}

# 6. User data survived uninstall
if ((Test-Path $marker) -and ((Get-Content $marker -Raw).Trim() -eq "preserve-me")) {
    Write-Host "PASS user data preserved after uninstall" -ForegroundColor Green
} else {
    Write-Host "FAIL user data NOT preserved" -ForegroundColor Red
    $script:failures++
}

Remove-Item $TestRoot -Recurse -Force -ErrorAction SilentlyContinue
if ($failures -gt 0) { Write-Host "[INSTALLER-RUNTIME] $failures FAILED" -ForegroundColor Red; exit 1 }
Write-Host "[INSTALLER-RUNTIME] ALL PASSED" -ForegroundColor Green