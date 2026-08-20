<#
.SYNOPSIS
    Nexus Scalp Engine (NSE) - Pre-Push Quality & CI Verification Script (Windows)

.DESCRIPTION
    Local mirror of the GitHub Actions "quality" job (.github/workflows/ci.yml).
    Runs the EXACT same gates with the EXACT same flags so a green local run
    predicts a green GitHub run:

      ruff lint    -> ruff check .                    (JSON + human text, like CI)
      ruff format  -> ruff format --check .           (validates, NEVER rewrites;
                      use -Fix for the legacy auto-format behaviour)
      mypy         -> mypy src --junit-xml            (JUnit XML, like CI)
      pytest       -> critical suite, -n <workers> --dist loadgroup
                      --cov=src + junit.xml + coverage.xml + htmlcov (like CI)

    After the gates it builds the SAME ci-results/ tree the workflow uploads
    as an artifact (scripts/ci/make_ci_results.py), prints a summary.md-
    equivalent console report, runs the canonical forensic deploy gate
    (nexus forensic --deploy-gate), and offers a verified stage/commit/push
    flow with a live remote-ahead/behind check.

    KNOWN DIFFERENCES FROM CI (intentional, all documented below):
      * RAM-aware xdist workers instead of bare `-n auto` (OOM guard, BUG-073
        swarm lesson: each worker loads torch+polars, ~500 MB).
      * The forensic deploy gate runs before push (CI does not ship that gate;
        skip with -SkipGate).
      * -Fix can auto-fix/format locally (CI never rewrites files).

.LINK
    https://github.com/Opselon/NexusTradingForexBot/.github/workflows/ci.yml

.PARAMETER Fix
    Auto-fix ruff lint violations and auto-format before the validation pass
    (mutates files). Off by default because CI only validates.

.PARAMETER FullSuite
    Run the FULL unit suite (tests/unit/, ~1931 tests, ~20 min) instead of the
    fast CI-critical suite (tests/critical_suite.txt, ~5 min with xdist).

.PARAMETER SkipPush
    Do not prompt for stage/commit/push at the end (CI-style "check only" run).

.PARAMETER SkipGate
    Skip the canonical forensic deploy gate (faster iteration runs).

.PARAMETER Workers
    Override the RAM-aware xdist worker count. Default:
    min(CPU cores, max(2, availableRAM_GB / 1.5)).

.EXAMPLE
    ./beforePush.ps1                 # full gate + push prompt (critical suite)
    ./beforePush.ps1 -SkipPush       # full gate, no push prompt (CI dry run)
    ./beforePush.ps1 -FullSuite      # legacy full unit suite
    ./beforePush.ps1 -Fix -SkipPush  # auto-fix, validate, no push
#>
[CmdletBinding()]
param(
    [switch]$Fix,
    [switch]$FullSuite,
    [switch]$SkipPush,
    [switch]$SkipGate,
    [int]$Workers = 0
)

# Stop on terminating errors; every native tool exit code is checked EXPLICITLY
# via $LASTEXITCODE (PowerShell does NOT throw on non-zero native exits, and a
# bare try/catch around pytest/ruff silently reports success - BUG-059 class).
$ErrorActionPreference = "Stop"

# Make emoji / Persian glyphs render in Windows Terminal and modern consoles.
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
try { $OutputEncoding       = [System.Text.Encoding]::UTF8 } catch { }

# =============================================================================
# CONFIGURATION
# =============================================================================
$RepoRoot = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
Set-Location $RepoRoot

$KeepRuns  = 3
$CiRoot    = Join-Path $RepoRoot "ci-results"
$GateOut   = Join-Path $RepoRoot "artifacts\forensics\deploy_gate_result.json"

# RAM-aware worker count (BUG-073): each xdist worker loads torch + polars
# (~500 MB). Cap by available memory as well as cores:
#   workers = min(cores, max(2, availGB / 1.5))
$Cores = [Math]::Max(2, [Environment]::ProcessorCount)
try {
    $os      = Get-CimInstance Win32_OperatingSystem
    $availGB = [Math]::Round(($os.FreePhysicalMemory / 1MB), 1)
    $MemCap  = [Math]::Max(2, [int][Math]::Floor($availGB / 1.5))
    if ($MemCap -lt $Cores) { $Cores = $MemCap }
} catch {
    $availGB = $null
}
if ($Workers -gt 0) { $Cores = $Workers }

