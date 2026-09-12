# WSL2 Linux Test Platform (MT5 under Wine)

Status: **TEST/PAPER platform only.** Nothing in this document enables live
trading. No "production ready" claim is made while operator broker probes
remain unverified.

This is the environment registry entry + reproduction guide for the Linux
test platform **verified on 2026-09-11** (mission
`TASK-LINUX-MT5-WSL2-REVERIFY`, commit `8fdcbdb9`; original VM mission
`TASK-LINUX-MT5-PLATFORM`, commit `38f3b80e`).

---

## 1. Environment entry (registry)

| Property | Value |
| :--- | :--- |
| Platform | **WSL2** (Kali GNU/Linux Rolling, kernel 6.18.x-microsoft-standard-WSL2) |
| Host | Windows 11 Pro, i3-12100 (8 vCPU exposed to WSL), ~7.6 GB RAM, 1 TB virtual disk |
| MT5 terminal | MetaTrader 5 x64 **build 6191** under **Wine 10.0** (Kali `wine`/`wine64`/`wine32:i386` 10.0~repack-11) |
| Wine prefix | isolated, user-owned: `$NEXUS_MT5_ROOT/test/prefix` (`WINEARCH=win64`, terminal run `/portable`) |
| Headless display | **Xvfb required** (`:99`, 1280x1024x24) |
| Direct Python `MetaTrader5` IPC | **UNSUPPORTED** - `initialize()` returns `(-10005, 'IPC timeout')`; proven with `WINEDEBUG=+file` pipe forensics (client connect/write OK, terminal-side pipe server never replies). Reproduced on wine 9.0, 10.0 and 11.16 |
| Supported Nexus integration | **`RemoteMT5GatewayAdapter`** (HTTP + HMAC) - localhost RTT median 0.80 ms / p95 1.26 ms (200 calls) |
| Network topology caveat | **WSL2 to Windows host TCP/ICMP is blocked by default** (Hyper-V firewall). A gateway on the Windows host is unreachable from WSL until mirrored networking or a narrowly scoped allow rule exists. For tests, run engine + gateway **inside WSL** |
| Shell EOL requirement | `scripts/linux/*.sh` must be **LF** (pinned in `.gitattributes`, commit `8fdcbdb9`); CRLF copies fail in bash with `set: -CR: invalid option` **while exiting 0** - masked breakage |
| LIVE capability | **None.** No credentials on this host (doctor-enforced), gateway demo-guard + `--allow-live` refusal intact, `DirectMT5Adapter` fails closed on Linux |

Verified uses: MT5 runtime lifecycle (start/stop/restart/health/version),
release engineering dry-runs, gateway-adapter integration tests, soak
stability (11.7 h, see section 5).

Not verified / out of scope: broker demo-account login, market data,
order APIs (BLOCKED-ON-OPERATOR - no credentials supplied by design).

---

## 2. Reproduce from scratch (no sudo at runtime)

One-time system packages (needs sudo, WSL: `wsl.exe -d kali-linux`):

```bash
sudo apt-get update
sudo apt-get install -y wine wine64 wine32:i386 xvfb python3-pip python3-venv
# i386 multiarch first: dpkg --add-architecture i386 && sudo apt-get update
```

Everything else is user-space under `NEXUS_MT5_ROOT` (default
`/root/nexus-mt5` on this box; any writable dir works):

```bash
export NEXUS_MT5_ROOT=$HOME/nexus-mt5
export NEXUS_MT5_WINE=$(command -v wine)          # /usr/bin/wine (Kali)
export NEXUS_MT5_WINESERVER=$(command -v wineserver)
scripts/linux/mt5_runtime.sh install   # official mt5setup.exe, silent /auto
scripts/linux/mt5_runtime.sh start     # Xvfb :99 + terminal64 /portable
scripts/linux/mt5_runtime.sh health
scripts/linux/mt5_runtime.sh logs      # UTF-16 -> UTF-8 tail
scripts/linux/mt5_runtime.sh version
scripts/linux/mt5_runtime.sh stop
python3 scripts/linux/mt5_doctor.py    # environment verification (fail-closed)
```

