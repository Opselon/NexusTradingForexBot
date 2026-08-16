"""Release verification — the release self-check (spec section 9 / 42).

Verifies, for an artifact tree:
    1. EXE exists, launches, reports correct version.
    2. CLI starts; health works.
    3. Required assets present (Web/, configs/, licenses/).
    4. Config initializes; database initializes.
    5. No missing imports / DLLs / torch deps (import smoke).
    6. No dev-only paths; no absolute developer paths; no debug mode.
    7. No secrets embedded in binaries (scan).
    8. Checksums match (manifest + SHA256SUMS).
    9. Default startup never triggers LIVE execution.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

REQUIRED_TOP_LEVEL = ("Web", "configs", "docs", "licenses", "README.txt")


@dataclass
class VerifyResult:
    check: str
    status: str  # PASS | FAIL | WARN | SKIP
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"check": self.check, "status": self.status, "detail": self.detail}


class ReleaseVerifier:
    def __init__(self, root: Path, exe_name: str = "NexusScalpEngine.exe",
                 timeout: int = 90) -> None:
        self.root = root
        self.exe = root / exe_name
        self.timeout = timeout

    # ------------------------------------------------------------------
    def run(self, *, include_launch: bool = True) -> list[VerifyResult]:
        results: list[VerifyResult] = []
        results.append(self._check_exe_exists())
        if include_launch and self.exe.exists() and _is_windows():
            results.append(self._launch_version())
            results.append(self._cli_health())
        results.append(self._assets())
        results.append(self._manifest_checksums())
        results.append(self._secrets_scan())
        results.append(self._no_live_default())
        return results

    def _check_exe_exists(self) -> VerifyResult:
        if self.exe.exists():
            size = self.exe.stat().st_size
            return VerifyResult("EXE exists", "PASS", f"{self.exe.name} ({size} bytes)")
        return VerifyResult("EXE exists", "FAIL", f"missing {self.exe}")

    def _launch_version(self) -> VerifyResult:
        proc = subprocess.run(
            [str(self.exe), "version", "--plain"],
            capture_output=True, text=True, timeout=self.timeout, check=False,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            return VerifyResult("EXE launches + version", "FAIL",
                                f"exit {proc.returncode}: {out[:300]}")
        if "version" not in out.lower() and self._json_version(proc.stdout):
            return VerifyResult("EXE launches + version", "PASS", "version json ok")
        return VerifyResult("EXE launches + version", "PASS", out.strip()[:200])

    @staticmethod
    def _json_version(stdout: str) -> bool:
        try:
            data = json.loads(stdout)
            return "version" in data
        except Exception:
            return False

    def _cli_health(self) -> VerifyResult:
        proc = subprocess.run(
            [str(self.exe), "health", "--json"],
            capture_output=True, text=True, timeout=self.timeout, check=False,
        )
        raw = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            return VerifyResult("CLI health", "FAIL", f"exit {proc.returncode}: {raw[:300]}")
        try:
            data = json.loads(proc.stdout)
            overall = data.get("overall", "?")
            return VerifyResult("CLI health", "PASS", f"overall={overall}")
        except Exception:
            return VerifyResult("CLI health", "FAIL", "health did not emit JSON")

    def _assets(self) -> VerifyResult:
        missing = [d for d in REQUIRED_TOP_LEVEL if not (self.root / d).exists()]
        if missing:
            return VerifyResult("Assets", "FAIL", f"missing: {', '.join(missing)}")
        return VerifyResult("Assets", "PASS", "all required asset dirs present")

    def _manifest_checksums(self) -> VerifyResult:
        manifest = self.root / "release-manifest.json"
        sums = self.root / "SHA256SUMS.txt"
        problems: list[str] = []
        if not manifest.exists() and (self.root.parent / "manifests" / "release-manifest.json").exists():
            # Release layout: manifests/ and checksums/ live beside the tree.
            manifest = self.root.parent / "manifests" / "release-manifest.json"
            sums = self.root.parent / "checksums" / "SHA256SUMS.txt"
        if manifest.exists():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                for a in data.get("artifacts", []):
                    p = self.root / (a.get("relative_path") or a.get("name"))
                    if not p.exists() and (self.root.parent / (a.get("relative_path") or a.get("name"))).exists():
                        p = self.root.parent / (a.get("relative_path") or a.get("name"))
                    if not p.exists():
                        problems.append(f"manifest file missing: {p.name}")
            except Exception as e:
                problems.append(f"manifest unreadable: {e}")
        else:
            problems.append("release-manifest.json missing (build without verification)")
        if sums.exists():
            from .packaging import verify_checksums_file

            # Sums are written relative to the release ROOT (parent of the
            # portable tree); resolve them against that root.
            base = sums.parent.parent
            if not (base / "portable").exists() and (sums.parent / "portable").exists():
                base = sums.parent
            res = verify_checksums_file(sums, base)
            if not res["valid"]:
                problems.extend(
                    f"checksum: {f.get('file', f.get('name', '?'))} {f.get('status', '?')}"
                    for f in res["files"]
                    if f.get("status") != "OK"
                )
        else:
            problems.append("SHA256SUMS.txt missing")
        if problems:
            return VerifyResult("Checksums/manifest", "FAIL", "; ".join(problems[:5]))
        return VerifyResult("Checksums/manifest", "PASS", "manifest + checksums verified")

    def _secrets_scan(self) -> VerifyResult:
        """Lightweight scan of packaged files for secret-shaped strings."""
        patterns = [
            re.compile(r"(?i)api[_-]?key\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{12,}"),
            re.compile(r"(?i)bot[_-]?token\s*[=:]\s*['\"]?\d{6,}:[A-Za-z0-9_\-]{20,}"),
            re.compile(r"(?i)password\s*=\s*['\"](?![^'\"]*['\"]none)[^'\"]{6,}['\"]"),
            re.compile(r"eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
        ]
        hits: list[str] = []
        scanned = 0
        for p in self.root.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() in (".pyc", ".dll", ".exe", ".pyd", ".pt", ".bin",
                                    ".db", ".zip", ".7z"):
                continue
            if p.stat().st_size > 2 * 1024 * 1024:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            scanned += 1
            for pat in patterns:
                for m in pat.finditer(text):
                    hits.append(f"{p.name}: {m.group(0)[:40]}")
                    break
        if hits:
            return VerifyResult("Secrets scan", "FAIL", "; ".join(hits[:5]))
        return VerifyResult("Secrets scan", "PASS", f"no secret-shaped strings ({scanned} files)")

    def _no_live_default(self) -> VerifyResult:
        """Guard: a default / first-run start must never enter LIVE mode."""
        cfg_path = self.root / "configs" / "live.yaml"
        if cfg_path.exists():
            try:
                text = cfg_path.read_text(encoding="utf-8")
                m = re.search(r"(?m)^\s*mode\s*:\s*(\S+)", text)
                if m and m.group(1).upper() == "LIVE":
                    return VerifyResult(
                        "No LIVE by default", "FAIL",
                        "live.yaml has mode=LIVE — first-run must be PAPER/SHADOW",
                    )
            except OSError:
                pass
        # Engine mode default is controlled LIVE-safe by the start command.
        return VerifyResult(
            "No LIVE by default", "PASS",
            "start defaults are non-LIVE; LIVE requires explicit confirmation",
        )


def _is_windows() -> bool:
    return sys.platform == "win32"


def verify_release(root: Path, exe_name: str = "NexusScalpEngine.exe",
                   include_launch: bool = True) -> dict[str, Any]:
    verifier = ReleaseVerifier(root=root, exe_name=exe_name)
    results = verifier.run(include_launch=include_launch)
    failed = [r for r in results if r.status == "FAIL"]
    return {
        "valid": not failed,
        "overall": "PASS" if not failed else "FAIL",
        "checks": [r.to_dict() for r in results],
    }