# --- log folder -----------------------------------------------------------
$LogRoot  = Join-Path $RepoRoot "artifacts\logs"
$RunStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$RunDir   = Join-Path $LogRoot "beforepush_$RunStamp"
$RunLog   = Join-Path $RunDir "run.log"
$ErrLog   = Join-Path $RunDir "error.log"
$WarnLog  = Join-Path $RunDir "warning.log"
New-Item -ItemType Directory -Path $RunDir -Force | Out-Null

# =============================================================================
# UI & LOGGING HELPERS (ALL DEFINED BEFORE ANY USE - PowerShell does not hoist
# functions; the old script called Write-Warn before its definition and died
# instantly under $ErrorActionPreference="Stop")
# =============================================================================
function Write-Banner {
    param([string]$Title)
    $line = "=" * 64
    Write-Host ""
    Write-Host $line -ForegroundColor Cyan
    Write-Host "  $Title" -ForegroundColor Cyan
    Write-Host $line -ForegroundColor Cyan
    Write-Log $RunLog "`n$line`n$Title`n$line"
}

function Write-Step {
    param([int]$Index, [int]$Total, [string]$Title)
    $line = "-" * 60
    Write-Host ""
    Write-Host $line -ForegroundColor DarkCyan
    Write-Host "  [$Index/$Total] $Title" -ForegroundColor Cyan
    Write-Host $line -ForegroundColor DarkCyan
    Write-Log $RunLog "`n--- STEP $Index/${Total}: $Title ---"
}

function Write-Info {
    param([string]$Message)
    Write-Host "  $Message" -ForegroundColor Gray
    Write-Log $RunLog "[$(Get-Date -Format 'HH:mm:ss')] INFO: $Message"
}

function Write-Cmd {
    param([string]$Command)
    Write-Host "    `$ $Command" -ForegroundColor DarkGray
    Write-Log $RunLog "[$(Get-Date -Format 'HH:mm:ss')] CMD : $Command"
}

function Write-Success {
    param([string]$Message)
    Write-Host "  [OK] $Message" -ForegroundColor Green
    Write-Log $RunLog "[$(Get-Date -Format 'HH:mm:ss')] OK  : $Message"
}

function Write-Warn {
    param([string]$Message)
    Write-Host "  [!!] $Message" -ForegroundColor Yellow
    Write-Log $WarnLog "[$(Get-Date -Format 'HH:mm:ss')] WARN: $Message"
    Write-Log $RunLog  "[$(Get-Date -Format 'HH:mm:ss')] WARN: $Message"
}

function Write-Fail {
    param(
        [string]$Stage,
        [string]$Message,
        [int]$ExitCode
    )
    $detail = "Stage [$Stage] exit=$ExitCode : $Message"
    Write-Host ""
    Write-Host "  [XX] ERROR: $detail" -ForegroundColor Red
    Write-Host "  [XX] Push aborted to protect the CI/CD pipeline." -ForegroundColor Red
    Write-Log $ErrLog "[$(Get-Date -Format 'HH:mm:ss')] $detail"
    Write-Log $RunLog "[$(Get-Date -Format 'HH:mm:ss')] ERROR: $detail"
    Write-Host "       Full output : $RunLog" -ForegroundColor Yellow
    Write-Host "       Errors      : $ErrLog" -ForegroundColor Yellow
    exit 1
}

# Write to any log file (creates parent + file as needed). Keeps $LASTEXITCODE
# untouched so it can be read after logging calls.
function Write-Log {
    param([string]$Path, [string]$Text)
    try {
        $dir = Split-Path $Path -Parent
        if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        Add-Content -Path $Path -Value $Text -ErrorAction SilentlyContinue
    } catch { }
}

# Native Get-Content -Tail (PS 3+) - REPLACES the old dependency on tail.exe
# which does not exist on every Windows box and threw a terminating
# CommandNotFoundException under -ErrorActionPreference Stop.
function Get-Tail {
    param([string]$File, [int]$Lines = 40)
    if (Test-Path $File) { Get-Content $File -Tail $Lines -ErrorAction SilentlyContinue }
}

