#!/bin/bash
# Get pytest failures from the latest CI run artifact (86b13d6, run 33583881735)
cd /c/Users/Capsizer/source/repos/NexusTradingForexBot
TOKEN=$(git credential fill <<'CREDEOF'
protocol=https
host=github.com
CREDEOF
)
TOKEN=$(echo "$TOKEN" | grep '^password=' | cut -d= -f2)
AID=$(curl -s -H "Authorization: token $TOKEN" \
  "https://api.github.com/repos/Opselon/NexusTradingForexBot/actions/runs/33583881735/artifacts" \
  | python -c "import json,sys; d=json.load(sys.stdin); a=[x for x in d.get('artifacts',[]) if 'quality' in x['name']]; print(a[0]['id'] if a else '')")
echo "artifact: $AID"
curl -sL -H "Authorization: token $TOKEN" \
  "https://api.github.com/repos/Opselon/NexusTradingForexBot/actions/artifacts/$AID/zip" \
  -o scratch/ci_results2.zip
rm -rf scratch/ci2 && mkdir -p scratch/ci2
cd scratch/ci2 && unzip -o ../ci_results2.zip >/dev/null 2>&1
echo "=== ruff lint violations ==="
grep -cE "^[A-Z]+[0-9]+ " ruff/lint.txt
grep -E "^\s+--> " ruff/lint.txt | sort | uniq -c | sort -rn | head -8
echo "=== pytest tail ==="
grep -E "^FAILED" pytest/pytest.txt | head -8
grep -E "[0-9]+ (passed|failed)" pytest/pytest.txt | tail -1
