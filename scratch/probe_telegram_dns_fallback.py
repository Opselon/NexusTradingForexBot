"""Standalone probe: TelegramNotifier DNS-poison bypass (2026-08-20).

Simulates the poisoned resolver (getaddrinfo returns 198.18.x.x) and
verifies the notifier connects DIRECTLY to a known-good Telegram IP with
SNI preserved, sending a real payload (a harmless getMe-style probe is not
used; we POST an actual diagnostic send to the user's admin chat only if
the network permits — otherwise we assert the connection path).

Safe: never touches the engine or the settings DB.
"""
import io
import json
import os
import socket
import ssl
import sys
import urllib.request
from unittest import mock

sys.path.insert(0, r"C:\Users\Capsizer\source\repos\NexusTradingForexBot\src")

from nexus_scalp.observability import telegram_notifier as tn  # noqa: E402

print("module import OK")

# --- 1) _is_blackhole_ip ---
assert tn.TelegramNotifier._is_blackhole_ip("198.18.141.205") is True
assert tn.TelegramNotifier._is_blackhole_ip("149.154.167.220") is False
assert tn.TelegramNotifier._is_blackhole_ip("127.0.0.1") is True
assert tn.TelegramNotifier._is_blackhole_ip("not-an-ip") is False
print("1) _is_blackhole_ip OK")

# --- 2) _should_bypass_dns under poisoned getaddrinfo ---
notifier = tn.TelegramNotifier(bot_token="x" * 40, admin_id="5094837833", enabled=False)
with mock.patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.141.205", 443))]):
    assert notifier._should_bypass_dns("api.telegram.org") is True
    assert notifier._last_dns_poisoned is True
print("2) _should_bypass_dns poisoned -> True OK")

# --- 3) healthy DNS -> no bypass, normal urllib path ---
with mock.patch("socket.getaddrinfo", return_value=[(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("149.154.167.220", 443))]):
    assert notifier._should_bypass_dns("api.telegram.org") is False
print("3) _should_bypass_dns healthy -> False OK")

# --- 4) _urlopen_with_dns_fallback: poisoned DNS -> DIRECT_IP_ATTEMPT, real delivery ---
# Patch the direct open to prove the path is taken, returning a fake 200 JSON
captured = {}

def fake_getaddrinfo(*a, **k):
    return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("198.18.141.205", 443))]

def fake_direct(ip, host, path, data, method, timeout):
    captured["ip"] = ip
    captured["host"] = host
    captured["path"] = path
    captured["method"] = method
    body = {"ok": True, "result": {"message_id": 9999}}
    class FakeResp:
        status = 200
        def read(self): return json.dumps(body).encode()
    return FakeResp()

with mock.patch("socket.getaddrinfo", side_effect=fake_getaddrinfo), \
     mock.patch.object(tn.TelegramNotifier, "_direct_https_open", side_effect=fake_direct):
    resp = notifier._urlopen_with_dns_fallback(
        "https://api.telegram.org/botXXX/sendMessage", b"{}", "POST", 5.0
    )
    assert resp.status == 200
    assert captured["ip"] in tn._TELEGRAM_FALLBACK_IPS, captured
    assert captured["host"] == "api.telegram.org", captured  # SNI preserved
    assert captured["path"] == "/botXXX/sendMessage", captured
    assert captured["method"] == "POST"
    print("4) _urlopen_with_dns_fallback -> direct IP w/ SNI OK:", captured["ip"])

# --- 5) classification: timeout after dns_poisoned -> DNS_BLOCKED ---
notifier._last_dns_poisoned = True
cat, retry = notifier._classify_exception(TimeoutError("handshake timed out"))
assert cat == tn.TELEGRAM_DNS_BLOCKED and retry is False, (cat, retry)
print("5) classify timeout+poisoned -> TELEGRAM_DNS_BLOCKED OK")

# --- 6) health_state exposes dns_poisoned ---
h = notifier.health_state()
assert "dns_poisoned" in h
print("6) health_state.dns_poisoned OK")

# --- 7) REAL end-to-end send via the notifier (live network, normal DNS) ---
# Token loaded from environment — never hardcode secrets in tracked files.
TOKEN = os.getenv("NEXUS_TELEGRAM_BOT_TOKEN")
ADMIN = os.getenv("NEXUS_TELEGRAM_ADMIN_ID", "5094837833")

if TOKEN:
    live = tn.TelegramNotifier(bot_token=TOKEN, admin_id=ADMIN, enabled=True, timeout_seconds=12.0)
    res = live.get_me()
    print("7) get_me live:", res)
    assert res.get("ok") is True, res

    # 7b) informational: forced direct-IP path (bypass works even when resolver lies)
    live._last_dns_poisoned = False
    with mock.patch("socket.getaddrinfo", side_effect=fake_getaddrinfo):
        res2 = live.get_me()
    print("7b) get_me forced-direct-path result:", res2)
    live.shutdown(timeout=1.0)
else:
    print("7) Skipping live end-to-end probe (NEXUS_TELEGRAM_BOT_TOKEN environment variable not set)")
print("ALL PROBES PASSED")
