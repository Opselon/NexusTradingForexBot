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
YELLOW='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# Always use the repository toolchain, not a globally installed CLI.
PYTHON_BIN="${PYTHON_BIN:-.venv/Scripts/python.exe}"
RUFF=("$PYTHON_BIN" -m ruff)
MYPY=("$PYTHON_BIN" -m mypy)
PYTEST=("$PYTHON_BIN" -m pytest)

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
if "${RUFF[@]}" check . --fix --unsafe-fixes; then
    write_success "Ruff Lint checks passed!"
else
    write_failure "Ruff Lint found unfixable errors. Fix them manually before pushing."
fi

# -----------------------------------------------------------------------------
# 2. RUFF FORMATTING
# -----------------------------------------------------------------------------
write_step "2/4: Running Ruff Format..."
if "${RUFF[@]}" format . && "${RUFF[@]}" format --check .; then
    write_success "Code formatted cleanly according to PEP 8 / Ruff style!"
else
    write_failure "Ruff formatting check failed."
fi

# -----------------------------------------------------------------------------
# 3. MYPY TYPE CHECKING
# -----------------------------------------------------------------------------
write_step "3/4: Running Mypy Type Checker on 'src'..."
if "${MYPY[@]}" src; then
    write_success "Mypy static type verification passed with 0 errors!"
else
    write_failure "Mypy type checking failed. Fix type mismatches before pushing."
fi

# -----------------------------------------------------------------------------
# 4. PYTEST UNIT & INTEGRATION TESTS
# -----------------------------------------------------------------------------
write_step "4/4: Running Critical-Suite Tests & Generating Coverage..."
# Default gate = the CI-critical suite (tests/critical_suite.txt, ~792 tests,
# ~2-4 min with xdist). Pass -FullSuite to run ALL unit tests (the slow
# legacy gate). Mirrors beforePush.ps1 semantics.
if [[ "$*" == *"-FullSuite"* ]]; then
    write_step "4/4b: FULL suite gate requested (all unit tests - slow)"
    if "${PYTEST[@]}" tests/unit/ -q --tb=short; then
        write_success "All tests passed successfully!"
    else
        write_failure "One or more pytest test cases failed."
    fi
else
    CRIT_FILES=()
    while IFS= read -r line; do
        case "$line" in ''|\#*) continue ;; esac
        CRIT_FILES+=("$line")
    done < tests/critical_suite.txt
    if [ ${#CRIT_FILES[@]} -eq 0 ]; then
        CRIT_FILES=("tests/unit/")
    fi
    if "${PYTEST[@]}" "${CRIT_FILES[@]}" -n auto --dist worksteal -q --tb=short; then
        write_success "All critical-suite tests passed successfully!"
    else
        write_failure "One or more critical-suite test cases failed."
    fi
fi

# -----------------------------------------------------------------------------
# 5. CANONICAL FORENSIC DEPLOY GATE (TASK-12)
# -----------------------------------------------------------------------------
# One health engine + one gate contract: `nexus forensic --deploy-gate`.
# Exit 0 = ALLOW / ALLOW_WITH_WARNING; 1 = BLOCK (CRITICAL); 2 = REVIEW
# (DEGRADED/UNKNOWN); 3 = FORENSIC_ENGINE_UNAVAILABLE (fail-safe block).
# The hook only CALLS the canonical engine — it never re-implements health
# rules (TASK-12 §5).
write_step "5/5: Running Forensic Deploy Gate..."
GATE_EXIT=0
if "$PYTHON_BIN" -m nexus_scalp.cli.main forensic --deploy-gate --json > artifacts/forensics/deploy_gate_result.json 2>&1; then
    write_success "Forensic deploy gate: ALLOW (no critical conditions)."
else
    GATE_EXIT=$?
    if [ "$GATE_EXIT" -eq 1 ]; then
        write_failure "Forensic deploy gate BLOCKED deployment (CRITICAL checks). See artifacts/forensics/deploy_gate_result.json"
    elif [ "$GATE_EXIT" -eq 2 ]; then
        echo -e "\n${YELLOW} ⚠ Forensic deploy gate: REVIEW REQUIRED (DEGRADED/UNKNOWN conditions). See artifacts/forensics/deploy_gate_result.json${NC}"
    elif [ "$GATE_EXIT" -eq 3 ]; then
        write_failure "Forensic engine UNAVAILABLE — deployment cannot be verified (fail-safe block)."
    else
        write_failure "Forensic deploy gate failed with exit $GATE_EXIT."
    fi
fi

# -----------------------------------------------------------------------------
# ALL CHECKS PASSED
# -----------------------------------------------------------------------------
echo -e "\n========================================================"
echo -e "${GREEN} 🎉 ALL CHECKS PASSED! YOUR CODE IS 100% CI/CD READY.${NC}"
echo -e "========================================================\n"
