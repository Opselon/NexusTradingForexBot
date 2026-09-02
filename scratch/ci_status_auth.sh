#!/bin/bash
# Check CI status of origin/main HEAD (authenticated)
cd /c/Users/Capsizer/source/repos/NexusTradingForexBot
SHA=$(git rev-parse origin/main)
echo "HEAD: $SHA"
TOKEN=$(git credential fill <<'CREDEOF'
protocol=https
host=github.com
CREDEOF
)
TOKEN=$(echo "$TOKEN" | grep '^password=' | cut -d= -f2)
curl -s -m 60 -H "Authorization: token $TOKEN" \
  "https://api.github.com/repos/Opselon/NexusTradingForexBot/actions/runs?per_page=8&head_sha=$SHA" \
  -o scratch/runs_auth.json
python - <<'PYEOF'
import json
d = json.load(open('scratch/runs_auth.json'))
for r in d.get('workflow_runs', []):
    print(r['id'], r['name'][:20], r['status'], r.get('conclusion'))
PYEOF
