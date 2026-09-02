#!/bin/bash
# Re-check CI on latest origin/main and ruff-format check on docs/api file locally
cd /c/Users/Capsizer/source/repos/NexusTradingForexBot
echo "=== ruff format check docs/api/API_REFERENCE.md ==="
.venv/Scripts/python.exe -m ruff format --check docs/api/API_REFERENCE.md 2>&1 | tail -2
echo ""
echo "=== ruff format check full repo (count) ==="
.venv/Scripts/python.exe -m ruff format --check . 2>&1 | tail -2
echo ""
echo "=== who owns docs/api/API_REFERENCE.md ==="
git log --oneline -2 -- docs/api/API_REFERENCE.md
