# Linux MT5 Mission Report — Windows-WSL2 (Kali) Re-Verification + IPC Forensics

**Agent:** Agent 11 (Linux Optimization & MT5 Infrastructure)
**Date:** 2026-09-11
**Scope:** Re-run the Linux MT5 test-platform mission on the operator's local
Windows 11 + WSL2 (Kali rolling) machine, since the original AWS Ubuntu VM
(`/home/ubuntu/nexus-mt5`) is no longer reachable. No live trading, no
strategy/ML/threshold changes, no production-code edits.

## Why this run exists

The 2026-09-09 mission (commit `38f3b80e`, on origin/main) delivered the Linux
MT5 test platform on a 4-vCPU Ubuntu VM. That VM is not reachable from the
current environment (no ssh config/keys found), so the mission was re-executed
locally inside WSL2 (Kali GNU/Linux Rolling, kernel 6.18, 8 vCPU i3-12100,
~7.8 GB RAM, 1 TB virtual disk at 1% usage) so the Linux-first workflow stays
testable on this box.

## What was measured (Phase 1 audit, WSL2 environment)

- CPU: 8 vCPU (i3-12100), host load avg ~0.15–0.9. Host Windows still shows
  ~64 python processes (Hermes swarm) using 60–80% total CPU — WSL2 inherits
  leftover cycles, do not treat host-side contention as a Linux defect.
- RAM: 7.6 GB visible to WSL; 2 GB swap on /dev/sdc (0 used).
- Disk: /dev/sdd 1007 GB, 1% used; I/O idle.
- FD limits: soft 10240, fs.file-max 9.2e15, somaxconn 4096.
- Clock: systemd-timesyncd, UTC synced; host TZ Asia/Tehran is a DISPLAY issue
  only (MT5 logged GMT+3 correctly per its own terminal log).
- Network: WSL NAT — 1.1.1.1 ping 99–334 ms (first-packet jitter is a WSL
  artifact), download.mql5.com resolves + download 23.8 MB in 9.7 s.
  WSL→Windows host (172.22.128.1) ICMP/TCP is BLOCKED by default
  (Hyper-V firewall); Windows→WSL localhost forwarding works (curl 200-ish
  via localhost), relevant for the RemoteMT5GatewayAdapter deployment
  (gateway should run INSIDE WSL next to the engine, or use mirrored
  networking mode in .wslconfig).
- /var/log/journal 800 MB (Kali default, no vacuuming) — cleanup recommended.

## MT5 under Wine (Phase 2) — re-proven on WSL2

- wine 10.0 (Kali repack, wine64 + wine32 i386 multiarch installed).
- Official MetaQuotes mt5setup.exe, sha256 a879492d…f4c93 (identical to the
  09-09 mission), silent /auto install into isolated prefix
  /root/nexus-mt5/test/prefix (WINEARCH=win64).
- terminal64.exe: build 6191 (newer than 6184), starts headless under Xvfb :99,
  /portable mode. Startup ~30–60 s incl. LiveUpdate ("new version build 6182
  available", mt5onnx64 70 MB downloaded; 131 MQL5 files recompiled).
- Steady-state (30 s per-thread accounting, no account logged in):
  RSS 260 MB, 20 threads, 106 fds, ~4–6% of one core total (main 1.9% +
  4x history-symbol threads 0.9% each) — matches the 09-09 VM numbers.
- scripts/linux/mt5_runtime.sh works as-is in WSL2 with
  `NEXUS_MT5_ROOT=/root/nexus-mt5 NEXUS_MT5_WINE=/usr/bin/wine
  NEXUS_MT5_WINESERVER=/usr/lib/wine/wineserver64` after CRLF fix
  (see Defect L2). stop/start/restart/health/version all verified.
- Gotcha (environment, not script): /tmp/.X11-unix is a read-only tmpfs mount
  in this WSL distro; Xvfb must be started before WSL remounts it read-only,
  or use a non-/tmp socket dir. If start fails with "Xvfb unavailable", check
  for a stale Xvfb holding abstract-namespace :99 (kill it, remove
  /tmp/.X99-lock, retry).

## Python↔MT5 IPC (Phase 3) — NEW FINDING, stronger disproof

Replicated the 09-09 result and went deeper:

- MetaTrader5 pip package: still no Linux wheels (`pip download` fails with
  "no matching distribution"). Repo import guard `HAS_NATIVE_MT5` correctly
  False on Linux.
- Windows CPython 3.11.9 (embeddable zip, sha 5ee42c4e…5fdde) installed at
  C:\python311 inside the prefix; python311._pth patched to enable site; pip
  bootstrapped; MetaTrader5 5.0.6180 + numpy installed INSIDE Wine.
- **numpy must be pinned <2**: numpy 2.x's bundled openblas calls
  `ucrtbase.crealf`, which wine's builtin ucrtbase does not implement →
  process aborts on import (WineHQ bug 58943 tracks the missing
  complex-float CRT exports). With numpy 1.26.4 the import succeeds.
- `mt5.initialize()` against a RUNNING terminal64 (build 6191, same prefix):
  returns False after 60.4 s with (-10005, 'IPC timeout') — reproduced twice
  on a fresh terminal start. WINEDEBUG=+file trace (1.6 MB):
  - client opens \\\\.\\pipe\\MT5.Terminal.781A…D12 → CreateFileW SUCCEEDS
    (handle 0x5c);
  - handshake write (28 B) completes; then 592 blocking 4-byte reads, 292
    8-byte writes — **zero responses**;
  - terminal's own log shows no pipe/IPC errors; the MCP HTTP listener
    (127.0.0.1:22346, 401 Unauthorized Bearer realm="MetaTrader5-MCP") is the
    only reachable API surface;
  - no named pipe unix-socket appears in /proc/net/unix for MT5.
- Conclusion unchanged (now with byte-level forensics): the Wine named-pipe
  server side never completes the MT5 IPC handshake on wine 10.0 either.
  Direct MetaTrader5-python-on-Linux remains UNSUPPORTED; the supported path
  is still RemoteMT5GatewayAdapter → Windows gateway (or this MCP port, if
  the operator ever enables it deliberately).

## Defect found and fixed (Phase 4)

- **L2 (proven, environment):** `scripts/linux/mt5_runtime.sh` is stored with
  CRLF in Windows working trees (git `text=auto`); running it via WSL/bash
  fails with `set: -\r: invalid option` + `$'\r': command not found` — every
  command exits 0 despite failing (masked breakage). Fix: pin
  `scripts/linux/*.sh text eol=lf` in .gitattributes. Blob at HEAD is already
  LF, so no renormalization churn; only future Windows checkouts change.

## Repo verification

- tests/unit/test_linux_mt5_platform.py: 8 tests, 6 pass 2 skip (win32 host)
  — still green at HEAD (b4f006e4).
- Foreign WIP in worktree (Agent-8 OBS-TRACE-2 edits to policy.py/auth.py,
  frontend/, pytest_*.txt) — untouched, not staged.

## Remaining honest gaps

- Broker demo-account login + symbol/market-data + order-API probes:
  BLOCKED-ON-OPERATOR (no credentials supplied by design).
- 8h+ soak: started 2026-09-11T02:17Z (pid 14621); check
  /root/nexus-mt5/soak_started_at + terminal log freshness later.
- numpy<2 pin must be added to any future Windows-side CPython-in-wine
  provisioning script if the Wine-IPC retry is ever attempted again.
