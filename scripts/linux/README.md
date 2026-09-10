# Nexus on Linux — MT5 Test Platform

Status: **TEST/PAPER platform only.** Nothing here enables live trading.

## What works on Linux (verified 2026-09-09, wine-11.16 staging, MT5 build 6184)

| Capability | Status | Evidence |
|---|---|---|
| Wine 11.16 + isolated 64-bit prefix | ✅ | `/home/ubuntu/nexus-mt5/test/prefix` (NEXUS_MT5_ROOT) |
| Official MT5 installer silent install (`/auto`) | ✅ | `cache/mt5setup.exe` → `terminal64.exe` build 6184 |
| Terminal start/stop/restart headless (Xvfb) | ✅ | `scripts/linux/mt5_runtime.sh start\|stop\|restart` |
| Terminal log collection | ✅ | `logs/` (UTF-16 → UTF-8 via `mt5_runtime.sh logs`) |
| Native `MetaTrader5` pip package on Linux | ❌ **impossible** | PyPI wheels are `win_amd64`-only (cp36–cp314); the native `_core` extension links `python311.dll`/USER32/KERNEL32 |
| `mt5.initialize()` ↔ Wine terminal IPC | ❌ **fails** | Named pipe `\pipe\MT5.Terminal.<sha256>` returns `c00000cb` (PIPE_NOT_AVAILABLE) from the pyd; terminal side never completes the handshake → `-10005 IPC timeout`. Proven with strace + WINEDEBUG=+file on both wine 9.0 and 11.16 |
| `RemoteMT5GatewayAdapter` (HTTP/HMAC) on Linux | ✅ | end-to-end stub-gateway test: connect → GET_LAST_TICK → disconnect, RTT ~1.4 ms localhost |
| Windows CPython inside the Wine prefix | ✅ | `drive_c/python311` (used for the IPC disproof above; keep for future retries) |

## The supported integration path

`DirectMT5Adapter` (Win32 IPC) is **Windows-only by design** (`sys.platform == "win32"`
guard in `adapters/mt5/mt5_adapter.py`). On Linux the engine boots with
`RemoteMT5GatewayAdapter`, which talks to the audited HTTP/HMAC gateway
(`gateway/server.py`) running **next to the terminal on a Windows host**:

```
Linux: LiveEngine → RemoteMT5GatewayAdapter --HTTPS/HMAC--> Windows: gateway/server.py → DirectMT5Adapter → MT5
```

Do NOT try to import `MetaTrader5` on Linux; the import guard is correct and
must stay. If a future Wine/MT5 version fixes the IPC handshake, the probe to
re-run is `phase3` of the 2026-09-09 mission (ipc_probe5.py inside the prefix).

## Reproducible install (no sudo at runtime)

```bash
# one-time system deps (needs sudo, ~250MB): wine-staging + Xvfb
sudo apt-get install -y wine-staging xvfb   # or the WineHQ repo for >= 10

# everything else is user-space:
scripts/linux/mt5_runtime.sh install   # downloads official mt5setup.exe, silent install
scripts/linux/mt5_runtime.sh start     # Xvfb + terminal64 /portable
scripts/linux/mt5_runtime.sh health
scripts/linux/mt5_runtime.sh logs
scripts/linux/mt5_runtime.sh stop
python3 scripts/linux/mt5_doctor.py    # environment verification
```

Environment overrides: `NEXUS_MT5_ROOT` (default `/home/ubuntu/nexus-mt5`),
`NEXUS_MT5_DISPLAY` (default `99`), `NEXUS_MT5_WINE` (default
`/opt/wine-staging/bin/wine`).

## Environment separation (LINUX TEST ≠ PAPER ≠ LIVE)

- The test prefix under `NEXUS_MT5_ROOT/test` must never hold real-account
  credentials. Logins are demo-only; `mt5_doctor.py` fails when
  `NSE_MT5__ACCOUNT/PASSWORD/SERVER` are set on this host.
- A future LIVE environment requires a **separate host or separate prefix**,
  explicit `NSE_GATEWAY_*` secrets (defaults are refused for LIVE-allowed
  gateways — gateway/server.py AUDIT-B2) and `gateway serve --allow-live`.
  None of these exist on this box by default, and the doctor checks it.
