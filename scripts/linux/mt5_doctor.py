#!/usr/bin/env python3
"""Nexus MT5 Linux doctor — environment & integration health checks (read-only).

Verifies, WITHOUT any broker contact:
  1. Wine runtime present and version-known
  2. Isolated MT5 prefix layout (NEXUS_MT5_ROOT)
  3. Terminal binary present + build string detectable
  4. Xvfb / headless display availability
  5. Native MetaTrader5 python package ABSENCE on Linux (expected)
  6. RemoteMT5GatewayAdapter importability (the supported Linux integration path)
  7. Environment separation: Linux test host must not carry LIVE credentials

Exit code 0 = environment usable for the Linux TEST platform.
"""

from __future__ import annotations

import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(os.environ.get("NEXUS_MT5_ROOT", "/home/ubuntu/nexus-mt5"))
PREFIX = ROOT / "test" / "prefix"
TERMINAL = PREFIX / "drive_c" / "Program Files" / "MetaTrader 5" / "terminal64.exe"
WINE = os.environ.get("NEXUS_MT5_WINE", "/opt/wine-staging/bin/wine")
LAUNCH_LOG = ROOT / "logs" / "terminal_launch.log"

PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
results: list[tuple[str, str, str]] = []


def check(name: str, ok: bool | None, detail: str = "") -> None:
    status = PASS if ok else (WARN if ok is None else FAIL)
    results.append((status, name, detail))


def terminal_build() -> str | None:
    """Detect the MT5 build number.

    Primary: the terminal's own log line 'MetaTrader 5 x64 build NNNN started'
    (UTF-16LE logs written by the terminal). Fallback: UTF-16LE scan of the
    binary — the build string is stored UTF-16 inside the PE, not ASCII.
    """
    for log in sorted((TERMINAL.parent / "logs").glob("*.log"), reverse=True):
        try:
            text = log.read_bytes().decode("utf-16-le", errors="ignore")
        except OSError:
            continue
        m = re.search(r"MetaTrader 5 x64 build (\d{4,5}) started", text)
        if m:
            return m.group(1)
    try:
        data = TERMINAL.read_bytes()
    except OSError:
        return None
    for m in re.finditer((r"build (\d{4,5})").encode("utf-16-le"), data):
        return m.group(1).decode()
    return None


def main() -> int:
    # 1. wine
    if Path(WINE).exists():
        try:
            v = subprocess.run(
                [WINE, "--version"], capture_output=True, text=True, timeout=20, check=False
            )
            check("wine-runtime", True, v.stdout.strip())
        except Exception as exc:  # pragma: no cover
            check("wine-runtime", False, str(exc))
    else:
        check("wine-runtime", False, f"{WINE} not found")

    # 2. prefix
    check("isolated-prefix", PREFIX.is_dir(), str(PREFIX))

    # 3. terminal binary + build
    if TERMINAL.exists():
        data = TERMINAL.stat().st_size
        check("terminal-binary", True, f"size={data}")
        build = terminal_build()
        check("terminal-build", build is not None, f"build={build}")
    else:
        check("terminal-binary", False, f"{TERMINAL} missing")
        check("terminal-build", None, "terminal not installed")

    # 4. headless display
    display = os.environ.get("NEXUS_MT5_DISPLAY", "99")
    if shutil.which("xdpyinfo"):
        r = subprocess.run(
            ["xdpyinfo", "-display", f":{display}"], capture_output=True, timeout=10, check=False
        )
        check("xvfb-display", r.returncode == 0, f":{display}")
    else:
        check("xvfb-display", None, "xdpyinfo not installed (cannot probe)")

    # 5. native MetaTrader5 package must NOT import on Linux
    try:
        import MetaTrader5  # type: ignore[import-not-found]  # noqa: F401

        check("native-mt5-package", False, "unexpectedly importable on Linux")
    except ImportError:
        check("native-mt5-package", True, "absent as expected (win-only wheel)")
    except Exception as exc:
        check("native-mt5-package", True, f"present but not usable on Linux: {exc}")

    # 6. gateway adapter importable
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
        from nexus_scalp.adapters.mt5.remote_gateway import (  # noqa: F401
            RemoteMT5GatewayAdapter,
        )

        check("gateway-adapter", True, "RemoteMT5GatewayAdapter importable")
    except Exception as exc:
        check("gateway-adapter", False, str(exc))

    # 7. LIVE credential leakage on this host (fail-closed for TEST platform)
    live_env = [
        k
        for k in (
            "NSE_MT5__ACCOUNT",
            "NSE_MT5__PASSWORD",
            "NSE_MT5__SERVER",
        )
        if os.environ.get(k)
    ]
    check(
        "no-live-credentials-in-env",
        not live_env,
        f"set: {live_env}" if live_env else "clean",
    )
    cfg = Path(".env")
    if cfg.exists():
        text = cfg.read_text(encoding="utf-8", errors="ignore")
        suspicious = [
            line.split("=")[0]
            for line in text.splitlines()
            if "=" in line
            and not line.strip().startswith("#")
            and ("MT5_PASSWORD" in line or "MT5__PASSWORD" in line)
        ]
        check(
            "no-mt5-password-in-repo-dotenv",
            not suspicious,
            f"keys: {suspicious}" if suspicious else "clean",
        )
    else:
        check("no-mt5-password-in-repo-dotenv", True, "no .env in cwd")

    check("platform", sys.platform.startswith("linux"), f"{platform.platform()}")

    width = max(len(n) for _, n, _ in results)
    fails = 0
    for status, name, detail in results:
        print(f"{status:4} {name:<{width}}  {detail}")
        if status == FAIL:
            fails += 1
    print()
    if fails:
        print(f"doctor: {fails} FAIL — Linux MT5 test platform NOT fully usable")
        return 1
    print("doctor: environment usable for the Linux MT5 TEST platform")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
