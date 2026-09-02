#!/bin/bash
# Diagnose CI failure at 03d0848 (run 33586820518)
cd /c/Users/Capsizer/source/repos/NexusTradingForexBot
TOKEN=$(git credential fill <<'CREDEOF'
protocol=https
host=github.com
CREDEOF
)
TOKEN=$(echo "$TOKEN" | grep '^password=' | cut -d= -f2)
curl -s -m 60 -H "Authorization: token $TOKEN" \
  "https://api.github.com/repos/Opselon/NexusTradingForexBot/actions/runs/33586820518/jobs" \
  -o scratch/jobs2.json
python - <<'PYEOF'
import json
d = json.load(open('scratch/jobs2.json'))
for j in d.get('jobs', []):
    print(j['id'], '|', j['name'][:40], '|', j['conclusion'])
PYEOF
AID=$(curl -s -m 60 -H "Authorization: token $TOKEN" \
  "https://api.github.com/repos/Opselon/NexusTradingForexBot/actions/runs/33586820518/artifacts" \
  | python -c "import json,sys; d=json.load(sys.stdin); a=[x for x in d.get('artifacts',[]) if 'quality' in x['name']]; print(a[0]['id'] if a else '')")
echo "artifact: $AID"
if [ -n "$AID" ]; then
  curl -sL -m 120 -H "Authorization: token $TOKEN" \
    "https://api.github.com/repos/Opselon/NexusTradingForexBot/actions/artifacts/$AID/zip" \
    -o scratch/ci3.zip
  rm -rf scratch/ci3 && mkdir -p scratch/ci3
  cd scratch/ci3 && unzip -o ../ci3.zip >/dev/null 2>&1
  echo "=== ruff violations ==="
  grep -cE "^[A-Z]+[0-9]+ " ruff/lint.txt 2>/dev/null
  grep -E "^\s+--> " ruff/lint.txt 2>/dev/null | sort | uniq -c | sort -rn | head -8
  echo "=== pytest FAILED ==="
  grep -E "^FAILED" pytest/pytest.txt 2>/dev/null | head -6
  grep -E "[0-9]+ (passed|failed)" pytest/pytest.txt 2>/dev/null | tail -1
  echo "=== mypy ==="
  tail -2 mypy/mypy.txt 2>/dev/null
  echo "=== format ==="
  grep -c "unformatted" format/format.txt 2>/dev/null
fi
