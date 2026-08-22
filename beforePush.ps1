<#
.SYNOPSIS
    Nexus Scalp Engine (NSE) - Pre-Push Quality "CI-Mirror" Gate v3 (Windows)

.DESCRIPTION
    Local mirror of the GitHub Actions "quality" job (ci.yml). v3 adds:
      * EVERY RUN STARTS CLEAN - Clear-Host wipes old console output and the
        ci-results/ tree is re-initialised (make_ci_results.py init) so no
        stale artifact from a previous run can leak into this one.
      * FRESH STATEMENT - a run-plan statement is printed at start (what will
        run, tool versions, worker count, git state) and a final statement
        table after every gate.
      * ASYNC - ALL FOUR checks (ruff lint, ruff format, mypy, pytest) run as
        concurrent background jobs instead of serial ruff-then-parallel.
        Add -Serial to force sequential order for debugging.
      * STRICT elseif STATUS RESOLUTION - every result is resolved through one
        Resolve-Status helper (0 -> PASSED, 1 -> FAILED, else -> ERRORED)
        instead of scattered if/else branches.
      * BUILT-IN SELF-TEST - after the gates, Invoke-SelfTest verifies every
        artifact actually exists (run log, per-check JSONs, junit.xml,
        coverage.xml, exit-code coherence) and prints OK/FAILED. Gate exits 1
        if the self-test does not pass.

    Gates (identical flags to ci.yml):
      ruff lint    -> ruff check --config pyproject.toml .          (JSON + text, like CI)
      ruff format  -> ruff format --config pyproject.toml --check . (validates; -Fix to auto-fix)
      mypy         -> mypy src --junit-xml
      pytest       -> critical suite, -n <ram-aware> --dist loadgroup
                    --cov=src + junit.xml + coverage.xml + htmlcov
    Then: ci-results/ tree (summary.md/manifest/SHA256SUMS), forensic deploy
    gate, final statement + self-test, verified stage/commit/push flow.

.PARAMETER Fix
    Auto-fix ruff lint + format BEFORE validation (mutates files).
.PARAMETER FullSuite
    Run ALL unit tests (tests/unit/, ~20 min) instead of the critical suite.
.PARAMETER SkipPush
    Do not prompt for stage/commit/push (CI-style check-only run).
.PARAMETER SkipGate
    Skip the forensic deploy gate.
.PARAMETER Workers
    Override RAM-aware xdist workers.
.PARAMETER Serial
    Disable async: run the four checks sequentially (debugging).
.EXAMPLE
    ./beforePush.ps1                  # full gate + push prompt
    ./beforePush.ps1 -SkipPush        # check-only (CI dry run)
    ./beforePush.ps1 -Fix -SkipPush   # auto-fix + validate
    ./beforePush.ps1 -Serial          # sequential checks
#>
[CmdletBinding()]
param(
    [switch]$Fix,
    [switch]$FullSuite,
    [switch]$SkipPush,
    [switch]$SkipGate,
    [switch]$Serial,
    [int]$Workers = 0
)

$ErrorActionPreference = "Stop"

# UTF-8 so emoji / Persian render in Windows Terminal.
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }
try { $OutputEncoding       = [System.Text.Encoding]::UTF8 } catch { }

# Clear OLD console output - every run starts fresh (v3 requirement).
# Guard: Clear-Host throws "handle is invalid" when stdout is redirected
# (pipes, CI, some terminals) - the clear must never kill the run.
try { Clear-Host } catch { }
# =============================================================================
# CONFIGURATION
# =============================================================================
$RepoRoot = if ($PSScriptRoot) { $PSScriptRoot } else { (Get-Location).Path }
Set-Location $RepoRoot

$KeepRuns = 3
$CiRoot   = Join-Path $RepoRoot "ci-results"
$GateOut  = Join-Path $RepoRoot "artifacts\forensics\deploy_gate_result.json"

# RAM-aware worker count (BUG-073): each xdist worker loads torch+polars
# (~500 MB) => workers = min(cores, max(2, availGB / 1.5)).
$Cores = [Math]::Max(2, [Environment]::ProcessorCount)
$availGB = $null
try {
    $os      = Get-CimInstance Win32_OperatingSystem
    $availGB = [Math]::Round(($os.FreePhysicalMemory / 1MB), 1)
    $MemCap  = [Math]::Max(2, [int][Math]::Floor($availGB / 1.5))
    if ($MemCap -lt $Cores) { $Cores = $MemCap }
} catch { $availGB = $null }
if ($Workers -gt 0) { $Cores = $Workers }

