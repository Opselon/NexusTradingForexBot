#!/bin/bash
# Poll CI on latest origin/main until completion (authenticated)
cd /c/Users/Capsizer/source/repos/NexusTradingForexBot
git fetch origin >/dev/null 2>&1
SHA=$(git rev-parse origin/main)
echo "watching $SHA"
TOKEN=$(git credential fill <<'CREDEOF'
protocol=https
host=github.com
CREDEOF
)
TOKEN=$(echo "$TOKEN" | grep '^password=' | cut -d= -f2)
for i in $(seq 1 14); do
  sleep 60
  ST=$(curl -s -m 50 -H "Authorization: token $TOKEN" \
    "https://api.github.com/repos/Opselon/NexusTradingForexBot/actions/runs?per_page=10&head_sha=$SHA" \
    | python -c "import json,sys; d=json.load(sys.stdin); ci=[r for r in d['workflow_runs'] if r['name']=='CI']; print(ci[0]['status'], ci[0].get('conclusion')) if ci else print('none')")
  echo "poll $i: $ST"
  case "$ST" in
    completed*) break;;
  esac
done
echo "FINAL-CI-MAIN: $ST"
