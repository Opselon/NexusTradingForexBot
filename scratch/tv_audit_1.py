"""
tv_audit_1.py — Hermes-Main (Hermes-UI-01) Phase 1 evidence probe.

Verifies the LIVE /api/v1/indicators snapshot vs the user's reference
screenshot, and locks the DOM id contract the current tv_widget.js relies on,
BEFORE any redesign. Read-only. No UI changes.
"""
import json
import urllib.request

BASE = "http://127.0.0.1:8080"
SCREENSHOT = {  # reference values from user's clip (TV-style widget)
    "osc": {"sell": 4, "neutral": 6, "buy": 1},
    "sum": {"sell": 11, "neutral": 6, "buy": 9},
    "ma": {"sell": 7, "neutral": 0, "buy": 8},
}
rows = []


def check(label, cond, detail=""):
    rows.append(("PASS" if cond else "FAIL", label, detail))


def get(path):
    with urllib.request.urlopen(BASE + path, timeout=15) as r:
        return json.loads(r.read().decode())


d = get("/api/v1/indicators?timeframe=M1&limit=2000")["data"]

# 1. envelope + identity fields
check("envelope has symbol/timeframe/bar_count/last_close",
      all(k in d for k in ("symbol", "timeframe", "bar_count", "last_close")),
      f"symbol={d.get('symbol')} tf={d.get('timeframe')} bars={d.get('bar_count')} close={d.get('last_close')}")
check("source_bar_count present", "source_bar_count" in d, str(d.get("source_bar_count")))

# 2. gauges block: exactly the three keys the widget consumes
g = d.get("gauges", {})
check("gauges keys == {oscillators, moving_averages, summary}",
      set(g) == {"oscillators", "moving_averages", "summary"}, str(sorted(g)))
for key, ref in (("oscillators", SCREENSHOT["osc"]), ("summary", SCREENSHOT["sum"]),
                 ("moving_averages", SCREENSHOT["ma"])):
    gl = g.get(key, {})
    counts = {"sell": gl.get("sell"), "neutral": gl.get("neutral"), "buy": gl.get("buy")}
    check(f"gauge {key} counts match screenshot", counts == ref,
          f"{counts} vs ref {ref}; label={gl.get('label')}")

# 3. table payloads
osc, ma, piv = d["oscillators"], d["moving_averages"], d["pivots"]
check("oscillators rows == 11", len(osc) == 11, f"got {len(osc)}")
check("moving_averages rows == 15", len(ma) == 15, f"got {len(ma)}")
check("every indicator has name+value+action",
      all(set(r) == {"name", "value", "action"} for r in osc + ma))
expected_osc_names = ["Relative Strength Index (14)", "Stochastic %K (14, 3, 3)",
                      "Commodity Channel Index (20)", "Average Directional Index (14)",
                      "Awesome Oscillator", "Momentum (10)", "MACD Level (12, 26)",
                      "Stochastic RSI Fast (3, 3, 14, 14)", "Williams Percent Range (14)",
                      "Bull Bear Power", "Ultimate Oscillator (7, 14, 28)"]
check("oscillator names+order unchanged", [r["name"] for r in osc] == expected_osc_names)
actions = [r["action"] for r in osc + ma]
check("action vocabulary is Buy/Sell/Neutral (+Strong variants if any)",
      set(actions) <= {"Buy", "Sell", "Neutral", "Strong buy", "Strong sell"},
      str(sorted(set(actions))))

p = get("/api/v1/indicators/pivots?timeframe=M1")["data"]["pivots"]
check("pivot levels R3..S3", p["levels"] == ["R3", "R2", "R1", "P", "S1", "S2", "S3"], str(p["levels"]))
check("pivot columns == [Classic, Fibonacci, Camarilla, Woodie, DM]",
      p["columns"] == ["Classic", "Fibonacci", "Camarilla", "Woodie", "DM"], str(p["columns"]))

# 4. DOM id contract consumed by Web/tv_widget.js (must survive redesign)
html = urllib.request.urlopen(BASE + "/", timeout=15).read().decode()
js = urllib.request.urlopen(BASE + "/tv_widget.js", timeout=15).read().decode()
REQUIRED_IDS = ["tv-indicator-widget", "tv-timeframes", "tv-live-badge",
                "tv-gauge-osc", "tv-gauge-sum", "tv-gauge-ma",
                "tv-osc-label", "tv-sum-label", "tv-ma-label",
                "tv-osc-sell", "tv-osc-neu", "tv-osc-buy",
                "tv-sum-sell", "tv-sum-neu", "tv-sum-buy",
                "tv-ma-sell", "tv-ma-neu", "tv-ma-buy",
                "tv-osc-body", "tv-ma-body", "tv-pivot-body"]
missing = [i for i in REQUIRED_IDS if f'id="{i}"' not in html]
check("all tv_widget.js DOM ids present in served index.html", not missing, f"missing={missing}")
check("tv_widget.js served and consumes /api/v1/indicators", "/api/v1/indicators" in js)

print("=" * 70)
fails = 0
for st, label, detail in rows:
    print(f"[{st}] {label}" + (f"  — {detail}" if detail else ""))
    fails += st == "FAIL"
print("=" * 70)
print(f"RESULT: {len(rows) - fails}/{len(rows)} PASS, {fails} FAIL")
print("EVIDENCE: live API http://127.0.0.1:8080/api/v1/indicators?timeframe=M1&limit=2000")
raise SystemExit(1 if fails else 0)
