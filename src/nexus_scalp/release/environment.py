"""Host environment detection for the Nexus release system.

Deterministic, dependency-free detection of everything the installer and the
doctor command need to know about the machine before installing or running:

    OS, architecture (x64 / ARM64 / ...), Python availability & version,
    RAM, disk space, GPU / NVIDIA driver / CUDA, Visual C++ runtime,
    MetaTrader5 availability, network connectivity, PowerShell,
    administrator privileges, architecture compatibility.

Architecture policy (from the ACTUAL dependency stack, 2026-08):
    * windows-x64         SUPPORTED  — torch / polars / pyarrow / MetaTrader5
                                       all ship x64 wheels for CPython 3.11.
    * windows-arm64       UNSUPPORTED — no torch / polars / pyarrow /
                                       MetaTrader5 wheels exist for this
                                       target today, so an ARM64 installer
                                       would fail inside the payload download.
    * Linux (container)   developer/Docker only (remote-gateway mode).
"""

from __future__ import annotations

import ctypes
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SUPPORTED_ARCHES = ("x64", "AMD64", "x86_64")
UNSUPPORTED_ARCHES = ("ARM64", "aarch64", "arm64")

MIN_RAM_MB = 4096


def is_windows() -> bool:
    return sys.platform == "win32"


RECOMMENDED_RAM_MB = 8192
MIN_FREE_DISK_MB = 2048
RECOMMENDED_FREE_DISK_MB = 5120
SUPPORTED_PYTHON = (3, 11)
SUPPORTED_PYTHON_STR = "3.11.x"


@dataclass
class EnvironmentInfo:
    """Snapshot of one machine's detected environment."""

    os_name: str = ""
    os_version: str = ""
    architecture: str = ""
    process_architecture: str = ""
    python_available: bool = False
    python_path: str | None = None
    python_version: tuple[int, int, int] | None = None
    ram_mb: int = 0
    free_disk_mb: int = 0
    gpu_name: str | None = None
    nvidia_driver: str | None = None
    cuda_available: bool = False
    cuda_version: str | None = None
    vc_runtime: str | None = None
    mt5_available: bool = False
    mt5_native_module: bool = False
    network_reachable: bool | None = None
    powershell_available: bool = False
    is_admin: bool = False
    cpu_name: str | None = None
    cpu_cores: int = 0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def architecture_supported(self) -> bool:
        return (
            self.architecture in SUPPORTED_ARCHES or self.process_architecture in SUPPORTED_ARCHES
        )


def _run(cmd: list[str], timeout: int = 5) -> str | None:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
        return (out.stdout or out.stderr).strip() or None
    except Exception:
        return None


def _read_os_version() -> str:
    try:
        return platform.version() or platform.release()
    except Exception:
        return ""


