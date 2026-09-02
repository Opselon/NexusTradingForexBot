#!/bin/bash
# Fetch CI artifacts for run 33581709459 (HEAD 6b893f0 CI failure)
cd /c/Users/Capsizer/source/repos/NexusTradingForexBot
TOKEN=$(git credential fill <<'CREDEOF'
protocol=https
host=github.com
CREDEOF
)
TOKEN=$(echo "$TOKEN" | grep '^password=' | cut -d= -f2)
curl -s -H "Authorization: token $TOKEN" \
  "https://api.github.com/repos/Opselon/NexusTradingForexBot/actions/runs/33581709459/artifacts" \
  -o scratch/ci_artifacts.json
python - <<'PYEOF'
import json
d = json.load(open('scratch/ci_artifacts.json'))
for a in d.get('artifacts', [])[:12]:
    print(a['id'], a['name'], a['size_in_bytes'])
PYEOF
