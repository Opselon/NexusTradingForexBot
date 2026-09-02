#!/bin/bash
# Inspect the latest CI failure on origin/main HEAD (86b13d6)
cd /c/Users/Capsizer/source/repos/NexusTradingForexBot
SHA=$(git rev-parse origin/main)
TOKEN=$(git credential fill <<'CREDEOF'
protocol=https
host=github.com
CREDEOF
)
TOKEN=$(echo "$TOKEN" | grep '^password=' | cut -d= -f2)
RUNID=$(curl -s -H "Authorization: token $TOKEN" \
  "https://api.github.com/repos/Opselon/NexusTradingForexBot/actions/runs?per_page=10&head_sha=$SHA" \
  | python -c "import json,sys; d=json.load(sys.stdin); ci=[r for r in d['workflow_runs'] if r['name']=='CI']; print(ci[0]['id'])")
echo "run: $RUNID"
curl -s -H "Authorization: token $TOKEN" \
  "https://api.github.com/repos/Opselon/NexusTradingForexBot/actions/runs/$RUNID/jobs" \
  -o scratch/jobs.json
python - <<'PYEOF'
import json
d = json.load(open('scratch/jobs.json'))
for j in d.get('jobs', []):
    print(j['id'], '|', j['name'][:40], '|', j['conclusion'])
PYEOF
JOBID=$(python -c "
import json
d = json.load(open('scratch/jobs.json'))
bad = [j for j in d.get('jobs', []) if j.get('conclusion') == 'failure']
print(bad[0]['id'] if bad else '')
")
echo "failing job: $JOBID"
curl -sL -H "Authorization: token $TOKEN" \
  "https://api.github.com/repos/Opselon/NexusTradingForexBot/actions/jobs/$JOBID/logs" \
  -o scratch/job_fail.log
wc -c scratch/job_fail.log
grep -nE "^FAILED|error:|Found [0-9]+ errors|would be reformatted|UnboundLocal|AttributeError" scratch/job_fail.log | head -20
