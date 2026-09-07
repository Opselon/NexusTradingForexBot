# Determinism check: re-run baseline_eval twice, compare all metric fields
# except the timestamp (which is wall-clock by design).
import json
import subprocess
import sys

MODEL = "artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt"
DS = "ds_70d_clean_m1_20260904"
PY = ".venv/Scripts/python.exe"

cmd = [PY, "-m", "nexus_scalp.research.baseline_eval", "--model", MODEL, "--dataset", DS]


def run():
    subprocess.run(cmd, capture_output=True)
    return json.load(open("artifacts/forensics/baseline_eval/bb1f0afe30f746da0aff38ef530c5229df6044c6688c79fdb77feb4e8aee683d.json"))


a = run()
b = run()
a.pop("timestamp", None)
b.pop("timestamp", None)
print("deterministic:", a == b)
if a != b:
    for k in a:
        if a.get(k) != b.get(k):
            print("DIFF:", k)
            print("  A:", str(a.get(k))[:120])
            print("  B:", str(b.get(k))[:120])
print("headline:", a["metrics"]["expectancy_r"], "trades:", a["metrics"]["trades"])
print("always_buy:", a["baseline_comparisons"]["always_buy"]["expectancy_r"])
print("always_sell:", a["baseline_comparisons"]["always_sell"]["expectancy_r"])
print("decision:", a["decision"])
print("artifact:", a.get("artifact_path"))
sys.exit(0 if a == b else 1)
