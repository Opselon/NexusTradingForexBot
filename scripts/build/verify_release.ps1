# =============================================================================
# Nexus Scalp Engine — Release Self-Check / Smoke Test (Windows PowerShell)
# =============================================================================
# Usage:  .\scripts\build\verify_release.ps1 [-Root release\v9.0.0\windows\x64]
#
# Verifies (spec section 9 / 42 / 43):
#   1. EXE exists + launches + reports version
#   2. CLI starts + health works
#   3. required assets present (Web, configs, docs, licenses, README.txt)
#   4. manifest + SHA256SUMS verify
#   5. no secret-shaped strings
#   6. default config is not LIVE
#   7. (optional) checksums file matches every artifact hash
# =============================================================================
param(
    [string]$Root = ""
)

$ErrorActionPreference = "Stop"
$Py = Join-Path $PSScriptRoot "..\..\.venv\Scripts\python.exe"
$RootPy = $PSScriptRoot

if (-not $Root) {
    $Version = (Select-String -Path (Join-Path $PSScriptRoot "..\..\pyproject.toml") -Pattern '^version = "([^"]+)"').Matches[0].Groups[1].Value
    $Root = Join-Path $PSScriptRoot "..\..\release\v$Version\windows\x64\portable"
}
$Root = (Resolve-Path $Root).Path
if (-not (Test-Path $Root)) {
    Write-Host "[VERIFY] Root not found: $Root" -ForegroundColor Red
    exit 1
}

Write-Host "[VERIFY] Release root: $Root" -ForegroundColor Cyan

# 1. EXE exists
$Exe = Join-Path $Root "NexusScalpEngine.exe"
if (-not (Test-Path $Exe)) {
    Write-Host "[VERIFY] FAIL: exe missing" -ForegroundColor Red
    exit 1
}
Write-Host "[VERIFY] PASS: exe present" -ForegroundColor Green

# 2. Launch + version + health
$v = & $Exe version --plain 2>&1 | Out-String
if ($LASTEXITCODE -ne 0 -or $v -notmatch "version") {
    Write-Host "[VERIFY] FAIL: version: $v" -ForegroundColor Red
    exit 1
}
Write-Host "[VERIFY] PASS: version -> $($v.Trim())" -ForegroundColor Green

$h = & $Exe health --json 2>&1 | Out-String
if ($h -notmatch '"overall"') {
    Write-Host "[VERIFY] FAIL: health JSON missing: $h" -ForegroundColor Red
    exit 1
}
Write-Host "[VERIFY] PASS: health ok" -ForegroundColor Green

# 3. Assets
foreach ($a in @("Web", "configs", "docs", "licenses", "README.txt")) {
    if (-not (Test-Path (Join-Path $Root $a))) {
        Write-Host "[VERIFY] FAIL: missing asset $a" -ForegroundColor Red
        exit 1
    }
}
Write-Host "[VERIFY] PASS: assets present" -ForegroundColor Green

# 4. Python verifier (checksums/manifest/secrets/no-LIVE)
& $Py -c "import sys; sys.path.insert(0, 'src'); from pathlib import Path; from nexus_scalp.release import verify as v; r = v.verify_release(Path(r'$Root')); print('OVERALL:', r['overall']); [print(f\"{c['status']:5} {c['check']} — {c['detail'][:100]}\") for c in r['checks']]; sys.exit(0 if r['valid'] else 1)"
if ($LASTEXITCODE -ne 0) {
    Write-Host "[VERIFY] FAIL: release verification (python)" -ForegroundColor Red
    exit 1
}

Write-Host "`n[VERIFY] ALL PASSED" -ForegroundColor Green