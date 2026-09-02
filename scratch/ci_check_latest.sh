#!/bin/bash
# Check CI status of the LATEST origin/main (single check)
cd /c/Users/Capsizer/source/repos/NexusTradingForexBot
git fetch origin >/dev/null 2>&1
SHA=$(git rev-parse origin/main)
echo "HEAD: $SHA"
git log origin/main --oneline -1 --format="%s"
TOKEN=$(git credential fill <<'CREDEOF'
protocol=https
host=github.com
CREDEOF
)
TOKEN=$(echo "$TOKEN" | grep '^password=' | cut -d= -f2)
curl -s -m 60 -H "Authorization: token $TOKEN" \
  "https://api.github.com/repos/Opselon/NexusTradingForexBot/actions/runs?per_page=8&head_sha=$SHA" \
  -o scratch/runs_latest.json
python - <<'PYEOF'
import json
d = json.load(open('scratch/runs_latest.json'))
for r in d.get('workflow_runs', []):
    print(r['id'], r['name'][:20], r['status'], r.get('conclusion'))
PYEOF
