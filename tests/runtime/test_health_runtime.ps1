# =============================================================================
# Nexus Scalp Engine — RUNTIME TEST: Health/Doctor on the REAL packaged EXE
# =============================================================================
# Usage:  .\tests\runtime\test_health_runtime.ps1 [-Exe path\to\NexusScalpEngine.exe]
#
# Verifies the packaged health engine reports ALL 19 categories with a valid
# verdict (PASS/WARNING/FAIL/UNKNOWN) and never fabricates a result.
# =============================================================================
param([string]$Exe = "")

$ErrorActionPreference = "Stop"
if (-not $Exe) {
    $Exe = Join-Path $PSScriptRoot "..\..\release\v$((Select-String -Path (Join-Path $PSScriptRoot "..\..\pyproject.toml") -Pattern '^version = "([^"]+)"').Matches[0].Groups[1].Value)\windows\x64\portable\NexusScalpEngine.exe"
}
$Exe = (Resolve-Path $Exe).Path
if (-not (Test-Path $Exe)) { Write-Host "FAIL: EXE not found at $Exe" -ForegroundColor Red; exit 1 }

$out = & $Exe health --json 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) { Write-Host "FAIL: health exit=$LASTEXITCODE" -ForegroundColor Red; exit 1 }
try { $h = $out | ConvertFrom-Json } catch {
    Write-Host "FAIL: health not JSON: $($out.Substring(0, [Math]::Min(200, $out.Length)))" -ForegroundColor Red
    exit 1
}

$expected = @("SYSTEM","RUNTIME","CONFIGURATION","DATABASE","MODEL","FEATURE_SCHEMA","GPU","MT5","NETWORK","DISK","MEMORY","LOGGING","WORKERS","NEWS","EXPERIENCE","RESEARCH","TRAINING","SHADOW","ACCOUNTING")
$verdicts = @("PASS","WARNING","FAIL","UNKNOWN")
$failures = 0
foreach ($cat in $expected) {
    $found = $h.checks | Where-Object { $_.category -eq $cat }
    if (-not $found) { Write-Host "FAIL: missing category $cat" -ForegroundColor Red; $script:failures++; continue }
    if ($verdicts -notcontains $found.verdict) {
        Write-Host "FAIL: $cat verdict '$($found.verdict)' not in allowed set" -ForegroundColor Red
        $script:failures++
        continue
    }
    if (-not $found.reason) {
        Write-Host "FAIL: $cat empty reason (fabricated?)" -ForegroundColor Red
        $script:failures++
        continue
    }
    Write-Host "PASS $($cat.PadRight(18)) $($found.verdict)" -ForegroundColor Green
}

if ($failures -gt 0) { Write-Host "[HEALTH-RUNTIME] $failures FAILED" -ForegroundColor Red; exit 1 }
Write-Host "[HEALTH-RUNTIME] ALL PASSED — $($expected.Count) categories, real verdicts" -ForegroundColor Green