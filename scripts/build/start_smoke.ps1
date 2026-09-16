# =============================================================================
# Nexus Scalp Engine — Packaged Fresh-Install START Smoke (BUG-293)
# =============================================================================
# Usage:  .\start_smoke.ps1 -ExePath <path\to\NexusScalpEngine.exe> [-DurationSec 60]
#
# Launches the packaged EXE EXACTLY like a user's first double-click (bare,
# no args -> PAPER default) from a CLEAN workspace and asserts it reaches a
# live running state instead of crashing. The v9.0.12 release died here with
#   ArtifactIntegrityError: LOAD_REJECTED: artifact missing or empty (model.pt)
# followed by [PYI-...ERROR] Failed to execute script 'packaged_main' — and
# every CI gate stayed green because nothing ever launched the built EXE in a
# START shape (only `version` / `health`, which do not construct the engine).
#
# PASS criteria (all required):
#   * process still alive after DurationSec   (a crash exits early)
#   * NO crash markers in the captured output
# FAIL prints the log tail for triage and exits 1 -> blocks the release job.
# =============================================================================
param(
    [Parameter(Mandatory = $true)][string]$ExePath,
    [int]$DurationSec = 60,
    [string]$WorkspaceRoot = ""
)

$ErrorActionPreference = "Stop"
if (-not (Test-Path $ExePath)) {
    Write-Host "[START-SMOKE] EXE missing: $ExePath" -ForegroundColor Red
    exit 1
}
$ExePath = (Resolve-Path $ExePath).Path

# Clean workspace: run from a fresh temp CWD (the packaged engine anchors
# artifacts to the bundle dir, but the double-click CWD is arbitrary — pin
# both shapes by launching from an empty dir).
if (-not $WorkspaceRoot) {
    $WorkspaceRoot = Join-Path $env:TEMP ("nse-start-smoke-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
}
New-Item -ItemType Directory -Force -Path $WorkspaceRoot | Out-Null
$outFile = Join-Path $WorkspaceRoot "start.log"
$errFile = Join-Path $WorkspaceRoot "start.err.log"

Write-Host "[START-SMOKE] launching $ExePath (bare launch = paper default) for ${DurationSec}s" -ForegroundColor Cyan
$proc = Start-Process -FilePath $ExePath -WorkingDirectory $WorkspaceRoot `
    -RedirectStandardOutput $outFile -RedirectStandardError $errFile `
    -PassThru -WindowStyle Hidden

$deadline = (Get-Date).AddSeconds($DurationSec)
$crashMarkers = @(
    "ArtifactIntegrityError",
    "MODEL_LOAD_REJECTED",
    "Failed to execute script",
    "[PYI-",
    "Traceback (most recent call last)"
)
$failText = ""
while ((Get-Date) -lt $deadline) {
    Start-Sleep -Seconds 2
    if ($proc.HasExited) {
        $failText = "process exited early (exit=$($proc.ExitCode))"
        break
    }
}
$alive = -not $proc.HasExited
if ($alive) {
    Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue
    Start-Sleep -Seconds 1
}

$captured = ""
foreach ($f in @($outFile, $errFile)) {
    if (Test-Path $f) { $captured += (Get-Content $f -Raw -ErrorAction SilentlyContinue) }
}

$hit = $crashMarkers | Where-Object { $captured -match [regex]::Escape($_) }
if ($hit) { $failText = "crash markers present: $($hit -join ', ')" }

Write-Host "[START-SMOKE] log summary:" -ForegroundColor Cyan
Write-Host ($captured -split "`n" | Select-Object -First 8 | ForEach-Object { "    $_" })
$starter = if ($captured -match "STARTER_PROVISIONED") { "starter minted (first run)" }
           elseif ($captured -match "ARTIFACT_INTEGRITY.*VERIFIED") { "existing bundle VERIFIED" }
           else { "no starter/VERIFIED line" }
Write-Host "[START-SMOKE] model bootstrap evidence: $starter"

if ($failText) {
    Write-Host "[START-SMOKE] FAILED: $failText" -ForegroundColor Red
    Write-Host "--- log tail ---" -ForegroundColor Red
    Write-Host (($captured -split "`n" | Select-Object -Last 40) -join "`n")
    Remove-Item $WorkspaceRoot -Recurse -Force -ErrorAction SilentlyContinue
    exit 1
}
if (-not $alive) {
    # Not crashed with a marker, but gone before the window: suspicious.
    Write-Host "[START-SMOKE] FAILED: process exited before ${DurationSec}s without a clean crash marker" -ForegroundColor Red
    Remove-Item $WorkspaceRoot -Recurse -Force -ErrorAction SilentlyContinue
    exit 1
}
Remove-Item $WorkspaceRoot -Recurse -Force -ErrorAction SilentlyContinue
Write-Host "[START-SMOKE] PASS: packaged engine ran clean for ${DurationSec}s" -ForegroundColor Green
exit 0
