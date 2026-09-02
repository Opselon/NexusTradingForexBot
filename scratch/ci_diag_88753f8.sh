#!/bin/bash
# Diagnose CI failure at 88753f8 (run 33589698099)
cd /c/Users/Capsizer/source/repos/NexusTradingForexBot
TOKEN=$(git credential fill <<'CREDEOF'
protocol=https
host=github.com
CREDEOF
)
TOKEN=$(echo "$TOKEN" | grep '^password=' | cut -d= -f2)
AID=$(curl -s -m 60 -H "Authorization: token $TOKEN" \
  "https://api.github.com/repos/Opselon/NexusTradingForexBot/actions/runs/33589698099/artifacts" \
  | python -c "import json,sys; d=json.load(sys.stdin); a=[x for x in d.get('artifacts',[]) if 'quality' in x['name']]; print(a[0]['id'] if a else '')")
echo "artifact: $AID"
curl -sL -m 120 -H "Authorization: token $TOKEN" \
  "https://api.github.com/repos/Opselon/NexusTradingForexBot/actions/artifacts/$AID/zip" \
  -o scratch/ci5.zip
rm -rf scratch/ci5 && mkdir -p scratch/ci5
cd scratch/ci5 && unzip -o ../ci5.zip >/dev/null 2>&1
echo "=== checks ==="
echo "ruff violations: $(grep -cE '^[A-Z]+[0-9]+ ' ruff/lint.txt 2>/dev/null)"
tail -1 format/format.txt 2>/dev/null
tail -1 mypy/mypy.txt 2>/dev/null
python - <<'PYEOF'
import xml.etree.ElementTree as ET
t = ET.parse('pytest/junit.xml')
r = t.getroot()
tests = fails = 0
fail_details = []
for tc in r.iter('testcase'):
    tests += 1
    for child in tc:
        if child.tag in ('failure', 'error'):
            fails += 1
            fail_details.append((tc.get('classname'), tc.get('name'), (child.text or '')[:180]))
print('tests:', tests, 'failures:', fails)
for c, n, m in fail_details[:8]:
    print('FAIL:', c.split('.')[-1], '::', n)
    print('     ', m.replace(chr(10), ' ')[:160])
PYEOF
