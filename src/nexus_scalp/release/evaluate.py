"""Deterministic preflight requirement evaluation.

Each requirement returns one of:
    PASS     — requirement met.
    WARNING  — soft shortfall (machine still runnable, degraded).
    BLOCKED  — hard failure (installation/startup must stop with a clear
               error; NEVER install blindly).
    UNKNOWN  — not determinable on this host (reported, never fatal).

The evaluator never throws: a detection failure degrades to UNKNOWN so the
caller can decide policy (installer: proceed with WARNING/UNKNOWN unless a
BLOCKED requirement exists; engine start: only the critical subset gates).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .environment import (
    MIN_FREE_DISK_MB,
    MIN_RAM_MB,
    RECOMMENDED_FREE_DISK_MB,
    RECOMMENDED_RAM_MB,
    EnvironmentInfo,
)

Verdict = str  # "PASS" | "WARNING" | "BLOCKED" | "UNKNOWN"


@dataclass
class RequirementResult:
    name: str
    verdict: Verdict
    detail: str
    suggestion: str = ""

    def to_row(self) -> dict[str, str]:
        return {"name": self.name, "verdict": self.verdict, "detail": self.detail,
                "suggestion": self.suggestion}


def evaluate_requirements(env: EnvironmentInfo) -> list[RequirementResult]:
    """Evaluate the full requirement set for this machine."""
    results: list[RequirementResult] = []
    results.append(_os(env))
    results.append(_architecture(env))
    results.append(_python(env))
    results.append(_ram(env))
    results.append(_disk(env))
    results.append(_vc_runtime(env))
    results.append(_mt5(env))
    results.append(_gpu(env))
    results.append(_network(env))
    results.append(_privileges(env))
    results.append(_powershell(env))
    return results


def overall_verdict(results: list[RequirementResult]) -> tuple[Verdict, list[str]]:
    """Highest-severity verdict + human summary of what must be fixed first."""
    blocked = [r for r in results if r.verdict == "BLOCKED"]
    warnings = [r for r in results if r.verdict == "WARNING"]
    unknowns = [r for r in results if r.verdict == "UNKNOWN"]
    verdict: Verdict = "PASS"
    if blocked:
        verdict = "BLOCKED"
    elif warnings:
        verdict = "WARNING"
    elif unknowns:
        verdict = "UNKNOWN"
    lines = []
    if blocked:
        lines.append("BLOCKED requirements: " + ", ".join(r.name for r in blocked))
    if warnings:
        lines.append("WARNING requirements: " + ", ".join(r.name for r in warnings))
    return verdict, lines


def _os(env: EnvironmentInfo) -> RequirementResult:
    name = env.os_name.lower()
    if "windows" in name:
        return RequirementResult("OS", "PASS", f"Windows {env.os_version}")
    if name in ("linux",):
        return RequirementResult(
            "OS",
            "WARNING",
            "Linux is a developer/container target (remote-gateway mode).",
            "Use a Windows host for the packaged release.",
        )
    return RequirementResult("OS", "BLOCKED", f"Unsupported OS: {env.os_name}",
                             "Nexus release targets Windows.")


def _architecture(env: EnvironmentInfo) -> RequirementResult:
    arch = (env.architecture or "").lower()
    proc = (env.process_architecture or "").lower()
    if arch in ("x64", "amd64", "x86_64") or proc in ("amd64", "x86_64"):
        return RequirementResult("Architecture", "PASS", f"{env.architecture}")
    if arch in ("arm64", "aarch64"):
        return RequirementResult(
            "Architecture",
            "BLOCKED",
            "ARM64 is unsupported by the current dependency stack "
            "(PyTorch / Polars / MetaTrader5 ship no Windows ARM64 wheels).",
            "Run on a Windows x64 machine.",
        )
    return RequirementResult("Architecture", "BLOCKED", f"Unknown architecture {env.architecture}",
                             "Windows x64 required.")


def _python(env: EnvironmentInfo) -> RequirementResult:
    if not env.python_available:
        return RequirementResult(
            "Python",
            env.python_path is None and "UNKNOWN" or "WARNING",
            f"Python {'unavailable' if not env.python_path else 'too old'}: "
            f"{'.'.join(map(str, env.python_version)) if env.python_version else 'none detected'}",
            "Packaged releases bundle Python via PyInstaller; source/dev installs need Python 3.11.",
        )
    return RequirementResult("Python", "PASS",
                             f"Python {'.'.join(map(str, env.python_version))} @ {env.python_path}")


def _ram(env: EnvironmentInfo) -> RequirementResult:
    if not env.ram_mb:
        return RequirementResult("RAM", "UNKNOWN", "RAM could not be determined")
    if env.ram_mb >= RECOMMENDED_RAM_MB:
        return RequirementResult("RAM", "PASS", f"{env.ram_mb} MB")
    if env.ram_mb >= MIN_RAM_MB:
        return RequirementResult(
            "RAM", "WARNING", f"{env.ram_mb} MB (recommended >= {RECOMMENDED_RAM_MB} MB)",
            "Close heavy applications while trading.",
        )
    return RequirementResult(
        "RAM", "BLOCKED", f"{env.ram_mb} MB (minimum {MIN_RAM_MB} MB)",
        "Upgrade RAM to run the engine reliably.",
    )


def _disk(env: EnvironmentInfo) -> RequirementResult:
    if not env.free_disk_mb:
        return RequirementResult("Disk", "UNKNOWN", "Free disk could not be determined")
    if env.free_disk_mb >= RECOMMENDED_FREE_DISK_MB:
        return RequirementResult("Disk", "PASS", f"{env.free_disk_mb} MB free")
    if env.free_disk_mb >= MIN_FREE_DISK_MB:
        return RequirementResult(
            "Disk", "WARNING", f"{env.free_disk_mb} MB free (recommended >= {RECOMMENDED_FREE_DISK_MB} MB)",
            "Free up disk space.",
        )
    return RequirementResult(
        "Disk", "BLOCKED", f"{env.free_disk_mb} MB free (minimum {MIN_FREE_DISK_MB} MB)",
        "Free up disk space before installing.",
    )


def _vc_runtime(env: EnvironmentInfo) -> RequirementResult:
    if env.os_name.lower() == "linux":
        return RequirementResult("VC Runtime", "PASS", "n/a (non-Windows)")
    if env.vc_runtime:
        return RequirementResult("VC Runtime", "PASS", env.vc_runtime)
    # Bundled PyInstaller apps carry their own VC runtime ucrt usually, so a
    # missing registered redistributable is a WARNING, not BLOCKED.
    return RequirementResult(
        "VC Runtime",
        "WARNING",
        "Visual C++ 2015-2022 x64 redistributable not detected (or not queryable).",
        "Install vc_redist.x64.exe if the packaged app fails to start.",
    )


def _mt5(env: EnvironmentInfo) -> RequirementResult:
    if env.mt5_available:
        detail = []
        if env.mt5_native_module:
            detail.append("native module available")
        if env.os_name.lower() == "windows":
            detail.append("terminal detected")
        return RequirementResult("MT5", "PASS", " | ".join(detail) or "available")
    return RequirementResult(
        "MT5",
        "WARNING",
        "MetaTrader 5 terminal not detected (required only for LIVE execution).",
        "Install MT5 and run in PAPER/SHADOW mode until then.",
    )


def _gpu(env: EnvironmentInfo) -> RequirementResult:
    if env.cuda_available:
        return RequirementResult("GPU/CUDA", "PASS",
                                 f"{env.gpu_name or 'CUDA GPU'} (CUDA {env.cuda_version or '?'})")
    if env.gpu_name and "nvidia" in env.gpu_name.lower():
        return RequirementResult(
            "GPU/CUDA",
            "WARNING",
            f"NVIDIA GPU {env.gpu_name} without usable CUDA (driver {env.nvidia_driver or '?'}).",
            "Update the NVIDIA driver — or continue in CPU mode (safe default).",
        )
    return RequirementResult(
        "GPU/CUDA",
        "PASS",
        (env.gpu_name or "no discrete GPU") + " — CPU mode (safe default).",
    )


def _network(env: EnvironmentInfo) -> RequirementResult:
    if env.network_reachable is True:
        return RequirementResult("Network", "PASS", "Outbound HTTPS OK")
    if env.network_reachable is False:
        return RequirementResult(
            "Network", "WARNING", "No outbound connectivity detected",
            "Local/paper features still work; news feeds and updates need internet.",
        )
    return RequirementResult("Network", "UNKNOWN", "Connectivity could not be determined")


def _privileges(env: EnvironmentInfo) -> RequirementResult:
    if env.os_name.lower() != "windows":
        return RequirementResult("Privileges", "PASS", "n/a")
    if env.is_admin:
        return RequirementResult(
            "Privileges", "WARNING", "Running as Administrator",
            "Not required; per-user install is recommended.",
        )
    return RequirementResult("Privileges", "PASS", "Standard user (per-user install)")


def _powershell(env: EnvironmentInfo) -> RequirementResult:
    if env.os_name.lower() != "windows":
        return RequirementResult("PowerShell", "PASS", "n/a")
    if env.powershell_available:
        return RequirementResult("PowerShell", "PASS", "available")
    return RequirementResult(
        "PowerShell", "WARNING", "PowerShell not found",
        "Used by maintenance scripts; the engine itself does not require it.",
    )


def requirements_json(env: EnvironmentInfo) -> dict[str, Any]:
    results = evaluate_requirements(env)
    verdict, lines = overall_verdict(results)
    return {
        "verdict": verdict,
        "lines": lines,
        "requirements": [r.to_row() for r in results],
    }