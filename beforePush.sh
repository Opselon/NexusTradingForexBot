#!/usr/bin/env bash
# ==============================================================================
# Nexus Scalp Engine (NSE) - Pre-Push Quality & CI Verification Script (Linux/Bash)
# ==============================================================================
# Mirrors beforePush.ps1 quality gates for Linux environments.
# Runs Ruff Lint/Format, Mypy static analysis, and Pytest unit test suite.
# ==============================================================================

set -euo pipefail

CYAN='\033[0;36m'
GREEN='\033[0;32m'
RED='\033[0;31m'
NC='\033[0m' # No Color

write_step() {
    echo -e "\n========================================================"
    echo -e "${CYAN} 🚀 $1${NC}"
    echo -e "========================================================"
}

write_success() {
    echo -e "${GREEN} ✔ $1${NC}"
}

write_failure() {
    echo -e "\n${RED} ❌ ERROR: $1${NC}"
    echo -e "${RED} ⛔ Push aborted to protect CI/CD pipeline.${NC}\n"
    exit 1
}

# -----------------------------------------------------------------------------
# 1. RUFF LINT & AUTO-FIX
# -----------------------------------------------------------------------------
write_step "1/4: Running Ruff Lint (with auto-fix)..."
if ruff check . --fix --unsafe-fixes; then
    write_success "Ruff Lint checks passed!"
else
    write_failure "Ruff Lint found unfixable errors. Fix them manually before pushing."
fi

# -----------------------------------------------------------------------------
# 2. RUFF FORMATTING
# -----------------------------------------------------------------------------
write_step "2/4: Running Ruff Format..."
if ruff format . && ruff format --check .; then
    write_success "Code formatted cleanly according to PEP 8 / Ruff style!"
else
    write_failure "Ruff formatting check failed."
fi

# -----------------------------------------------------------------------------
# 3. MYPY TYPE CHECKING
# -----------------------------------------------------------------------------
write_step "3/4: Running Mypy Type Checker on 'src'..."
if mypy src; then
    write_success "Mypy static type verification passed with 0 errors!"
else
    write_failure "Mypy type checking failed. Fix type mismatches before pushing."
fi

# -----------------------------------------------------------------------------
# 4. PYTEST UNIT & INTEGRATION TESTS
# -----------------------------------------------------------------------------
write_step "4/4: Running Unit Tests & Generating Coverage..."
if pytest tests/unit/ -q --tb=short; then
    write_success "All tests passed successfully!"
else
    write_failure "One or more pytest test cases failed."
fi

# -----------------------------------------------------------------------------
# ALL CHECKS PASSED
# -----------------------------------------------------------------------------
echo -e "\n========================================================"
echo -e "${GREEN} 🎉 ALL CHECKS PASSED! YOUR CODE IS 100% CI/CD READY.${NC}"
echo -e "========================================================\n"