function Show-Tail {
    param([string]$File, [string]$Stage, [int]$Lines = 40)
    $tail = @(Get-Tail $File $Lines)
    if ($tail.Count -gt 0) {
        Write-Host "  --- tail of $Stage output ($File) ---" -ForegroundColor DarkYellow
        foreach ($ln in $tail) { Write-Host "  | $ln" -ForegroundColor DarkYellow }
        Write-Log $ErrLog "--- tail of $Stage output ($File) ---"
        foreach ($ln in $tail) { Write-Log $ErrLog $ln }
    }
}

# JSON writer, BOM-FREE UTF-8 (a BOM breaks json.loads in make_ci_results.py -
# BUG-093 lesson with build-info.json).
function Set-JsonFile {
    param([string]$Path, $Object)
    $text = $Object | ConvertTo-Json -Depth 6
    $enc  = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $text, $enc)
}

# =============================================================================
# TOOLCHAIN — ALWAYS the project venv, never PATH (bare tool names resolve to
# other venvs on this host, e.g. Hermes' own, which lack pytest-xdist).
# =============================================================================
$VenvPy = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
    $VenvPy = "python"
    Write-Warn ".venv python not found at $VenvPy - falling back to PATH python"
}
$Stopped = New-Object System.Diagnostics.Stopwatch
$Stopped.Start()

# =============================================================================
# PHASE 0 - ENVIRONMENT SNAPSHOT
# =============================================================================
Write-Banner "NSE Pre-Push Quality Gate v2 (CI-mirror)"

$PyVer   = (& $VenvPy --version 2>&1 | Select-Object -First 1)
$RuffVer = (& $VenvPy -m ruff --version 2>&1 | Select-Object -First 1)
$MypyVer = (& $VenvPy -m mypy --version 2>&1 | Select-Object -First 1)
$PyVer   = if ($PyVer) { $PyVer.Trim() } else { "unknown" }
$RuffVer = if ($RuffVer) { $RuffVer.Trim() } else { "unknown" }
$MypyVer = if ($MypyVer) { $MypyVer.Trim() } else { "unknown" }

if ($PyVer -notmatch "3\.11") {
    Write-Warn "Local Python is $PyVer - GitHub CI runs Python 3.11 (pyproject target)."
}
if ($RuffVer -and $RuffVer -notmatch "0\.16\.3") {
    Write-Warn "Local ruff $RuffVer vs pinned CI ruff 0.16.3 - run: $VenvPy -m pip install -q ruff==0.16.3"
}
if ($env:GIT_TERMINAL_PROMPT) { $env:GIT_TERMINAL_PROMPT = "0" }  # push must never hang on a credential prompt

Write-Info "Repo        : $RepoRoot"
Write-Info "Python      : $PyVer"
Write-Info "Ruff        : $RuffVer"
Write-Info "Mypy        : $MypyVer"
$memInfo = ""
if ($null -ne $availGB) { $memInfo = " (avail RAM ~$availGB GB)" }
Write-Info "Workers     : $Cores$memInfo"
Write-Success "Log folder  : $RunDir"

# =============================================================================
# PHASE 1 - GIT PREFLIGHT (informational; never blocks on network)
# =============================================================================
Write-Step 1 7 "Git preflight - branch / distance / dirty tree"

# Guard against empty git output (detached HEAD, fresh repo) - .Trim() on
# $null is a terminating error under $ErrorActionPreference=Stop.
$Branch  = & git branch --show-current 2>$null | Select-Object -First 1
$Branch  = if ($Branch) { $Branch.Trim() } else { "(detached)" }
$HeadSha = & git rev-parse --short HEAD 2>$null | Select-Object -First 1
$HeadSha = if ($HeadSha) { $HeadSha.Trim() } else { "?" }
$Porcelain = @(& git status --porcelain 2>$null)
$DirtyCount = $Porcelain.Count

Write-Info "Branch  : $Branch  | HEAD: $HeadSha  | dirty entries: $DirtyCount"

if ($DirtyCount -gt 0) {
    $staged   = @($Porcelain | Where-Object { $_ -match '^[MADRC]' }).Count
    $unstaged = @($Porcelain | Where-Object { $_ -match '^.[MD]' }).Count
    $untrack  = @($Porcelain | Where-Object { $_ -match '^\?\?' }).Count
    Write-Warn "Working tree is NOT clean: $staged staged, $unstaged unstaged-modified, $untrack untracked."
    $junkRoot = @($Porcelain | Where-Object { $_ -match '^\?\?' -and $_ -notmatch '\?\? scratch/' })
    if ($junkRoot.Count -gt 0) {
        Write-Warn "Untracked items OUTSIDE scratch/: (repo-root hygiene mandate)"
        foreach ($j in $junkRoot) { Write-Info "  $j" }
    }
} else {
    Write-Success "Working tree clean."
}

