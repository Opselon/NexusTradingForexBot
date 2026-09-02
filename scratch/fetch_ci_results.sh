#!/bin/bash
# Download + inspect CI results artifact for run 33581709459 (6b893f0)
cd /c/Users/Capsizer/source/repos/NexusTradingForexBot
TOKEN=$(git credential fill <<'CREDEOF'
protocol=https
host=github.com
CREDEOF
)
TOKEN=$(echo "$TOKEN" | grep '^password=' | cut -d= -f2)
curl -sL -H "Authorization: token $TOKEN" \
  "https://api.github.com/repos/Opselon/NexusTradingForexBot/actions/artifacts/9828760978/zip" \
  -o scratch/ci_results.zip
mkdir -p scratch/ci_results_x
cd scratch/ci_results_x && unzip -o ../ci_results.zip >/dev/null 2>&1
echo "=== files ==="
find . -name "*.txt" | head -12
echo "=== ruff lint head ==="
head -c 1500 ruff/lint.txt 2>/dev/null
echo ""
echo "=== ruff format head ==="
head -c 800 format/format.txt 2>/dev/null
echo ""
echo "=== mypy head ==="
head -c 1200 mypy/mypy.txt 2>/dev/null