Installer provenance: sha256 of `mt5setup.exe` recorded to
`$NEXUS_MT5_ROOT/cache/mt5setup.exe.sha256` on every install
(`a879492d...f4c93` for the 2026-09 runs). MT5 self-updates via LiveUpdate on
first start (6184 to 6191 observed; the terminal manages its own build).

**WSL2 display gotcha:** `/tmp/.X11-unix` is a read-only tmpfs in some WSL
distros. If Xvfb cannot bind (log: `failed to bind listener`), kill stale
`Xvfb :99` processes, remove `/tmp/.X99-lock`, and retry; `mt5_runtime.sh`
detects the socket and reuses a running Xvfb.

---

## 3. Direct Python-in-Wine probe (documented as UNSUPPORTED - do not deploy)

Reproduction of the disproof (kept for future wine-version re-tests):

1. Install Windows CPython 3.11.9 **embeddable zip** into the prefix at
   `C:\python311` (the MSI installer aborts under wine 10; the zip works).
   Patch `python311._pth` -> uncomment `import site`.
2. `python.exe -m pip` via get-pip, then `pip install MetaTrader5 "numpy<2"`.
   **numpy MUST be <2**: numpy 2.x bundles an openblas that calls
   `ucrtbase.crealf`, unimplemented in wine 10's builtin ucrtbase -> the
   interpreter aborts on `import numpy` (WineHQ bug 58943).
3. With terminal64 running: `initialize()` returns `False` after ~60 s with
   `(-10005, 'IPC timeout')`. `WINEDEBUG=+file` shows the client's
   `CreateFileW` on `\\.\pipe\MT5.Terminal.<sha256>` **succeeds**, the
   handshake write completes, then every 4-byte read blocks - the terminal
   side never completes the handshake. No Wine IPC workaround will be
   shipped; re-test only when a newer Wine claims named-pipe fixes.

---

## 4. Supported integration path

```
WSL2: LiveEngine -> RemoteMT5GatewayAdapter --HTTP/HMAC--> gateway (same WSL host for tests)
                                                            -> (future) Windows host MT5 gateway
```

- `NSE_GATEWAY_URL` / `NSE_GATEWAY_API_KEY` / `NSE_GATEWAY_SECRET` configure
  the client; defaults are refused for LIVE-allowed gateways (AUDIT-B2).
- `nse gateway status` verifies reachability + HMAC PING from Linux.
- Gateway-unavailable behaviour (verified): `connect()` returns `False`,
  `get_last_tick()` raises `RuntimeError` - the engine fails closed, never
  fabricates market data.
- If topology B (gateway on Windows host) is ever chosen, the minimal safe
  change is mirrored networking (`networkingMode=mirrored` in
  `.wslconfig`) or a Hyper-V firewall rule scoped to WSL subnet -> host
  `127.0.0.1:<gateway-port>` - never a global firewall disable.

---

## 5. Soak evidence (2026-09-11)

- Started 02:17:54Z, collected 13:42Z -> **11.7 h wall**, single process
  (pid 14621), zero restarts after the final start (05:31:46Z; terminal log
  shows 13 starts total = the mission's own install/LiveUpdate/probe
  cycles, 1 clean `shutdown with 0`).
- RSS 260 -> 276 MB over 10.9 h (+16 MB, then flat; 0.0 MB delta in 60 s
  windows), 20 threads constant, **106 fds constant** (no FD leak),
  VmSwap 0 kB.
- CPU: 2.0-2.2% of one core average, ~5% instantaneous, wineserver ~1%.
- Terminal health: process alive, log written, MCP listener on
  127.0.0.1:22346 up, `mt5_runtime.sh health` PASS.
- Verdict: **no unexplained crash, no runaway memory, no FD leak, no
  lifecycle degradation** (idle-terminal soak, no account logged in).