# Remote distance (fast, best-effort; no network = warn and continue)
try {
    & git fetch --quiet --prune 2>$null
    if ($LASTEXITCODE -ne 0) { throw "git fetch failed" }
    $dist = & git rev-list --left-right --count "@{u}"...HEAD 2>$null | Select-Object -First 1
    $dist = if ($dist) { $dist.Trim() } else { "" }
    if ($dist -match '^\s*(\d+)\s+(\d+)\s*$') {
        $behind = [int]$Matches[1]; $ahead = [int]$Matches[2]
        if ($behind -gt 0) {
            Write-Warn "Branch is BEHIND upstream by $behind commit(s) - pull/rebase before pushing."
        } elseif ($ahead -gt 0) {
            Write-Info "Branch is ahead of upstream by $ahead commit(s) (will be pushed)."
        } else {
            Write-Success "Branch in sync with upstream."
        }
    } else {
        Write-Warn "No upstream tracking branch configured - skipping ahead/behind check."
    }
} catch {
    Write-Warn "Remote check skipped (network or upstream unavailable): $($_.Exception.Message)"
}

# =============================================================================
# PHASE 2 - RUFF LINT + FORMAT (CI-identical validation)
# =============================================================================
Write-Step 2 7 "Ruff lint + format (mirrors ci.yml steps 'Ruff - Lint' / 'Ruff - Format check')"

$LintJson  = Join-Path $CiRoot "ruff\lint.json"
$LintErr   = Join-Path $CiRoot "ruff\lint.stderr.txt"
$LintTxt   = Join-Path $CiRoot "ruff\lint.txt"
$FormatTxt = Join-Path $CiRoot "format\format.txt"
$RuffLog   = Join-Path $RunDir "ruff.log"

# CI initializes the results tree FIRST (wiping any stale tree), then every
# check writes into its own subdirectory - same order as ci.yml.
Write-Cmd "$VenvPy scripts/ci/make_ci_results.py init $CiRoot"
& $VenvPy scripts/ci/make_ci_results.py init $CiRoot --json "$CiRoot\run-info\env-digest.json" *> $RunLog 2>&1

if ($Fix) {
    Write-Warn "-Fix set: auto-fixing lint + format BEFORE validation (files will be rewritten)."
    Write-Cmd "$VenvPy -m ruff check . --fix --unsafe-fixes"
    & $VenvPy -m ruff check . --fix --unsafe-fixes *> $RuffLog
    if ($LASTEXITCODE -ne 0) {
        Show-Tail $RuffLog "ruff --fix"
        Write-Fail "ruff-fix" "Ruff --fix left unfixable violations." $LASTEXITCODE
    }
    Write-Cmd "$VenvPy -m ruff format ."
    & $VenvPy -m ruff format . *>> $RuffLog
    if ($LASTEXITCODE -ne 0) {
        Show-Tail $RuffLog "ruff format"
        Write-Fail "ruff-format" "Ruff format failed." $LASTEXITCODE
    }
    Write-Success "Auto-fix pass complete (ruff check --fix + ruff format)."
}

# --- lint: JSON machine output + human text, exactly like CI -----------------
Write-Cmd "$VenvPy -m ruff check . --output-format json"
& $VenvPy -m ruff check . --output-format json *> $LintJson
$ruffLintRc = $LASTEXITCODE
Write-Cmd "$VenvPy -m ruff check . > lint.txt (derived from JSON, no re-scan)"
try {
    $viol = Get-Content $LintJson -Raw | ConvertFrom-Json
    if ($viol -is [array] -and $viol.Count -gt 0) {
        $viol | ForEach-Object {
            $fn = $_.filename; $ln = $_.location.row; $col = $_.location.column; $code = $_.code; $msg = $_.message
            "$fn`:$ln`:$col`: $code $msg" | Add-Content $LintTxt
        }
        Add-Content $LintTxt ""
    } else {
        "All checks passed!" | Add-Content $LintTxt
    }
} catch {
    "All checks passed!" | Add-Content $LintTxt
}
# note: CI re-runs ruff for the human text (exit ignored); local derives it from
# the same JSON so no second full-tree scan is needed.

