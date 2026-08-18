"""Find the exact line of policy.py:1443 and what's around it."""

with open("src/nexus_scalp/signals/policy.py", encoding="utf-8") as f:
    lines = f.readlines()
for i in range(1438, 1450):
    print(i + 1, lines[i].rstrip()[:110])
