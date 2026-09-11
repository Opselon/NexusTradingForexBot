# Nexus on Linux — MT5 Test Platform

Status: **TEST/PAPER platform only.** Nothing here enables live trading.

> WSL2 (Kali) environment entry + full reproduction guide:
> **[docs/linux_wsl2_mt5_platform.md](../../docs/linux_wsl2_mt5_platform.md)**.
> The table below tracks the platform capability matrix across verification runs.

## What works on Linux

| Capability | Status | Evidence |
|---|---|---|
| Wine + isolated 64-bit prefix | OK | 2026-09-09: wine-staging 11.16, `/home/ubuntu/nexus-mt5/test/prefix` | 2026-09-11: wine 10.0 (Kali), `$NEXUS_MT5_ROOT/test/prefix` (WSL2) |
| Official MT5 installer silent install (`/auto`) | OK | `cache/mt5setup.exe` (sha `a879492d...f4c93` both runs) -> terminal64.exe: build 6184 (09-09) / **build 6191** (09-11) |
| Terminal start/stop/restart headless (Xvfb) | OK | `scripts/linux/mt5_runtime.sh start|stop|restart`; 11.7 h idle soak on WSL2, 0 crashes, 0 FD growth |
| Terminal log collection | OK | `logs/` (UTF-16 -> UTF-8 via `mt5_runtime.sh logs`) |
| Native `MetaTrader5` pip package on Linux | **impossible** | PyPI wheels are `win_amd64`-only (cp36-cp314); the native `_core` extension links `python311.dll`/USER32/KERNEL32 |
| `mt5.initialize()` <-> Wine terminal IPC | **fails - UNSUPPORTED, do not deploy** | Client pipe connect + handshake write succeed, terminal-side pipe server never replies -> `-10005 IPC timeout`. Proven with strace + `WINEDEBUG=+file` on wine 9.0, 10.0 and 11.16. Windows CPython under Wine additionally needs **numpy<2** (numpy 2.x openblas hits unimplemented `ucrtbase.crealf`, WineHQ bug 58943) |
| `RemoteMT5GatewayAdapter` (HTTP/HMAC) on Linux | OK | end-to-end stub-gateway test: connect -> GET_LAST_TICK -> disconnect; RTT median 0.80 ms / p95 1.26 ms (200 calls, WSL2 loopback 2026-09-11) |
| Windows CPython inside the Wine prefix | OK | `drive_c/python311` (embeddable zip; MSI installer fails under wine 10) - used for the IPC disproof above; keep for future retries |

## The supported integration path

`DirectMT5Adapter` (Win32 IPC) is **Windows-only by design** (`sys.platform == "win32"`
guard in `adapters/mt5/mt5_adapter.py`). On Linux the engine boots with
`RemoteMT5GatewayAdapter`, which talks to the audited HTTP/HMAC gateway
(`gateway/server.py`). Two topologies:

```
A (verified for tests):  WSL2: LiveEngine -> RemoteMT5GatewayAdapter --HTTP/HMAC--> gateway (same WSL host) -> stub/MT5-on-Wine (lifecycle only)
B (future):              WSL2: LiveEngine -> ... -> Windows host: gateway/server.py -> DirectMT5Adapter -> MT5
```

**Topology B caveat:** WSL2 -> Windows-host TCP/ICMP is blocked by the default
Hyper-V firewall. Enabling it requires mirrored networking
(`networkingMode=mirrored` in `.wslconfig`) or a firewall allow rule scoped to
the WSL subnet and the gateway port only - never a global firewall disable.
Until then, run engine + gateway inside WSL for tests.

Do NOT try to import `MetaTrader5` on Linux; the import guard is correct and
must stay. If a future Wine/MT5 version fixes the IPC handshake, the probe to
re-run is documented in `docs/linux_wsl2_mt5_platform.md` section 3.

## Reproducible install (no sudo at runtime)

```bash
# one-time system deps (needs sudo): wine + Xvfb (Kali: wine wine64 wine32:i386; Ubuntu: wine-staging via WineHQ repo)
sudo apt-get install -y wine wine64 wine32:i386 xvfb   # Kali/Debian multiarch

# everything else is user-space:
scripts/linux/mt5_runtime.sh install   # downloads official mt5setup.exe, silent install
scripts/linux/mt5_runtime.sh start     # Xvfb + terminal64 /portable
scripts/linux/mt5_runtime.sh health
scripts/linux/mt5_runtime.sh logs
scripts/linux/mt5_runtime.sh stop
python3 scripts/linux/mt5_doctor.py    # environment verification
```

Environment overrides: `NEXUS_MT5_ROOT` (default `/home/ubuntu/nexus-mt5`;
use `/root/nexus-mt5` or `$HOME/nexus-mt5` on WSL2),
`NEXUS_MT5_DISPLAY` (default `99`), `NEXUS_MT5_WINE` (default
`/opt/wine-staging/bin/wine`; on Kali set `NEXUS_MT5_WINE=/usr/bin/wine` and
`NEXUS_MT5_WINESERVER=/usr/lib/wine/wineserver64`), `NEXUS_MT5_WINEARCH`.

**Shell EOL:** `scripts/linux/*.sh` is pinned LF in `.gitattributes`
(commit `8fdcbdb9`). A CRLF working-tree copy fails in bash with
`set: -\r: invalid option` while still exiting 0 - if you see `$'\r'`
errors, re-checkout the file, do not patch around it.

## Environment separation (LINUX TEST != PAPER != LIVE)

- The test prefix under `NEXUS_MT5_ROOT/test` must never hold real-account
  credentials. Logins are demo-only; `mt5_doctor.py` fails when
  `NSE_MT5__ACCOUNT/PASSWORD/SERVER` are set on this host.
- A future LIVE environment requires a **separate host or separate prefix**,
  explicit `NSE_GATEWAY_*` secrets (defaults are refused for LIVE-allowed
  gateways - gateway/server.py AUDIT-B2) and `gateway serve --allow-live`.
  None of these exist on this box by default, and the doctor checks it.
