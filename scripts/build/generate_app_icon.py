"""Generate the canonical Windows application icon ``NexusScalpEngine.ico``.

WHY (EU-RELEASE-001, end-user release lane): the PyInstaller builds in
``scripts/build/build_release.ps1`` and ``scripts/release/build_artifact.py``
never passed ``--icon``, so both shipped executables (``NexusScalpEngine.exe``
and ``NexusScalpEngine-CLI.exe``) carried PyInstaller's generic placeholder
icon. The Inno Setup script points ``UninstallDisplayIcon`` at the installed
EXE, so the placeholder also leaked into the Start Menu / uninstall entry.
Every real desktop application surfaces its own branded icon on the taskbar,
the file explorer, the Start Menu and the shortcut; this script produces it.

SOURCE OF TRUTH: ``frontend/public/icon-512.png`` — the canonical brand asset
already used by the PWA manifest, the Apple touch icon and the OG card. No
new art is introduced; this is format conversion to the Windows container.

OUTPUT: ``installer/NexusScalpEngine.ico`` (multi-resolution: 256, 128, 64,
48, 32, 16) next to ``NexusScalpEngine.iss`` so both build scripts resolve it
by a stable repo-relative path.

BUILD-TIME ONLY: Pillow is a release-build dependency, never a runtime one.
Invoked by ``scripts/build/build_release.ps1``; also runnable directly:

    python scripts/build/generate_app_icon.py

Idempotent: re-running over an existing identical source rewrites the same
bytes (PIL's ICO encoder is deterministic for a fixed source + size list).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

SOURCE = REPO / "frontend" / "public" / "icon-512.png"
OUTPUT = REPO / "installer" / "NexusScalpEngine.ico"

# Windows ICO sizes: 256 (Vista+ large tiles / taskbar on hi-dpi), 128, 64,
# 48 (classic desktop), 32 (Alt-Tab / list view), 16 (small icons / title bar).
# A single 256 entry would be scaled by the shell; shipping the real sizes
# keeps every surface crisp.
SIZES = (256, 128, 64, 48, 32, 16)


def build_icon(source: Path = SOURCE, output: Path = OUTPUT) -> Path:
    try:
        from PIL import Image
    except ImportError as exc:  # pragma: no cover - build-time tooling
        raise SystemExit(
            "Pillow is required to build the application icon "
            "(build-time only, never a runtime dependency): "
            "uv pip install pillow  /  pip install pillow"
        ) from exc

    if not source.exists():
        raise SystemExit(f"canonical brand asset missing: {source}")
    image = Image.open(source).convert("RGBA")
    if image.size != (512, 512):
        image = image.resize((512, 512), Image.LANCZOS)

    output.parent.mkdir(parents=True, exist_ok=True)
    # embed=True keeps the largest entry PNG-compressed (the ICO container
    # supports PNG from Vista on); smaller entries use lossless BMP. The
    # file is valid for Windows 7+ and every icon-picking tool.
    image.save(output, format="ICO", sizes=[(s, s) for s in SIZES], embed=True)
    print(f"[icon] {output.name}: {len(SIZES)} resolutions from {source.name}")
    for size in SIZES:
        print(f"       {size}x{size}")
    return output


if __name__ == "__main__":
    out = build_icon()
    print(f"[icon] wrote {out} ({out.stat().st_size} bytes)")
    sys.exit(0)
