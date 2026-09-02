#!/bin/bash
# Diagnose CI failure at 8c9fc3d (run 33590865439) — after BUG-213 root fix
cd /c/Users/Capsizer/source/repos/NexusTradingForexBot
TOKEN=$(git credential fill <<'CREDEOF'
protocol=https
host=github.com
CREDEOF
)
TOKEN=$(echo "$TOKEN" | grep '^password=' | cut -d= -f2)
AID=$(curl -s -m 60 -H "Authorization: token $TOKEN" \
  "https://api.github.com/repos/Opselon/NexusTradingForexBot/actions/runs/33590865439/artifacts" \
  | python -c "import json,sys; d=json.load(sys.stdin); a=[x for x in d.get('artifacts',[]) if 'quality' in x['name']]; print(a[0]['id'] if a else '')")
echo "artifact: $AID"
curl -sL -m 120 -H "Authorization: token $TOKEN" \
  "https://api.github.com/repos/Opselon/NexusTradingForexBot/actions/artifacts/$AID/zip" \
  -o scratch/ci6.zip
rm -rf scratch/ci6 && mkdir -p scratch/ci6
cd scratch/ci6 && unzip -o ../ci6.zip >/dev/null 2>&1
echo "=== checks ==="
echo "ruff violations: $(grep -cE '^[A-Z]+[0-9]+ ' ruff/lint.txt 2>/dev/null)"
tail -1 format/format.txt 2>/dev/null
grep -E "^\s+--> " format/format.txt 2>/dev/null | head -3
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
            fail_details.append((tc.get('classname'), tc.get('name'), (child.text or '')[:200]))
print('tests:', tests, 'failures:', fails)
for c, n, m in fail_details[:8]:
    print('FAIL:', c.split('.')[-1], '::', n)
    print('     ', m.replace(chr(10), ' ')[:170])
PYEOF
