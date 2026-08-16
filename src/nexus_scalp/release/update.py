"""UpdateEngine — safe update / rollback planning.

Contract (spec sections 23/24/54):
    * detect current version, map available artifact, verify architecture.
    * download correct artifact, verify SHA-256 before touching anything.
    * install into a NEW versioned directory; keep `current` / `previous`.
    * NEVER overwrite user config, databases, model history or experience
      history (they live under %LOCALAPPDATA%\\NexusScalpEngine, outside the
      installation directory).
    * run post-update health; on failure restore `previous` (rollback).
    * rollback NEVER touches user databases.

The actual transport is delegated to the CLI/build scripts (this module is
fully offline-testable); ``UpdatePlan`` is the deterministic decision core.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .metadata import parse_version


@dataclass
class UpdatePlan:
    current_version: str
    target_version: str
    artifact_name: str | None
    artifact_sha256: str | None
    mirror: str | None
    release_notes_url: str | None
    decisions: list[str] = field(default_factory=list)
    ready: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "current_version": self.current_version,
            "target_version": self.target_version,
            "artifact_name": self.artifact_name,
            "artifact_sha256": self.artifact_sha256,
            "mirror": self.mirror,
            "release_notes_url": self.release_notes_url,
            "decisions": self.decisions,
            "ready": self.ready,
        }


class UpdateEngine:
    """Decides whether/how an update can proceed for this machine."""

    def __init__(self, architecture: str | None = None, channel: str = "stable") -> None:
        self.architecture = architecture or _machine_arch()
        self.channel = channel

    def plan(
        self,
        current_version: str,
        available: dict[str, Any] | None = None,
        allow_prerelease: bool = False,
    ) -> UpdatePlan:
        """Build an UpdatePlan from an (optional) available-release descriptor.

        ``available`` shape (from a release manifest / GitHub API):
            {
              "tag_name": "v9.0.0",
              "assets": [
                 {"name": "NexusScalpEngine-9.0.0-win-x64.zip",
                  "browser_download_url": "...", "digest_sha256": "..."},
                 ...
              ],
              "html_url": "...",
              "prerelease": false,
            }
        """
        plan = UpdatePlan(
            current_version=current_version,
            target_version=current_version,
            artifact_name=None,
            artifact_sha256=None,
            mirror=None,
            release_notes_url=None,
        )
        if not available:
            plan.decisions.append("no available-release descriptor — update not possible")
            return plan

        tag = str(available.get("tag_name", "")).lstrip("v")
        if not tag:
            plan.decisions.append("release descriptor missing tag_name")
            return plan

        if available.get("prerelease") and not allow_prerelease:
            plan.decisions.append(f"{tag} is a pre-release; stable channel refuses it")
            return plan

        cur = parse_version(current_version)
        tgt = parse_version(tag)
        if not cur or not tgt:
            plan.decisions.append(f"cannot compare versions {current_version} vs {tag}")
            return plan
        if tgt <= cur:
            plan.decisions.append(f"already at {current_version}; no newer release ({tag})")
            return plan

        suffix = {"x64": "win-x64", "AMD64": "win-x64", "x86_64": "win-x64",
                  "ARM64": "win-arm64", "arm64": "win-arm64"}.get(self.architecture, "win-x64")
        wished = f"win-x64" if self.architecture in ("x64", "AMD64", "x86_64") else None
        asset = None
        for a in available.get("assets", []):
            name = str(a.get("name", ""))
            if suffix in name or (wished and wished in name):
                asset = a
                break
        if asset is None:
            plan.decisions.append(
                f"no artifact for architecture {self.architecture} on {tag} "
                f"(want {suffix}) — ARM64 remains unsupported by the dependency stack"
            )
            return plan

        plan.target_version = tag
        plan.artifact_name = str(asset.get("name"))
        plan.artifact_sha256 = asset.get("digest_sha256") or asset.get("sha256")
        plan.mirror = asset.get("browser_download_url")
        plan.release_notes_url = available.get("html_url")
        plan.decisions.append(
            f"release {tag} offers {plan.artifact_name} for {self.architecture}"
        )
        if plan.artifact_sha256:
            plan.decisions.append("SHA-256 will be verified before install")
        else:
            plan.decisions.append("WARNING: release lacks a digest — update will refuse")
            return plan
        plan.ready = True
        return plan


def format_update_report(plan: UpdatePlan) -> str:
    try:
        from rich.console import Console
        from rich.table import Table
        from rich.panel import Panel
        from rich import box

        console = Console()
        table = Table(title="Nexus Update", box=box.SIMPLE, show_header=False)
        table.add_column("Key", style="bold cyan")
        table.add_column("Value")
        table.add_row("Installed", plan.current_version)
        table.add_row("Available", plan.target_version if plan.ready else "—")
        table.add_row("Channel", "stable")
        table.add_row("Architecture", "-")
        if plan.artifact_name:
            table.add_row("Artifact", plan.artifact_name)
        if plan.mirror:
            table.add_row("Mirror", plan.mirror)
        for d in plan.decisions:
            table.add_row("·", d)
        console.print(Panel(table, border_style="cyan"))
    except Exception:
        import sys as _sys

        print(f"Installed: {plan.current_version}")
        print(f"Available: {plan.target_version if plan.ready else '—'}")
        for d in plan.decisions:
            print(f"- {d}")
        _sys.stdout.flush()
    return plan.to_dict() if False else ""


def _machine_arch() -> str:
    import platform

    m = platform.machine().lower()
    if m in ("arm64", "aarch64"):
        return "ARM64"
    if m in ("x86_64", "amd64"):
        return "x64"
    return m or "unknown"


def load_available_releases(manifest_path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(data, list) and data:
        return data[0]
    if isinstance(data, dict):
        return data
    return None