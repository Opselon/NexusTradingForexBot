<#
.SYNOPSIS
    Nexus Scalp Engine (NSE) - Pre-Push Quality & CI Verification Script
.DESCRIPTION
    Runs Ruff Lint/Format, Mypy static analysis, and Pytest suites before
    pushing to GitHub.

    PERFORMANCE: pytest runs with pytest-xdist across ALL CPU cores
    (`-n auto`, worksteal distribution) — measured ~5x faster than serial on
    the heavy suites (139s -> 26s). Ruff and Mypy stages run CONCURRENTLY
    where safe (mypy src | pytest tests) via background jobs.

    LOGGING: every run writes to a fresh timestamped folder
    `artifacts/logs/beforepush_<yyyyMMdd_HHmmss>/` containing:
      - run.log      (full combined output of all stages)
      - error.log    (stage name + exit code + tail of failing output)
      - warning.log  (pytest warnings / ruff warnings summary)
    Old run folders are pruned: only the newest 3 are kept.
#>

$ErrorActionPreference = "Stop"

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------
$KeepRuns = 3
$Cores = [Math]::Max(2, [Environment]::ProcessorCount)
$LogRoot  = Join-Path (Get-Location) "artifacts\logs"
$RunStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$RunDir   = Join-Path $LogRoot "beforepush_$RunStamp"
$RunLog   = Join-Path $RunDir "run.log"
$ErrLog   = Join-Path $RunDir "error.log"
$WarnLog  = Join-Path $RunDir "warning.log"

# -----------------------------------------------------------------------------
# Logging & UI helpers
# -----------------------------------------------------------------------------
function Write-Step {
    param([string]$Message)
    $line = "========================================================"
    Write-Host "`n$line" -ForegroundColor Cyan
    Write-Host " 🚀 $Message" -ForegroundColor Cyan
    Write-Host "$line" -ForegroundColor Cyan
    Add-Content -Path $RunLog -Value "`n$line`n[$(Get-Date -Format 'HH:mm:ss')] $Message`n$line"
}

function Write-Success {
    param([string]$Message)
    Write-Host " ✔ $Message" -ForegroundColor Green
    Add-Content -Path $RunLog -Value "[$(Get-Date -Format 'HH:mm:ss')] OK  : $Message"
}

function Write-Warn {
    param([string]$Message)
    Write-Host " ⚠ $Message" -ForegroundColor Yellow
    Add-Content -Path $WarnLog -Value "[$(Get-Date -Format 'HH:mm:ss')] WARN: $Message"
    Add-Content -Path $RunLog -Value "[$(Get-Date -Format 'HH:mm:ss')] WARN: $Message"
}

function Write-Failure {
    param(
        [string]$Stage,
        [string]$Message,
        [int]$ExitCode
    )
    $detail = "❌ [$Stage] exit=$ExitCode : $Message"
    Write-Host "`n ❌ ERROR: $detail" -ForegroundColor Red
    Add-Content -Path $ErrLog -Value "[$(Get-Date -Format 'HH:mm:ss')] $detail"
    Add-Content -Path $RunLog -Value "[$(Get-Date -Format 'HH:mm:ss')] ERROR: $detail"
    Write-Host " ⛔ Push aborted to protect CI/CD pipeline." -ForegroundColor Red
    Write-Host " 📄 Full output: $RunLog" -ForegroundColor Yellow
    Write-Host " 📄 Errors:      $ErrLog" -ForegroundColor Yellow
    exit 1
}

# Tail a file into the error log (last N lines, native tail.exe)
function Append-Tail {
    param([string]$File, [string]$Stage, [int]$Lines = 40)
    if (Test-Path $File) {
        Add-Content -Path $ErrLog -Value "--- tail of $Stage output ($File) ---"
        & tail.exe -n $Lines $File 2>$null | ForEach-Object {
            Add-Content -Path $ErrLog -Value $_
        }
    }
}

# -----------------------------------------------------------------------------
# Toolchain resolution — ALWAYS use the project venv (never PATH).
# On this host bare `pytest`/`mypy`/`ruff` resolve to Hermes' own venv which
# lacks pytest-xdist. The project .venv is the single source of truth.
# -----------------------------------------------------------------------------
$VenvPy = Join-Path (Get-Location) ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
    $VenvPy = "python"
    Write-Warn ".venv python not found — falling back to PATH python"
}
$PytestCmd = @($VenvPy, "-m", "pytest")
$MyPyCmd   = @($VenvPy, "-m", "mypy")
$RuffCmd   = @($VenvPy, "-m", "ruff")

# -----------------------------------------------------------------------------
# 0. PREPARE LOG FOLDER + PRUNE OLD RUNS
# -----------------------------------------------------------------------------
New-Item -ItemType Directory -Path $RunDir -Force | Out-Null
Add-Content -Path $RunLog -Value "# NSE beforePush run $RunStamp | cores=$Cores | started $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')"
Write-Success "Log folder: $RunDir"

# Keep only the newest $KeepRuns run folders
$oldRuns = Get-ChildItem -Path $LogRoot -Directory -Filter "beforepush_*" |
    Sort-Object Name -Descending |
    Select-Object -Skip $KeepRuns
foreach ($old in $oldRuns) {
    try {
        Remove-Item -Path $old.FullName -Recurse -Force -ErrorAction Stop
        Write-Warn "Pruned old run folder: $($old.Name)"
    } catch {
        Write-Warn "Could not prune $($old.Name): $($_.Exception.Message)"
    }
}