def _detect_ram_mb() -> int:
    if sys.platform == "win32":
        try:
            # GlobalMemoryStatusEx via ctypes — no external deps.
            class MEMORYSTATUSEX(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            stat = MEMORYSTATUSEX()
            stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return int(stat.ullTotalPhys // (1024 * 1024))
        except Exception:
            pass
    try:
        out = _run(["wmic", "ComputerSystem", "get", "TotalPhysicalMemory"])
        if out and out.splitlines():
            for line in out.splitlines():
                if line.strip().isdigit():
                    return int(line.strip()) // (1024 * 1024)
    except Exception:
        pass
    return 0


def _detect_free_disk_mb(path: Path) -> int:
    try:
        if sys.platform == "win32":
            free = ctypes.c_ulonglong(0)
            ctypes.windll.kernel32.GetDiskFreeSpaceExW(
                ctypes.c_wchar_p(str(path.resolve())), None, None, ctypes.byref(free)
            )
            if free.value:
                return int(free.value // (1024 * 1024))
            return 0
        else:
            usage = shutil.disk_usage(path)
            return int(usage.free // (1024 * 1024))
    except Exception:
        return 0


def _detect_gpu() -> tuple[str | None, str | None, bool, str | None]:
    """Returns (gpu_name, nvidia_driver_version, cuda_available, cuda_version)."""
    gpu: str | None = None
    driver: str | None = None
    cuda = False
    cuda_ver: str | None = None
    if sys.platform == "win32":
        out = _run(
            [
                "wmic",
                "path",
                "win32_VideoController",
                "get",
                "name,driverversion",
            ],
            timeout=8,
        )
        for raw in (out or "").splitlines()[1:]:
            line = raw.strip()
            if not line:
                continue
            parts = line.rsplit(None, 1)
            name = parts[0]
            driver = parts[1] if len(parts) > 1 else None
            if not gpu:
                gpu = name
            if "nvidia" in name.lower():
                gpu = name
                break
    try:
        import torch  # type: ignore[import-not-found]

        if torch.cuda.is_available():
            cuda = True
            cuda_ver = torch.version.cuda
            if not gpu:
                gpu = torch.cuda.get_device_name(0)
    except Exception:
        pass
    return gpu, driver, cuda, cuda_ver


def _detect_vc_runtime() -> str | None:
    """Visual C++ 2015-2022 x64 redistributable detection (registry)."""
    if sys.platform != "win32":
        return None
    import winreg  # type: ignore[import-not-found]

    keys = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64",
        ),
    ]
    for hive, key in keys:
        try:
            with winreg.OpenKey(hive, key) as k:
                installed, _ = winreg.QueryValueEx(k, "Installed")
                if installed == 1:
                    try:
                        ver, _ = winreg.QueryValueEx(k, "Version")
                        return ver
                    except OSError:
                        return "installed"
        except OSError:
            continue
    return None


def _detect_mt5() -> tuple[bool, bool]:
    """(mt5_terminal_installed, native_python_module_available)"""
    terminal = False
    if sys.platform == "win32":
        terminal = any(
            Path(p).exists()
            for p in (
                r"C:\Program Files\MetaTrader 5\terminal64.exe",
                r"C:\Program Files\MetaTrader 5\terminal.exe",
            )
        )
    native_module = False
    try:
        import MetaTrader5 as mt5  # type: ignore[import-not-found]

        native_module = True
        if terminal is False:
            # Ask the module itself where its terminal is.
            try:
                term_path = mt5.terminal_info()
                if term_path is not None:
                    p = Path(getattr(term_path, "path", "") or "")
                    if p.exists():
                        terminal = True
            except Exception:
                pass
    except Exception:
        pass
    return terminal, native_module


def _detect_network(timeout: int = 4) -> bool | None:
    """True/False when determinable, None on unknown. Never fatal."""
    try:
        import urllib.request

        req = urllib.request.Request("https://pypi.org", method="HEAD")
        with urllib.request.urlopen(req, timeout=timeout):
            return True
    except Exception:
        pass
    # No network -> local-only features still work; report False (not fatal).
    return False


def _is_admin() -> bool:
    if sys.platform != "win32":
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _detect_architecture() -> str:
    machine = platform.machine() or ""
    if machine.lower() in ("arm64", "aarch64"):
        return "ARM64"
    if machine.lower() in ("x86_64", "amd64"):
        return "x64"
    return machine


def detect_environment() -> EnvironmentInfo:
    """Run the full deterministic preflight and return an EnvironmentInfo."""
    arch = _detect_architecture()
    proc_arch = os.environ.get("PROCESSOR_ARCHITECTURE", arch)
    py_ver = sys.version_info[:3]
    python_path = sys.executable if py_ver >= (3, 10) else None

    env = EnvironmentInfo(
        os_name=platform.system() or "unknown",
        os_version=_read_os_version(),
        architecture=arch,
        process_architecture=proc_arch,
        python_available=py_ver >= SUPPORTED_PYTHON,
        python_path=python_path,
        python_version=py_ver,
        ram_mb=_detect_ram_mb(),
        free_disk_mb=_detect_free_disk_mb(Path.cwd()),
        cpu_cores=os.cpu_count() or 0,
        cpu_name=platform.processor() or None,
        powershell_available=shutil.which("powershell") is not None,
        is_admin=_is_admin(),
    )
    gpu, driver, cuda, cuda_ver = _detect_gpu()
    env.gpu_name = gpu
    env.nvidia_driver = driver
    env.cuda_available = cuda
    env.cuda_version = cuda_ver
    env.vc_runtime = _detect_vc_runtime()
    mt5_term, mt5_mod = _detect_mt5()
    env.mt5_available = mt5_term or mt5_mod
    env.mt5_native_module = mt5_mod
    env.network_reachable = _detect_network()
    env.raw = {
        "os": env.os_name,
        "os_version": env.os_version,
        "architecture": env.architecture,
        "process_architecture": env.process_architecture,
        "python": ".".join(map(str, env.python_version)) if env.python_version else None,
        "ram_mb": env.ram_mb,
        "free_disk_mb": env.free_disk_mb,
        "gpu": env.gpu_name,
        "nvidia_driver": env.nvidia_driver,
        "cuda_available": env.cuda_available,
        "cuda_version": env.cuda_version,
        "vc_runtime": env.vc_runtime,
        "mt5_available": env.mt5_available,
        "mt5_native_module": env.mt5_native_module,
        "network_reachable": env.network_reachable,
        "is_admin": env.is_admin,
    }
    return env


def format_hardware_block(info: EnvironmentInfo) -> dict[str, Any]:
    """Hardware/GPU report block used by CLI and installer (section 14)."""
    if info.gpu_name and ("nvidia" in info.gpu_name.lower()):
        mode = "GPU" if info.cuda_available else "CPU (NVIDIA present, CUDA unavailable)"
    elif info.gpu_name:
        mode = "CPU (no NVIDIA/CUDA acceleration)"
    else:
        mode = "CPU"
    return {
        "CPU": f"{info.cpu_name or 'unknown'} ({info.cpu_cores} cores)"
        if info.cpu_cores
        else "unknown",
        "RAM": f"{info.ram_mb} MB" if info.ram_mb else "unknown",
        "GPU": info.gpu_name or "none detected",
        "Driver": info.nvidia_driver or "n/a",
        "CUDA": (info.cuda_version or "available") if info.cuda_available else "unavailable",
        "Mode": mode,
    }