# --- format: validate only, never rewrite (unless -Fix already ran) ----------
Write-Cmd "$VenvPy -m ruff format --check ."
& $VenvPy -m ruff format --check . *> $FormatTxt
$ruffFmtRc = $LASTEXITCODE

if ($ruffLintRc -eq 0) { Write-Success "Ruff lint: 0 violations." }
else {
    Show-Tail $LintTxt "ruff lint" 30
    Write-Fail "ruff_lint" "Ruff lint found violations (see $LintJson / $LintTxt)." $ruffLintRc
}
if ($ruffFmtRc -eq 0) { Write-Success "Ruff format: all files formatted." }
else {
    Show-Tail $FormatTxt "ruff format" 30
    Write-Fail "ruff_format" "Ruff format check failed (run '$VenvPy -m ruff format .' or .\beforePush.ps1 -Fix)." $ruffFmtRc
}

& $VenvPy -m ruff check . 2>&1 | Where-Object { $_ -match '[Ww]arning' } | ForEach-Object { Write-Log $WarnLog $_ }

# =============================================================================
# PHASE 3+4 - MYPY  ||  PYTEST  (concurrent background jobs, like the old gate)
#   mypy  : mypy src --junit-xml ...          (ci.yml 'Mypy - Type checking')
#   pytest: critical suite, -n workers --dist loadgroup --cov=src
#           + junit.xml + coverage.xml + htmlcov  (ci.yml 'Pytest with coverage')
#   --dist loadgroup is the CI distribution mode; the old script used
#   worksteal - a real source of "passes locally, fails on GitHub".
# =============================================================================
Write-Step 3 7 "Mypy (src) || Pytest (critical suite) - running in parallel"

$MyPyLog   = Join-Path $RunDir "mypy.log"
$PyTestLog = Join-Path $CiRoot "pytest\pytest.txt"
$MyPyJUnit = Join-Path $CiRoot "mypy\mypy-junit.xml"
$JunitXml  = Join-Path $CiRoot "pytest\junit.xml"
$CovXml    = Join-Path $CiRoot "pytest\coverage.xml"
$CovHtml   = Join-Path $CiRoot "pytest\htmlcov"

# Critical suite (default gate) or full unit suite (-FullSuite, ~20 min legacy)
if ($FullSuite) {
    $PytestTarget = @("tests/unit/")
    Write-Warn "FULL suite gate requested (tests/unit/, ~20 min - legacy gate)."
} elseif (Test-Path "tests\critical_suite.txt") {
    $PytestTarget = @(Get-Content "tests\critical_suite.txt" |
        Where-Object { $_ -match '\S' -and $_ -notmatch '^\s*#' })
    if ($PytestTarget.Count -eq 0) { $PytestTarget = @("tests/unit/") }
    Write-Info "Critical-suite gate: $($PytestTarget.Count) files from tests/critical_suite.txt"
} else {
    $PytestTarget = @("tests/unit/")
    Write-Warn "tests/critical_suite.txt missing - falling back to full tests/unit/."
}

# Start-Job spawns a fresh PowerShell whose cwd is NOT inherited reliably
# (observed: System32). Set-Location to the repo root inside every job - the
# same fix that resolved BUG-073. Each job writes its real exit code to a
# ".exit" file because jobs do not share variables with the parent scope.
$RunRoot = $RepoRoot
$mypyJob = Start-Job -ScriptBlock {
    param($log, $ui, $venvPy, $runRoot)
    Set-Location -Path $runRoot
    try {
        & $venvPy -m mypy src --junit-xml "$runRoot\ci-results\mypy\mypy-junit.xml" *> $log
        $code = $LASTEXITCODE
        Copy-Item -Path $log -Destination "$runRoot\ci-results\mypy\mypy.txt" -Force -ErrorAction SilentlyContinue
    } catch {
        $code = 900
        $_ | Out-String | Add-Content $log
    }
    Set-Content -Path "$log.exit" -Value $code
} -ArgumentList $MyPyLog, $null, $VenvPy, $RunRoot