# -----------------------------------------------------------------------------
# 1. RUFF LINT & FORMAT (serial — both mutate the same files, must not race)
# -----------------------------------------------------------------------------
$Stage = "ruff"
$RuffLog = Join-Path $RunDir "ruff.log"
New-Item -ItemType Directory -Path (Split-Path $RuffLog) -Force | Out-Null

Write-Step "1/4: Ruff Lint + Format"
& $RuffCmd[0] $RuffCmd[1] $RuffCmd[2] check . --fix --unsafe-fixes *> $RuffLog
if ($LASTEXITCODE -ne 0) {
    Append-Tail $RuffLog $Stage
    Write-Failure $Stage "Ruff Lint found unfixable errors." $LASTEXITCODE
}
& $RuffCmd[0] $RuffCmd[1] $RuffCmd[2] format . *>> $RuffLog
if ($LASTEXITCODE -ne 0) {
    Append-Tail $RuffLog $Stage
    Write-Failure $Stage "Ruff format failed." $LASTEXITCODE
}
& $RuffCmd[0] $RuffCmd[1] $RuffCmd[2] format --check . *>> $RuffLog
if ($LASTEXITCODE -ne 0) {
    Append-Tail $RuffLog $Stage
    Write-Failure $Stage "Ruff formatting check failed." $LASTEXITCODE
}
Add-Content -Path $RunLog -Value (Get-Content $RuffLog -Raw)
Write-Success "Ruff lint + format clean"

# -----------------------------------------------------------------------------
# 2+3. MYPY src  ||  PYTEST -n $Cores  (CONCURRENT via background jobs)
# -----------------------------------------------------------------------------
$MyPyLog  = Join-Path $RunDir "mypy.log"
$PyTestLog = Join-Path $RunDir "pytest.log"

Write-Step "2/4+3/4: Mypy (src) || Pytest (-n $Cores, worksteal) — running in parallel"

$mypyJob = Start-Job -ScriptBlock {
    param($log, $venvPy)
    & $venvPy -m mypy src *> $log
    $script:code = $LASTEXITCODE
    Set-Content -Path "$log.exit" -Value $script:code
} -ArgumentList $MyPyLog, $VenvPy

$pytestJob = Start-Job -ScriptBlock {
    param($log, $cores, $venvPy)
    & $venvPy -m pytest tests/unit/ -n $cores --dist worksteal -q --tb=short *> $log
    $script:code = $LASTEXITCODE
    Set-Content -Path "$log.exit" -Value $script:code
} -ArgumentList $PyTestLog, $Cores, $VenvPy

# Wait for both
Wait-Job $mypyJob, $pytestJob | Out-Null
Remove-Job $mypyJob, $pytestJob -Force

# --- pytest result ---
$pytestExit = if (Test-Path "$PyTestLog.exit") { [int](Get-Content "$PyTestLog.exit") } else { -1 }
$mypyExit   = if (Test-Path "$MyPyLog.exit") { [int](Get-Content "$MyPyLog.exit") } else { -1 }

# Warnings -> warning.log (pytest warnings section + ruff warnings)
if (Test-Path $PyTestLog) {
    Get-Content $PyTestLog | Where-Object { $_ -match "Warning|warning|SKIPPED|Deprecation" } |
        Add-Content $WarnLog
}
if (Test-Path $RuffLog) {
    Get-Content $RuffLog | Where-Object { $_ -match "warning|Warning" } |
        Add-Content $WarnLog
}

# Capture tails / summaries into run.log
Add-Content -Path $RunLog -Value "`n--- pytest (-n $Cores) ---"
Get-Content $PyTestLog -ErrorAction SilentlyContinue | Select-Object -Last 30 | Add-Content $RunLog
Add-Content -Path $RunLog -Value "`n--- mypy ---"
Get-Content $MyPyLog -ErrorAction SilentlyContinue | Select-Object -Last 15 | Add-Content $RunLog

if ($pytestExit -ne 0) {
    Append-Tail $PyTestLog "pytest"
    Write-Failure "pytest" "pytest exited with code $pytestExit." $pytestExit
}
if ($mypyExit -ne 0) {
    Append-Tail $MyPyLog "mypy"
    Write-Failure "mypy" "Mypy type checking failed." $mypyExit
}

Write-Success "Mypy static type verification passed with 0 errors!"
Write-Success "All tests passed (pytest exit 0)!"

# -----------------------------------------------------------------------------
# ALL CHECKS PASSED
# -----------------------------------------------------------------------------
Write-Host "`n========================================================" -ForegroundColor Green
Write-Host " 🎉 ALL CHECKS PASSED! YOUR CODE IS 100% CI/CD READY." -ForegroundColor Green
Write-Host "========================================================`n" -ForegroundColor Green
Write-Success "Run completed: $RunDir"

$pushChoice = Read-Host "Would you like to stage, commit, and push now? (y/n)"
if ($pushChoice -eq "y" -or $pushChoice -eq "Y") {
    $commitMsg = Read-Host "Enter commit message"
    if ([string]::IsNullOrWhiteSpace($commitMsg)) {
        $commitMsg = "chore: pre-push verified quality update"
    }

    git add .
    git commit -m "$commitMsg"
    git push
    Write-Host "`n🚀 Successfully pushed to remote repository!`n" -ForegroundColor Cyan
} else {
    Write-Host "`nChanges verified. You can manually push when ready.`n" -ForegroundColor Yellow
}