<#
.SYNOPSIS
    Nexus Scalp Engine (NSE) - Pre-Push Quality & CI Verification Script
.DESCRIPTION
    Runs Ruff Lint/Format, Mypy static analysis, and Pytest suites before pushing to GitHub.
#>

$ErrorActionPreference = "Stop"

function Write-Step {
    param([string]$Message)
    Write-Host "`n========================================================" -ForegroundColor Cyan
    Write-Host " 🚀 $Message" -ForegroundColor Cyan
    Write-Host "========================================================" -ForegroundColor Cyan
}

function Write-Success {
    param([string]$Message)
    Write-Host " ✔ $Message" -ForegroundColor Green
}

function Write-Failure {
    param([string]$Message)
    Write-Host "`n ❌ ERROR: $Message" -ForegroundColor Red
    Write-Host " ⛔ Push aborted to protect CI/CD pipeline.`n" -ForegroundColor Red
    exit 1
}

# -----------------------------------------------------------------------------
# 1. RUFF LINT & AUTO-FIX
# -----------------------------------------------------------------------------
Write-Step "1/4: Running Ruff Lint (with auto-fix)..."
try {
    ruff check . --fix --unsafe-fixes
    Write-Success "Ruff Lint checks passed!"
} catch {
    Write-Failure "Ruff Lint found unfixable errors. Fix them manually before pushing."
}

# -----------------------------------------------------------------------------
# 2. RUFF FORMATTING
# -----------------------------------------------------------------------------
Write-Step "2/4: Running Ruff Format..."
try {
    ruff format .
    ruff format --check .
    Write-Success "Code formatted cleanly according to PEP 8 / Ruff style!"
} catch {
    Write-Failure "Ruff formatting check failed."
}

# -----------------------------------------------------------------------------
# 3. MYPY TYPE CHECKING
# -----------------------------------------------------------------------------
Write-Step "3/4: Running Mypy Type Checker on 'src'..."
try {
    mypy src
    Write-Success "Mypy static type verification passed with 0 errors!"
} catch {
    Write-Failure "Mypy type checking failed. Fix type mismatches before pushing."
}

# -----------------------------------------------------------------------------
# 4. PYTEST UNIT & INTEGRATION TESTS
# -----------------------------------------------------------------------------
Write-Step "4/4: Running Unit Tests & Generating Coverage..."
try {
    pytest tests/unit/ -q --tb=short
    Write-Success "All tests passed successfully!"
} catch {
    Write-Failure "One or more pytest test cases failed."
}

# -----------------------------------------------------------------------------
# ALL CHECKS PASSED
# -----------------------------------------------------------------------------
Write-Host "`n========================================================" -ForegroundColor Green
Write-Host " 🎉 ALL CHECKS PASSED! YOUR CODE IS 100% CI/CD READY." -ForegroundColor Green
Write-Host "========================================================`n" -ForegroundColor Green

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