$pytestArgs = @($PytestTarget) + @(
    "-n", "$Cores", "--dist", "loadgroup",
    "--cov=src", "--cov-report=term-missing",
    "--cov-report=xml:$CovXml", "--cov-report=html:$CovHtml",
    "--junitxml=$JunitXml", "-q", "--tb=short"
)
$pytestJob = Start-Job -ScriptBlock {
    param($log, $venvPy, $runRoot, $argsList)
    Set-Location -Path $runRoot
    try {
        & $venvPy -m pytest @argsList *> $log
        $code = $LASTEXITCODE
    } catch {
        $code = 900
        $_ | Out-String | Add-Content $log
    }
    Set-Content -Path "$log.exit" -Value $code
} -ArgumentList $PyTestLog, $VenvPy, $RunRoot, $pytestArgs

Write-Cmd "$VenvPy -m mypy src --junit-xml ci-results/mypy/mypy-junit.xml"
Write-Cmd "$VenvPy -m pytest $($PytestTarget -join ' ') -n $Cores --dist loadgroup --cov=src --cov-report=term-missing --cov-report=xml:ci-results/pytest/coverage.xml --cov-report=html:ci-results/pytest/htmlcov --junitxml=ci-results/pytest/junit.xml -q --tb=short"

Wait-Job $mypyJob, $pytestJob | Out-Null
Remove-Job $mypyJob, $pytestJob -Force

$pytestExit = if (Test-Path "$PyTestLog.exit") { [int](Get-Content "$PyTestLog.exit") } else { -1 }
$mypyExit   = if (Test-Path "$MyPyLog.exit")   { [int](Get-Content "$MyPyLog.exit") }   else { -1 }

if (Test-Path $PyTestLog) {
    Get-Content $PyTestLog | Where-Object { $_ -match 'warning|Warning|SKIPPED|Deprecation|error' } |
        ForEach-Object { Write-Log $WarnLog $_ }
}

if ($mypyExit -eq 0) { Write-Success "Mypy completed: 0 type errors." }
else {
    Show-Tail (Join-Path $CiRoot "mypy\mypy.txt") "mypy" 30
    Write-Fail "mypy" "Mypy type checking failed (exit $mypyExit)." $mypyExit
}
if ($pytestExit -eq 0) { Write-Success "Pytest completed: critical suite green." }
else {
    Show-Tail $PyTestLog "pytest" 40
    Write-Fail "pytest" "Pytest exited $pytestExit (see $PyTestLog / $JunitXml)." $pytestExit
}

# =============================================================================
# PHASE 5 - CI RESULTS TREE (same tree GitHub uploads + final gate replication)
# =============================================================================
Write-Step 5 7 "Build ci-results/ tree via scripts/ci/make_ci_results.py (CI parity)"

# Record per-check outcomes (tree was initialized in Phase 2 - before the
# checks wrote into it - exactly like ci.yml).
& $VenvPy scripts/ci/make_ci_results.py check $CiRoot ruff_lint  $ruffLintRc "violations found (see ruff/)" *> $RunLog
& $VenvPy scripts/ci/make_ci_results.py check $CiRoot ruff_format $ruffFmtRc  "files would be reformatted (see format/)" *> $RunLog
& $VenvPy scripts/ci/make_ci_results.py check $CiRoot mypy        $mypyExit   "type errors found (see mypy/)" *> $RunLog
& $VenvPy scripts/ci/make_ci_results.py check $CiRoot pytest      $pytestExit "see pytest/junit.xml + pytest.txt" *> $RunLog

# Coverage + pytest stats - replicate the workflow's inline Python so the
# summary.md is byte-for-byte the same shape as CI's artifact.
$pct = $null
foreach ($line in @(Get-Content $PyTestLog -ErrorAction SilentlyContinue)) {
    if ($line -match 'TOTAL\s+\d+\s+\d+\s+(\d+)%') { $pct = [double]$Matches[1] }
}
$covStatus = if ($null -eq $pct) { "errored" } else { "passed" }
$cell = [ordered]@{
    check     = "coverage"
    status    = $covStatus
    exit_code = 0
    detail    = ("{0:n1}% line coverage" -f $pct)
    percent   = $pct
}
Set-JsonFile (Join-Path $CiRoot "run-info\coverage.json") $cell
Set-JsonFile (Join-Path $CiRoot "run-info\coverage-extra.json") ([ordered]@{ percent = $pct })

