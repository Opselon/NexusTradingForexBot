#!/bin/bash
# Diagnose CI failure at a97ccce (run 33587762695)
cd /c/Users/Capsizer/source/repos/NexusTradingForexBot
TOKEN=$(git credential fill <<'CREDEOF'
protocol=https
host=github.com
CREDEOF
)
TOKEN=$(echo "$TOKEN" | grep '^password=' | cut -d= -f2)
AID=$(curl -s -m 60 -H "Authorization: token $TOKEN" \
  "https://api.github.com/repos/Opselon/NexusTradingForexBot/actions/runs/33587762695/artifacts" \
  | python -c "import json,sys; d=json.load(sys.stdin); a=[x for x in d.get('artifacts',[]) if 'quality' in x['name']]; print(a[0]['id'] if a else '')")
echo "artifact: $AID"
curl -sL -m 120 -H "Authorization: token $TOKEN" \
  "https://api.github.com/repos/Opselon/NexusTradingForexBot/actions/artifacts/$AID/zip" \
  -o scratch/ci4.zip
rm -rf scratch/ci4 && mkdir -p scratch/ci4
cd scratch/ci4 && unzip -o ../ci4.zip >/dev/null 2>&1
echo "=== ruff violations ==="
grep -cE "^[A-Z]+[0-9]+ " ruff/lint.txt 2>/dev/null
grep -E "^\s+--> " ruff/lint.txt 2>/dev/null | sort | uniq -c | sort -rn | head -6
echo "=== format ==="
tail -2 format/format.txt 2>/dev/null
grep -E "^\s+--> " format/format.txt 2>/dev/null | head -4
echo "=== mypy ==="
tail -1 mypy/mypy.txt 2>/dev/null
echo "=== junit summary ==="
python - <<'PYEOF'
import xml.etree.ElementTree as ET
t = ET.parse('pytest/junit.xml')
r = t.getroot()
tests = fails = errs = skipped = 0
fail_details = []
for tc in r.iter('testcase'):
    tests += 1
    for child in tc:
        if child.tag in ('failure', 'error'):
            fails += 1
            fail_details.append((tc.get('classname'), tc.get('name'), (child.text or '')[:150]))
        elif child.tag == 'skipped':
            skipped += 1
print('tests:', tests, 'failures:', fails, 'skipped:', skipped)
for c, n, m in fail_details[:6]:
    print('FAIL:', c.split('.')[-1], '::', n)
    print('     ', m.replace(chr(10), ' ')[:140])
PYEOF