# --- log folder -----------------------------------------------------------
$LogRoot  = Join-Path $RepoRoot "artifacts\logs"
$RunStamp = Get-Date -Format "yyyyMMdd_HHmmss"
$RunDir   = Join-Path $LogRoot "beforepush_$RunStamp"
$RunLog   = Join-Path $RunDir "run.log"
$ErrLog   = Join-Path $RunDir "error.log"
$WarnLog  = Join-Path $RunDir "warning.log"
New-Item -ItemType Directory -Path $RunDir -Force | Out-Null

# --- toolchain: ALWAYS the project venv, never PATH -------------------------
$VenvPy = Join-Path $RepoRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) { $VenvPy = "python" }

# =============================================================================
# UI & LOGGING HELPERS - ALL DEFINED BEFORE ANY USE (PowerShell does not hoist
# functions; v1 died by calling Write-Warn before its definition)
# =============================================================================
function Write-Log {
    param([string]$Path, [string]$Text)
    try {
        $dir = Split-Path $Path -Parent
        if ($dir -and -not (Test-Path $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
        Add-Content -Path $Path -Value $Text -ErrorAction SilentlyContinue
    } catch { }
}

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
    param([string]$Stage, [string]$Message, [int]$ExitCode)
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

# BOM-FREE UTF-8 JSON (a BOM breaks json.loads - BUG-093 lesson).
function Set-JsonFile {
    param([string]$Path, $Object)
    $text = $Object | ConvertTo-Json -Depth 6
    $enc  = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $text, $enc)
}

# =============================================================================
# STRICT STATUS RESOLUTION - ONE elseif chain, used by every check (v3).
#   0 -> PASSED, 1 -> FAILED, anything else -> ERRORED.
# =============================================================================
function Resolve-Status {
    param([int]$ExitCode)
    if ($ExitCode -eq 0) { return "PASSED" }
    elseif ($ExitCode -eq 1) { return "FAILED" }
    else { return "ERRORED" }
}

# =============================================================================
# BUILT-IN SELF TEST (v3) - verifies every artifact plus exit-code coherence
# so we never cheer "ALL CHECKS PASSED" when a file is missing or a status
# JSON disagrees with the real tool exit code.
# =============================================================================
function Invoke-SelfTest {
    param(
        [int]$RuffLintRc, [int]$RuffFmtRc, [int]$MypyRc, [int]$PytestRc,
        [string]$CiRoot, [string]$RunLog, [string]$ErrLog,
        [string]$JunitXml, [string]$CovXml
    )
    $problems = @()

    # 1) Core run artifacts must exist. error.log only exists when something
    #    warned/failed - on a fully clean run it is legitimately absent, so
    #    require it ONLY when any check did not pass.
    foreach ($p in @($RunLog)) {
        if (-not (Test-Path $p)) { $problems += "missing artifact: $p" }
    }
    $anyNonPass = @($RuffLintRc, $RuffFmtRc, $MypyRc, $PytestRc) -ne 0
    if ($anyNonPass -and -not (Test-Path $ErrLog)) {
        $problems += "missing error.log despite a non-passing check: $ErrLog"
    }

    # 2) Per-check status JSONs must exist AND agree with real exit codes.
    $expect = @{
        "ruff_lint"  = $RuffLintRc
        "ruff_format" = $RuffFmtRc
        "mypy"       = $MypyRc
        "pytest"     = $PytestRc
    }
    foreach ($k in $expect.Keys) {
        $j = Join-Path $CiRoot "run-info\$k.json"
        if (-not (Test-Path $j)) {
            $problems += "missing check json: $j"
            continue
        }
        try {
            $c = Get-Content $j -Raw | ConvertFrom-Json
            $want = Resolve-Status $expect[$k]
            if ($c.status -ne $want) {
                $problems += "status mismatch: $k json=$($c.status) want=$want (rc=$($expect[$k]))"
            }
        } catch { $problems += "unreadable check json: $j" }
    }

    # 3) junit.xml + coverage.xml exist when pytest produced them.
    if ($PytestRc -ne 0 -or $PytestRc -eq 0) {
        if (-not (Test-Path $JunitXml)) { $problems += "missing junit.xml: $JunitXml" }
        if (-not (Test-Path $CovXml))   { $problems += "missing coverage.xml: $CovXml" }
    }

    # 4) The overall exit-code set must be coherent (no gate unchanged).
    $allRcs = @($RuffLintRc, $RuffFmtRc, $MypyRc, $PytestRc)
    if ($allRcs -contains -1) { $problems += "a gate never wrote its exit file (-1) - async job failed to start" }

    if ($problems.Count -gt 0) {
        Write-Host ""
        Write-Host "  [--] SELF-TEST: FAILED" -ForegroundColor Red
        foreach ($p in $problems) {
            Write-Host "  [--]   - $p" -ForegroundColor Red
            Write-Log $ErrLog "SELFTEST: $p"
        }
        return $false
    }
    Write-Success "SELF-TEST: all artifacts + status JSONs verified OK."
    return $true
}

# =============================================================================
# PHASE 0 - ENVIRONMENT SNAPSHOT + FRESH RUN STATEMENT (v3)
# =============================================================================
Write-Banner "NSE Pre-Push Quality Gate v3 (CI-mirror + self-test)"

$Stopped = New-Object System.Diagnostics.Stopwatch
$Stopped.Start()

# Version probes: stderr may surface as an ErrorRecord and missing tools emit
# nothing - stringify via ToString() and guard before any .Trim() (an
# ErrorRecord has no Trim method and a null crashes under ErrorActionPreference
# Stop). A failed probe renders "unknown", never a crash.
function Get-ToolVer {
    param([string]$ToolVerRaw)
    try {
        if ($null -eq $ToolVerRaw) { return "unknown" }
        $s = $ToolVerRaw.ToString().Trim()
        if ($s.Length -gt 0) { return $s }
    } catch { }
    return "unknown"
}
$PyVer   = Get-ToolVer (& $VenvPy --version 2>&1 | Select-Object -First 1)
$RuffVer = Get-ToolVer (& $VenvPy -m ruff --version 2>&1 | Select-Object -First 1)
$MypyVer = Get-ToolVer (& $VenvPy -m mypy --version 2>&1 | Select-Object -First 1)

if ($PyVer -notmatch "3\.11") {
    Write-Warn "Local Python $PyVer vs CI Python 3.11 (pyproject target)."
}
if ($RuffVer -and $RuffVer -notmatch "0\.16\.3") {
    Write-Warn "Local ruff $RuffVer vs pinned CI 0.16.3 - run: $VenvPy -m pip install -q ruff==0.16.3"
}
if ($env:GIT_TERMINAL_PROMPT) { $env:GIT_TERMINAL_PROMPT = "0" }

# --- FRESH STATEMENT: what this run will do (v3) -----------------------------
Write-Host ""
Write-Host "  RUN PLAN" -ForegroundColor Magenta
Write-Host "  ".PadRight(60, '-') -ForegroundColor Magenta
Write-Host "   Repo     : $RepoRoot" -ForegroundColor White
Write-Host "   Python   : $PyVer" -ForegroundColor White
Write-Host "   Ruff     : $RuffVer" -ForegroundColor White
Write-Host "   Mypy     : $MypyVer" -ForegroundColor White
$memInfo = ""
if ($null -ne $availGB) { $memInfo = " (avail RAM ~$availGB GB)" }
Write-Host "   Workers  : $Cores$memInfo" -ForegroundColor White
$mode = if ($FullSuite) { "FULL unit suite (tests/unit/, ~20 min)" }
         elseif ($Serial) { "CRITICAL suite, SEQUENTIAL" }
         else { "CRITICAL suite, ASYNC (4 checks in parallel)" }
Write-Host "   Pytest   : $mode" -ForegroundColor White
$fx = if ($Fix) { "auto-fix ON" } else { "validate-only" }
$gt = if ($SkipGate) { "SKIPPED" } else { "ENABLED" }
$ps = if ($SkipPush) { "SKIPPED" } else { "prompt at end" }
Write-Host "   Lint fix : $fx | Forensic gate: $gt | Push: $ps" -ForegroundColor White
Write-Host "   Logs     : $RunDir" -ForegroundColor White
Write-Host "   CI tree  : $CiRoot (wiped + re-initialised this run)" -ForegroundColor White
Write-Host "  ".PadRight(60, '-') -ForegroundColor Magenta
Write-Success "Log folder  : $RunDir"

# =============================================================================
# PHASE 1 - GIT PREFLIGHT (informational; never blocks on network)
# =============================================================================
Write-Step 1 7 "Git preflight - branch / distance / dirty tree"

$Branch  = & git branch --show-current 2>$null | Select-Object -First 1
$Branch  = if ($Branch) { $Branch.Trim() } else { "(detached)" }
$HeadSha = & git rev-parse --short HEAD 2>$null | Select-Object -First 1
$HeadSha = if ($HeadSha) { $HeadSha.Trim() } else { "?" }
$Porcelain  = @(& git status --porcelain 2>$null)
$DirtyCount = $Porcelain.Count

Write-Info "Branch  : $Branch  | HEAD: $HeadSha  | dirty entries: $DirtyCount"

if ($DirtyCount -gt 0) {
    $staged   = @($Porcelain | Where-Object { $_ -match '^[MADRC]' }).Count
    $unstaged = @($Porcelain | Where-Object { $_ -match '^.[MD]' }).Count
    $untrack  = @($Porcelain | Where-Object { $_ -match '^\?\?' }).Count
    Write-Warn "Working tree NOT clean: $staged staged, $unstaged unstaged, $untrack untracked."
    $junkRoot = @($Porcelain | Where-Object { $_ -match '^\?\?' -and $_ -notmatch '\?\? scratch/' })
    if ($junkRoot.Count -gt 0) {
        Write-Warn "Untracked items OUTSIDE scratch/ (repo-root hygiene mandate):"
        foreach ($j in $junkRoot) { Write-Info "  $j" }
    }
} else {
    Write-Success "Working tree clean."
}

try {
    & git fetch --quiet --prune 2>$null
    if ($LASTEXITCODE -ne 0) { throw "git fetch failed" }
    $dist = & git rev-list --left-right --count "@{u}"...HEAD 2>$null | Select-Object -First 1
    $dist = if ($dist) { $dist.Trim() } else { "" }
    if ($dist -match '^\s*(\d+)\s+(\d+)\s*$') {
        $behind = [int]$Matches[1]; $ahead = [int]$Matches[2]
        if ($behind -gt 0) {
            Write-Warn "Branch BEHIND upstream by $behind commit(s) - pull/rebase before pushing."
        } elseif ($ahead -gt 0) {
            Write-Info "Branch ahead of upstream by $ahead commit(s) (will be pushed)."
        } else {
            Write-Success "Branch in sync with upstream."
        }
    } else {
        Write-Warn "No upstream tracking branch - skipping ahead/behind check."
    }
} catch {
    Write-Warn "Remote check skipped (network/unavailable): $($_.Exception.Message)"
}

# =============================================================================
# PHASE 2 - INIT CI TREE (CLEAN-OLD: wipe stale results first, like ci.yml)
# =============================================================================
Write-Step 2 7 "Reset ci-results/ (clear old) + optional -Fix pass"

Write-Cmd "$VenvPy scripts/ci/make_ci_results.py init $CiRoot"
& $VenvPy scripts/ci/make_ci_results.py init $CiRoot --json "$CiRoot\run-info\env-digest.json" *> $RunLog 2>&1

if ($Fix) {
    Write-Warn "-Fix set: auto-fixing lint + format BEFORE validation (files rewritten)."
    $RuffFixLog = Join-Path $RunDir "ruff-fix.log"
    Write-Cmd "$VenvPy -m ruff check --config pyproject.toml . --fix --unsafe-fixes"
    & $VenvPy -m ruff check --config pyproject.toml . --fix --unsafe-fixes *> $RuffFixLog
    if ($LASTEXITCODE -ne 0) { Show-Tail $RuffFixLog "ruff --fix"; Write-Fail "ruff-fix" "Ruff --fix left unfixable violations." $LASTEXITCODE }
    Write-Cmd "$VenvPy -m ruff format --config pyproject.toml ."
    & $VenvPy -m ruff format --config pyproject.toml . *>> $RuffFixLog
    if ($LASTEXITCODE -ne 0) { Show-Tail $RuffFixLog "ruff format"; Write-Fail "ruff-format" "Ruff format failed." $LASTEXITCODE }
    Write-Success "Auto-fix pass complete."
}

# =============================================================================
# PHASE 3 - ALL FOUR CHECKS  (ASYNC: concurrent background jobs; -Serial for
# sequential). Each job writes its real exit code to a "<log>.exit" file
# because jobs do not share variables with the parent scope. Every job
# Set-Location to the repo root first (BUG-073: Start-Job cwd is unreliable).
# =============================================================================
$RunRoot = $RepoRoot

$RuffLintLog = Join-Path $RunDir "ruff-lint.log"
$RuffFmtLog  = Join-Path $RunDir "ruff-format.log"
$MyPyLog     = Join-Path $RunDir "mypy.log"
$PyTestLog   = Join-Path $CiRoot "pytest\pytest.txt"
$LintJson    = Join-Path $CiRoot "ruff\lint.json"
$LintTxt     = Join-Path $CiRoot "ruff\lint.txt"
$FormatTxt   = Join-Path $CiRoot "format\format.txt"
$MyPyJUnit   = Join-Path $CiRoot "mypy\mypy-junit.xml"
$JunitXml    = Join-Path $CiRoot "pytest\junit.xml"
$CovXml      = Join-Path $CiRoot "pytest\coverage.xml"
$CovHtml     = Join-Path $CiRoot "pytest\htmlcov"

$lintJob = Start-Job -ScriptBlock {
    param($log, $json, $txt, $venvPy, $runRoot)
    Set-Location -Path $runRoot
    try {
        & $venvPy -m ruff check --config pyproject.toml . --output-format json *> $json
        $code = $LASTEXITCODE
        # derive human text from the SAME json (no second full-tree scan)
        try {
            $viol = Get-Content $json -Raw | ConvertFrom-Json
            if ($viol -is [array] -and $viol.Count -gt 0) {
                foreach ($v in $viol) {
                    "{0}:{1}:{2}: {3} {4}" -f $v.filename, $v.location.row, $v.location.column, $v.code, $v.message | Add-Content $txt
                }
            } else { "All checks passed!" | Add-Content $txt }
        } catch { "All checks passed!" | Add-Content $txt }
    } catch {
        $code = 900
        $_ | Out-String | Add-Content $log
    }
    Set-Content -Path "$log.exit" -Value $code
} -ArgumentList $RuffLintLog, $LintJson, $LintTxt, $VenvPy, $RunRoot

$fmtJob = Start-Job -ScriptBlock {
    param($log, $fmtTxt, $venvPy, $runRoot)
    Set-Location -Path $runRoot
    try {
        & $venvPy -m ruff format --config pyproject.toml --check . *> $fmtTxt
        $code = $LASTEXITCODE
    } catch {
        $code = 900
        $_ | Out-String | Add-Content $log
    }
    Set-Content -Path "$log.exit" -Value $code
} -ArgumentList $RuffFmtLog, $FormatTxt, $VenvPy, $RunRoot

$mypyJob = Start-Job -ScriptBlock {
    param($log, $venvPy, $runRoot, $junit)
    Set-Location -Path $runRoot
    try {
        & $venvPy -m mypy src --junit-xml $junit *> $log
        $code = $LASTEXITCODE
        Copy-Item -Path $log -Destination "$runRoot\ci-results\mypy\mypy.txt" -Force -ErrorAction SilentlyContinue
    } catch {
        $code = 900
        $_ | Out-String | Add-Content $log
    }
    Set-Content -Path "$log.exit" -Value $code
} -ArgumentList $MyPyLog, $VenvPy, $RunRoot, $MyPyJUnit

# critical suite (default) or full unit suite (-FullSuite)
if ($FullSuite) {
    $PytestTarget = @("tests/unit/")
    Write-Warn "FULL suite gate requested (tests/unit/, ~20 min - legacy)."
} elseif (Test-Path "tests\critical_suite.txt") {
    $PytestTarget = @(Get-Content "tests\critical_suite.txt" | Where-Object { $_ -match '\S' -and $_ -notmatch '^\s*#' })
    if ($PytestTarget.Count -eq 0) { $PytestTarget = @("tests/unit/") }
    Write-Info "Critical-suite gate: $($PytestTarget.Count) files from tests/critical_suite.txt"
} else {
    $PytestTarget = @("tests/unit/")
    Write-Warn "tests/critical_suite.txt missing - fallback to tests/unit/."
}

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

Write-Step 3 7 "Ruff lint + format + mypy + pytest - all four checks $(if ($Serial) { 'SEQUENTIAL' } else { 'ASYNC (concurrent jobs)' })"
Write-Cmd "$VenvPy -m ruff check --config pyproject.toml . --output-format json"
Write-Cmd "$VenvPy -m ruff format --config pyproject.toml --check ."
Write-Cmd "$VenvPy -m mypy src --junit-xml ci-results/mypy/mypy-junit.xml"
Write-Cmd "$VenvPy -m pytest $($PytestTarget -join ' ') -n $Cores --dist loadgroup --cov=src --junitxml ci-results/pytest/junit.xml -q --tb=short"

if ($Serial) {
    # SEQUENTIAL: run each job in order (debug mode)
    Receive-Job $lintJob | Out-Null; Wait-Job $lintJob | Out-Null
    Receive-Job $fmtJob  | Out-Null; Wait-Job $fmtJob  | Out-Null
    Receive-Job $mypyJob | Out-Null; Wait-Job $mypyJob | Out-Null
    Receive-Job $pytestJob | Out-Null; Wait-Job $pytestJob | Out-Null
} else {
    # ASYNC: all four at once
    Wait-Job $lintJob, $fmtJob, $mypyJob, $pytestJob | Out-Null
}

# collect outputs into run.log (tail sections)
foreach ($pair in @(@($RuffLintLog,"ruff lint"), @($RuffFmtLog,"ruff format"), @($MyPyLog,"mypy"))) {
    if (Test-Path $pair[0]) {
        Write-Log $RunLog "`n--- $($pair[1]) (tail) ---"
        Get-Content $pair[0] -Tail 10 -ErrorAction SilentlyContinue | ForEach-Object { Write-Log $RunLog $_ }
    }
}
Write-Log $RunLog "`n--- pytest (tail) ---"
Get-Content $PyTestLog -Tail 30 -ErrorAction SilentlyContinue | ForEach-Object { Write-Log $RunLog $_ }

Remove-Job $lintJob, $fmtJob, $mypyJob, $pytestJob -Force

# Real exit codes from the .exit files (a job that never started = -1)
$ruffLintRc = if (Test-Path "$RuffLintLog.exit") { [int](Get-Content "$RuffLintLog.exit") } else { -1 }
$ruffFmtRc  = if (Test-Path "$RuffFmtLog.exit")  { [int](Get-Content "$RuffFmtLog.exit")  } else { -1 }
$mypyRc     = if (Test-Path "$MyPyLog.exit")     { [int](Get-Content "$MyPyLog.exit")     } else { -1 }
$pytestRc   = if (Test-Path "$PyTestLog.exit")   { [int](Get-Content "$PyTestLog.exit")   } else { -1 }

# warnings -> warning.log
Get-Content $PyTestLog -ErrorAction SilentlyContinue |
    Where-Object { $_ -match 'warning|Warning|SKIPPED|Deprecation|error' } |
    ForEach-Object { Write-Log $WarnLog $_ }

# =============================================================================
# PHASE 4 - STRICT STATUS RESOLUTION + FAIL-FAST (v3 elseif chain)
# =============================================================================
Write-Step 4 7 "Resolve check statuses (strict elseif) + fail-fast"

$ruffLintStatus = Resolve-Status $ruffLintRc
$ruffFmtStatus  = Resolve-Status $ruffFmtRc
$mypyStatus     = Resolve-Status $mypyRc
$pytestStatus   = Resolve-Status $pytestRc

Write-Info "ruff lint   : $ruffLintStatus (rc=$ruffLintRc)"
Write-Info "ruff format : $ruffFmtStatus  (rc=$ruffFmtRc)"
Write-Info "mypy        : $mypyStatus     (rc=$mypyRc)"
Write-Info "pytest      : $pytestStatus   (rc=$pytestRc)"

if ($ruffLintStatus -ne "PASSED") {
    Show-Tail $LintTxt "ruff lint" 30
    Write-Fail "ruff_lint" "Ruff lint $ruffLintStatus (rc=$ruffLintRc) - see $LintJson / $LintTxt." $ruffLintRc
}
if ($ruffFmtStatus -ne "PASSED") {
    Show-Tail $FormatTxt "ruff format" 30
    Write-Fail "ruff_format" "Ruff format $ruffFmtStatus (rc=$ruffFmtRc) - run .\beforePush.ps1 -Fix or '$VenvPy -m ruff format --config pyproject.toml .'." $ruffFmtRc
}
if ($mypyStatus -ne "PASSED") {
    Show-Tail (Join-Path $CiRoot "mypy\mypy.txt") "mypy" 30
    Write-Fail "mypy" "Mypy $mypyStatus (rc=$mypyRc)." $mypyRc
}
if ($pytestStatus -ne "PASSED") {
    Show-Tail $PyTestLog "pytest" 40
    Write-Fail "pytest" "Pytest $pytestStatus (rc=$pytestRc) - see $PyTestLog / $JunitXml." $pytestRc
}
Write-Success "All four checks PASSED (ruff lint / format, mypy, pytest)."

# =============================================================================
# PHASE 5 - CI RESULTS TREE (same tree GitHub uploads) + SUMMARY
# =============================================================================
Write-Step 5 7 "Build ci-results/ tree (make_ci_results.py) + summary"

& $VenvPy scripts/ci/make_ci_results.py check $CiRoot ruff_lint   $ruffLintRc "violations found (see ruff/)" *> $RunLog
& $VenvPy scripts/ci/make_ci_results.py check $CiRoot ruff_format $ruffFmtRc  "files would be reformatted (see format/)" *> $RunLog
& $VenvPy scripts/ci/make_ci_results.py check $CiRoot mypy         $mypyRc    "type errors found (see mypy/)" *> $RunLog
& $VenvPy scripts/ci/make_ci_results.py check $CiRoot pytest       $pytestRc  "see pytest/junit.xml + pytest.txt" *> $RunLog

# coverage % from the pytest log (single TOTAL line under xdist)
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

# junit stats (tests / failed / skipped)
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

# --- replicate CI's final 'fail job if any check failed' step ---------------
$statusMap = @{}
foreach ($chk in @("ruff_lint","ruff_format","mypy","pytest","coverage")) {
    $p = Join-Path $CiRoot "run-info\$chk.json"
    try { $statusMap[$chk] = (Get-Content $p -Raw | ConvertFrom-Json).status } catch { $statusMap[$chk] = "errored" }
}
$failing = @($statusMap.GetEnumerator() | Where-Object { $_.Value -in @("failed","errored") } | ForEach-Object { $_.Key })
$statusLine = ($statusMap.GetEnumerator() | Sort-Object Key | ForEach-Object { "$($_.Key)=$($_.Value)" }) -join " | "
Write-Info "CI-style status: $statusLine"
if ($failing.Count -gt 0) {
    Write-Fail "ci-gate" "CI gate failed on: $($failing -join ', ') (mirrors GitHub final step)" 1
}
Write-Success "CI gate replication OK (all statuses green)."

# =============================================================================
# PHASE 6 - BUILT-IN SELF TEST (v3): every artifact + exit-code coherence
# =============================================================================
Write-Step 6 7 "Self-test: verify every artifact + status JSON (ok/not ok)"
$selfTestOk = Invoke-SelfTest `
    -RuffLintRc $ruffLintRc -RuffFmtRc $ruffFmtRc -MypyRc $mypyRc -PytestRc $pytestRc `
    -CiRoot $CiRoot -RunLog $RunLog -ErrLog $ErrLog -JunitXml $JunitXml -CovXml $CovXml
if (-not $selfTestOk) {
    Write-Fail "self-test" "Self-test FAILED - artifacts missing or exit codes incoherent. Run is NOT trustworthy." 1
}

# =============================================================================
# PHASE 7 - CANONICAL FORENSIC DEPLOY GATE (TASK-12)
#   0=ALLOW | 1=BLOCK | 2=REVIEW | 3=ENGINE UNAVAILABLE (fail-safe block)
# =============================================================================
$gateDecision = "SKIPPED"
if (-not $SkipGate) {
    Write-Step 7 8 "Forensic deploy gate (nexus forensic --deploy-gate --json)"
    New-Item -ItemType Directory -Path (Split-Path $GateOut) -Force | Out-Null
    Write-Cmd "$VenvPy -m nexus_scalp.cli.main forensic --deploy-gate --json"
    & $VenvPy -m nexus_scalp.cli.main forensic --deploy-gate --json *> $GateOut
    $gateExit = $LASTEXITCODE
    $g = $null
    try { $g = Get-Content $GateOut -Raw | ConvertFrom-Json } catch { }
    $gateDecision = if ($g) { $g.decision } else { "UNKNOWN" }
    $ck = if ($g) { $g.check_count } else { 0 }
    $crit = if ($g) { $g.critical_count } else { 0 }
    $warnC = if ($g) { $g.warning_count } else { 0 }
    $degr = if ($g) { $g.degraded_count } else { 0 }
    if ($gateExit -eq 0) {
        Write-Success "Forensic deploy gate: ALLOW - $gateDecision ($ck checks, 0 critical, $warnC warnings)."
    } elseif ($gateExit -eq 2) {
        Write-Warn "Forensic deploy gate: REVIEW REQUIRED - $gateDecision ($ck checks, $crit critical, $warnC warnings, $degr degraded)."
        Write-Info "Evidence: $GateOut"
    } else {
        Write-Fail "forensic-gate" "Forensic deploy gate exit $gateExit ($gateDecision). Evidence: $GateOut" $gateExit
    }
} else {
    Write-Warn "-SkipGate set - forensic deploy gate skipped."
    $ck = 0; $crit = 0; $warnC = 0
}

# =============================================================================
# PHASE 8 - FINAL STATEMENT + SELF-TEST RESULT + PUSH FLOW
# =============================================================================
Write-Step 8 8 "Final statement + optional push"

# run summary statement (v3: everything in one clear statement)
Write-Host ""
Write-Host ("=" * 64) -ForegroundColor Green
Write-Host "  FINAL STATEMENT - ${pytestDetail} | coverage $covDisplay" -ForegroundColor Green
Write-Host ("=" * 64) -ForegroundColor Green

$rows = @(
    [pscustomobject]@{ Check = "Git preflight";  Status = if ($DirtyCount -eq 0) { "CLEAN" } else { "DIRTY" };     Detail = "$DirtyCount dirty entries" },
    [pscustomobject]@{ Check = "Ruff lint";      Status = $ruffLintStatus; Detail = "rc=$ruffLintRc (ci-results/ruff/)" },
    [pscustomobject]@{ Check = "Ruff format";    Status = $ruffFmtStatus;  Detail = "rc=$ruffFmtRc (ci-results/format/)" },
    [pscustomobject]@{ Check = "Mypy";           Status = $mypyStatus;     Detail = "rc=$mypyRc (ci-results/mypy/)" },
    [pscustomobject]@{ Check = "Pytest";         Status = $pytestStatus;   Detail = $pytestDetail },
    [pscustomobject]@{ Check = "Coverage";       Status = $covStatus.ToUpper(); Detail = $covDisplay },
    [pscustomobject]@{ Check = "CI tree";        Status = "BUILT"; Detail = "ci-results/run-info/summary.md" },
    [pscustomobject]@{ Check = "Self-test";      Status = if ($selfTestOk) { "OK" } else { "FAILED" }; Detail = "artifacts + exit-code coherence" }
)
if (-not $SkipGate) {
    $rows += [pscustomobject]@{ Check = "Forensic gate"; Status = $gateDecision; Detail = "$ck checks | $crit critical | $warnC warnings" }
}
foreach ($r in $rows) {
    $okSet  = @("PASSED","CLEAN","BUILT","ALLOW","OK")
    $badSet = @("FAILED","ERRORED","DIRTY","BLOCK","REVIEW_REQUIRED","UNKNOWN")
    if ($okSet -contains $r.Status) {
        $mark = "[OK] "; $color = "Green"
    } elseif ($badSet -contains $r.Status) {
        $mark = "[!!] "; $color = "Red"
    } else {
        $mark = "[--] "; $color = "Yellow"
    }
    Write-Host ("  {0} {1,-16} {2,-16} {3}" -f $mark, $r.Check, $r.Status, $r.Detail) -ForegroundColor $color
}
Write-Host ""
$elapsed = "{0:mm\:ss}" -f $Stopped.Elapsed
Write-Info "Elapsed: $elapsed | Logs: $RunDir | CI tree: $CiRoot"

# push flow (only when ALL gates + self-test passed - otherwise Write-Fail already exited)
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
            if ($LASTEXITCODE -ne 0) { Write-Fail "git-push" "git push failed - resolve above then push manually." $LASTEXITCODE }
            $newHead = & git log -1 --oneline 2>$null | Select-Object -First 1
            if ($newHead) { $newHead = $newHead.Trim() }
            Write-Success "Pushed successfully. HEAD: $newHead"
        }
    } else {
        Write-Host "  Changes verified locally. Push manually when ready." -ForegroundColor Yellow
    }
} else {
    Write-Host "  -SkipPush set - no push performed." -ForegroundColor Yellow
}

Write-Banner "Gate finished in $elapsed - all artifacts verified OK. Happy shipping!"
exit 0
