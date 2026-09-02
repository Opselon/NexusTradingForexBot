#!/bin/bash
# Extract the summary section + failing checks from the latest CI failure log
cd /c/Users/Capsizer/source/repos/NexusTradingForexBot
echo "=== per-check rc lines ==="
grep -nE "CHECK [a-z_]+ rc=" scratch/job_fail.log | head -8
echo ""
echo "=== pytest failures (if any) ==="
grep -E "^FAILED" scratch/job_fail.log | head -10
echo ""
echo "=== gate summary ==="
grep -B2 -A8 "CI Validation" scratch/job_fail.log | head -20
echo ""
echo "=== mypy errors ==="
grep -E ": error:" scratch/job_fail.log | head -8
echo ""
echo "=== ruff first violations ==="
grep -A1 -E "^(RUF|I00|E[0-9]|F[0-9]|W[0-9])" scratch/job_fail.log | head -20
