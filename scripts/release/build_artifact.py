"""Build the onefile CLI release artifact fresh at the CURRENT HEAD.

This is the actual release artifact used for black-box release acceptance:
PyInstaller onefile CLI (NexusScalpEngine-CLI.exe) with a FRESH build-info.json
stamped at the current pyproject version + git commit (the canonical build
identity contract from scripts/build/build_release.ps1, replicated minimally
here because the Inno Setup piece (ISCC) is absent on this machine - the
onefile CLI is the artifact we can exercise end-to-end).

The build-info.json stamp is restored afterwards (it is a tracked file; the
stale 9.0.3 stamp must NOT be committed over).
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PY = REPO / ".venv" / "Scripts" / "python.exe"
PYINSTALLER = REPO / ".venv" / "Scripts" / "pyinstaller.exe"
BUILD_DIR = REPO / "release" / "build" / "windows-x64"
OUT = BUILD_DIR / "onefile" / "NexusScalpEngine-CLI.exe"
BUILD_INFO = REPO / "build-info.json"


def git(*a: str) -> str:
    return subprocess.run(
        ["git", *a], cwd=str(REPO), capture_output=True, text=True, check=True
    ).stdout.strip()


def main() -> int:
    version = re.search(
        r'^version = "([^"]+)"', (REPO / "pyproject.toml").read_text(encoding="utf-8"), re.M
    )
    assert version, "pyproject version missing"
    version = version.group(1)
    commit = git("rev-parse", "--short", "HEAD")
    dirty = bool(git("status", "--porcelain"))
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    original_stamp = BUILD_INFO.read_bytes()
    try:
        fresh = {
            "product": "NexusScalpEngine",
            "version": version,
            "git_commit": commit,
            "dirty_tree": dirty,
            "build_timestamp": stamp,
            "platform": "windows",
            "architecture": "x64",
            "python": "3.11",
            "channel": "stable",
            "build_mode": "Release",
            "feature_schema": "scalp_v3",
            "installer_version": "1.0.0",
        }
        BUILD_INFO.write_text(json.dumps(fresh, indent=2), encoding="utf-8")
        print(f"build-info stamped: v{version} @ {commit} (dirty={dirty})")

        r = subprocess.run(
            [
                str(PYINSTALLER),
                "--noconfirm",
                "--clean",
                "--onefile",
                "--name",
                "NexusScalpEngine-CLI",
                # Stamped build identity travels INSIDE the onefile payload
                # (sys._MEIPASS lookup, BUG-174 contract). Without this the
                # artifact reports NOT_RECORDED - found by release acceptance.
                "--add-data",
                f"{BUILD_INFO};.",
                "--exclude-module",
                "torch",
                "--exclude-module",
                "polars",
                "--exclude-module",
                "numpy",
                "--exclude-module",
                "pyarrow",
                "--exclude-module",
                "MetaTrader5",
                "--distpath",
                str(BUILD_DIR / "onefile"),
                "--workpath",
                str(BUILD_DIR / "work-cli"),
                "--specpath",
                str(BUILD_DIR),
                str(REPO / "src" / "nexus_scalp" / "release" / "cli_shim.py"),
            ],
            check=False,
            cwd=str(REPO),
            capture_output=True,
            text=True,
            timeout=1200,
            encoding="utf-8",
            errors="replace",
        )
        if r.returncode != 0 or not OUT.exists():
            print("BUILD FAILED rc=", r.returncode)
            print(r.stdout[-800:])
            print(r.stderr[-800:])
            return 1
        sha = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(Get-FileHash -Algorithm SHA256 '{OUT.as_posix()}').Hash.ToLower()",
            ],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        # provenance next to the artifact (release contract)
        (BUILD_DIR / "onefile" / "artifact-provenance.json").write_text(
            json.dumps(
                {
                    "artifact": OUT.name,
                    "version": version,
                    "commit": commit,
                    "dirty_tree": dirty,
                    "built_at": stamp,
                    "sha256": sha,
                    "size_bytes": OUT.stat().st_size,
                    "builder": "scripts/release/build_artifact.py",
                },
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"ARTIFACT OK: {OUT.name} v{version} @ {commit} sha256={sha[:16]}")
        return 0
    finally:
        BUILD_INFO.write_bytes(original_stamp)
        print("build-info.json restored to tracked state")


if __name__ == "__main__":
    sys.exit(main())
