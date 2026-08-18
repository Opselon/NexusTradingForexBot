import re

# count log lines per minute in the last window to see where flow stopped
counts = {}
with open("artifacts/logs/nse_live.log", encoding="utf-8", errors="replace") as f:
    for line in f:
        m = re.match(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2})", line)
        if not m:
            continue
        key = m.group(1)
        counts[key] = counts.get(key, 0) + 1
items = sorted(counts.items())
print("last 30 minutes of log line counts:")
for k, v in items[-30:]:
    print(k, v)