$tests = 0; $failedTests = 0; $skippedTests = 0
try {
    [xml]$junit = Get-Content $JunitXml -Raw
    foreach ($ts in @($junit.SelectNodes("//testsuite"))) {
        $tests += [int]$ts.tests
        $failedTests += [int]$ts.failures + [int]$ts.errors
        $skippedTests += [int]$ts.skipped
    }
} catch { }
$passedTests = $tests - $failedTests - $skippedTests
$pytestDetail = "$tests tests, $failedTests failed, $skippedTests skipped"
Set-JsonFile (Join-Path $CiRoot "run-info\pytest-extra.json") ([ordered]@{ detail = $pytestDetail })
$covDisplay = if ($null -eq $pct) { "n/a" } else { "{0:n1}%" -f $pct }
Write-Success "Coverage: $covDisplay | $pytestDetail"

& $VenvPy scripts/ci/make_ci_results.py summary  $CiRoot *> $RunLog
& $VenvPy scripts/ci/make_ci_results.py manifest $CiRoot *> $RunLog
Write-Success "CI tree ready: $CiRoot (run-info/summary.md, manifest.json, SHA256SUMS.txt)"

# --- replicate the workflow's final "Fail job if any check failed" step ------
$statusMap = @{}
foreach ($chk in @("ruff_lint","ruff_format","mypy","pytest","coverage")) {
    $p = Join-Path $CiRoot "run-info\$chk.json"
    try { $statusMap[$chk] = (Get-Content $p -Raw | ConvertFrom-Json).status } catch { $statusMap[$chk] = "errored" }
}
$failing = @($statusMap.GetEnumerator() | Where-Object { $_.Value -in @("failed","errored") } | ForEach-Object { $_.Key })
$statusLine = ($statusMap.GetEnumerator() | Sort-Object Key | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join " | "
Write-Info "CI-style status: $statusLine"
if ($failing.Count -gt 0) {
    Write-Fail "ci-gate" "CI gate failed on: $($failing -join ', ')  (mirrors GitHub's final step)" 1
}
Write-Success "ALL CHECKS PASSED (CI gate replication)."

# =============================================================================
# PHASE 6 - CANONICAL FORENSIC DEPLOY GATE (TASK-12)
#   exit 0 = ALLOW | 1 = BLOCK (CRITICAL) | 2 = REVIEW (DEGRADED/UNKNOWN)
#   | 3 = ENGINE UNAVAILABLE (fail-safe block). The hook only CALLS the
#   canonical engine (TASK-12 s5) - no duplicated health rules.
# =============================================================================
if (-not $SkipGate) {
    Write-Step 6 7 "Forensic deploy gate (nexus forensic --deploy-gate --json)"
    New-Item -ItemType Directory -Path (Split-Path $GateOut) -Force | Out-Null
    Write-Cmd "$VenvPy -m nexus_scalp.cli.main forensic --deploy-gate --json"
    & $VenvPy -m nexus_scalp.cli.main forensic --deploy-gate --json *> $GateOut
    $gateExit = $LASTEXITCODE
    $g = $null
    try { $g = Get-Content $GateOut -Raw | ConvertFrom-Json } catch { }
    $decision = if ($g) { $g.decision } else { "UNKNOWN" }
    $ck = if ($g) { $g.check_count } else { 0 }
    $crit = if ($g) { $g.critical_count } else { 0 }
    $warn = if ($g) { $g.warning_count } else { 0 }
    $degr = if ($g) { $g.degraded_count } else { 0 }
    switch ($gateExit) {
        0 { Write-Success "Forensic deploy gate: ALLOW - $decision ($ck checks, 0 critical, $warn warnings)." }
        2 {
            Write-Warn "Forensic deploy gate: REVIEW REQUIRED - $decision ($ck checks, $crit critical, $warn warnings, $degr degraded)."
            Write-Info "Evidence: $GateOut"
        }
        3 { Write-Fail "forensic-gate" "Forensic engine UNAVAILABLE - deployment cannot be verified (fail-safe block). Evidence: $GateOut" $gateExit }
        default { Write-Fail "forensic-gate" "Forensic deploy gate exit $gateExit ($decision). Evidence: $GateOut" $gateExit }
    }
} else {
    Write-Warn "-SkipGate set - forensic deploy gate skipped."
}

# =============================================================================
# PHASE 7 - FINAL REPORT + PUSH FLOW
# =============================================================================
Write-Step 7 7 "Final report & optional push"
$Stopped.Stop()
$elapsed = "{0:mm\:ss}" -f $Stopped.Elapsed

Write-Host ""
Write-Host ("=" * 64) -ForegroundColor Green
Write-Host "  ALL CHECKS PASSED - CODE IS CI/CD READY" -ForegroundColor Green
Write-Host ("=" * 64) -ForegroundColor Green

$rows = @(
    [pscustomobject]@{ Check = "Git preflight";  Status = if ($DirtyCount -eq 0) { "CLEAN" } else { "DIRTY" };    Detail = "$DirtyCount dirty entries" },
    [pscustomobject]@{ Check = "Ruff lint";      Status = if ($ruffLintRc -eq 0) { "PASSED" } else { "FAILED" };  Detail = "see ci-results/ruff/" },
    [pscustomobject]@{ Check = "Ruff format";    Status = if ($ruffFmtRc -eq 0) { "PASSED" } else { "FAILED" };  Detail = "see ci-results/format/" },
    [pscustomobject]@{ Check = "Mypy";           Status = if ($mypyExit -eq 0) { "PASSED" } else { "FAILED" };   Detail = "see ci-results/mypy/" },
    [pscustomobject]@{ Check = "Pytest";         Status = if ($pytestExit -eq 0) { "PASSED" } else { "FAILED" }; Detail = $pytestDetail },
    [pscustomobject]@{ Check = "Coverage";       Status = $covStatus.ToUpper(); Detail = $covDisplay },
    [pscustomobject]@{ Check = "CI tree";        Status = "BUILT"; Detail = "ci-results/run-info/summary.md" }
)
if (-not $SkipGate) {
    $rows += [pscustomobject]@{ Check = "Forensic gate"; Status = $decision; Detail = "$ck checks | $crit critical | $warn warnings" }
}
foreach ($r in $rows) {
    $mark = if ($r.Status -in @("PASSED","CLEAN","BUILT","ALLOW")) { "[OK] " } else { "[!!] " }
    $color = if ($r.Status -in @("PASSED","CLEAN","BUILT","ALLOW")) { "Green" } else { "Yellow" }
    Write-Host ("  {0} {1,-16} {2,-14} {3}" -f $mark, $r.Check, $r.Status, $r.Detail) -ForegroundColor $color
}
Write-Host ""
Write-Info "Elapsed: $elapsed | Logs: $RunDir | CI tree: $CiRoot"

if (-not $SkipPush) {
    $pushChoice = Read-Host "`nStage, commit and push now? (y/n)"
    if ($pushChoice -match '^[yY]$') {
        $commitMsg = Read-Host "Commit message"
        if ([string]::IsNullOrWhiteSpace($commitMsg)) {
            $commitMsg = "chore: pre-push verified quality update"
            Write-Info "Using default commit message: $commitMsg"
        }
        Write-Cmd "git add -A"
        & git add -A --ignore-errors 2>&1 | ForEach-Object { Write-Info $_ }
        if ($LASTEXITCODE -ne 0) { Write-Fail "git-add" "git add failed." $LASTEXITCODE }
        $staged = @(& git diff --cached --name-only 2>$null)
        if ($staged.Count -eq 0) {
            Write-Warn "Nothing staged - nothing to commit (all changes already committed?)."
        } else {
            Write-Info "Staging $($staged.Count) file(s)..."
            Write-Cmd "git commit -m `"$commitMsg`""
            & git commit -m $commitMsg 2>&1 | ForEach-Object { Write-Info $_ }
            if ($LASTEXITCODE -ne 0) { Write-Fail "git-commit" "git commit failed." $LASTEXITCODE }
            Write-Cmd "git push"
            & git push 2>&1 | ForEach-Object { Write-Info $_ }
            if ($LASTEXITCODE -ne 0) {
                Write-Fail "git-push" "git push failed - resolve the error above then push manually." $LASTEXITCODE
            }
            $newHead = & git log -1 --oneline 2>$null | Select-Object -First 1
            if ($newHead) { $newHead = $newHead.Trim() }
            Write-Success "Pushed successfully. HEAD: $newHead"
        }
    } else {
        Write-Host "  Changes verified locally. Push manually when ready." -ForegroundColor Yellow
    }
} else {
    Write-Host "  -SkipPush set - no push performed. Run './beforePush.ps1' to stage/commit/push." -ForegroundColor Yellow
}

Write-Banner "Gate finished in $elapsed - happy shipping!"
exit 0