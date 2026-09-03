"""scratch probe: verify the custom OpenAI-compatible endpoint returns strategy JSON.

Purpose: prove the /v1/chat/completions endpoint answers with a JSON envelope
{strategies: [...]} using the user's key, so the factory provider gets a real
target. Output captured to probe_api_strategy_generation.out.txt.
"""
import json
import os
import sys

import httpx

BASE = os.environ.get("SF_BASE", "http://178.105.20.69:20128/v1")
KEY = os.environ.get("SF_KEY", "sk-7390310a65ab5b57-lxv2ql-f23e524e")
MODEL = os.environ.get("SF_MODEL", "claude-opus-5")

prompt = (
    "Return ONLY a JSON object with a key \"strategies\" holding 2 strategy "
    "hypotheses for XAUUSD M1 scalping. Each strategy object must have exactly "
    "these keys: schema_version, hypothesis, family, market, context, setup, "
    "entry, filters, exit, risk, constraints. Use only these features: "
    "norm_rsi, norm_atr_ratio, upper_wick_ratio. no_future_data must be true."
)

payload = {
    "model": MODEL,
    "temperature": 0.7,
    "max_tokens": 2000,
    "messages": [
        {"role": "system", "content": "You are a quantitative strategy designer. Reply in JSON only."},
        {"role": "user", "content": prompt},
    ],
    "response_format": {"type": "json_object"},
}
url = f"{BASE}/chat/completions"
headers = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}

try:
    resp = httpx.post(url, json=payload, headers=headers, timeout=120.0)
except Exception as e:
    print(f"NETWORK_FAIL {type(e).__name__}: {e}")
    sys.exit(1)

print(f"STATUS {resp.status_code}")
print(resp.text[:3000])
try:
    data = resp.json()
    choices = data.get("choices")
    print(f"CHOICES {len(choices) if choices else 0}")
    if choices:
        content = (choices[0].get("message") or {}).get("content") or ""
        print("---CONTENT---")
        print(content[:4000])
        obj = json.loads(content)
        print("---ENVELOPE---")
        print(json.dumps(obj, indent=2)[:4000])
        print("STRATEGIES", len(obj.get("strategies") or []))
except Exception as e:
    print(f"PARSE_FAIL {type(e).__name__}: {e}")
    print(resp.text[:2000])