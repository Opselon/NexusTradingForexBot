"""Extracted from release/updater.py (update-package split); behavior preserved."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


class PostUpdateHealth:
    """Post-install health gate: the new application must answer health."""

    def __init__(
        self, app_root: Path, exe_name: str = "NexusScalpEngine.exe", timeout: int = 90
    ) -> None:
        self.exe = app_root / exe_name
        self.timeout = timeout

    def run(self) -> dict[str, Any]:
        if not self.exe.exists():
            return {"overall": "FAIL", "checks": [], "error": "executable missing after install"}
        try:
            proc = subprocess.run(
                [str(self.exe), "health", "--json"],
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
            if proc.returncode != 0:
                return {
                    "overall": "FAIL",
                    "checks": [],
                    "error": f"health exit {proc.returncode}",
                }
            data = json.loads(proc.stdout)
            return {"overall": data.get("overall", "FAIL"), "checks": data.get("checks", [])}
        except Exception as e:
            return {"overall": "FAIL", "checks": [], "error": str(e)}


