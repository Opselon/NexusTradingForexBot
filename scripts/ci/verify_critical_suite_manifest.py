"""CI manifest preflight: every resolved path in tests/critical_suite.txt must exist.

Companion to the user CI-forensics steer section 13: a missing/renamed file in
the manifest must surface as an explicit CONFIGURATION ERROR here (fast, in
the preflight step) instead of as a mysterious pytest collection error later.

CI wiring lands in ci.yml right before the pytest step; this module is
import-safe so the preflight never breaks collection itself.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST = REPO_ROOT / "tests" / "critical_suite.txt"


def parse_manifest(path: Path | None = None) -> list[str]:
    """Reads the manifest: strips comments (#...) and blank lines, keeps order.

    Mirrors the CI bash parser exactly (line split at first '#', then trim) so
    this preflight can never disagree with what CI would pass to pytest.
    """
    p = Path(path) if path else MANIFEST
    if not p.exists():
        raise FileNotFoundError(f"critical suite manifest missing: {p}")
    out: list[str] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def missing_paths(path: Path | None = None, *, root: Path | None = None) -> list[str]:
    """Resolved manifest entries that do not exist on disk (configuration error).

    ``root`` defaults to the repo this script lives in; check_local.py passes
    its own REPO_ROOT so a gate copied into a temp tree validates THAT tree.
    """
    base = root if root is not None else REPO_ROOT
    missing = []
    for entry in parse_manifest(path):
        if not (base / entry).exists():
            missing.append(entry)
    return missing


def main(root: Path | None = None) -> int:
    manifest = (root if root is not None else REPO_ROOT) / "tests" / "critical_suite.txt"
    missing = missing_paths(manifest, root=root)
    if missing:
        print("CRITICAL_SUITE_MANIFEST_ERROR: missing test paths:")
        for m in missing:
            print(f"  - {m}")
        return 1
    n = len(parse_manifest(manifest))
    print(f"CRITICAL_SUITE_MANIFEST_OK: {n} paths all exist")
    return 0


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Validate tests/critical_suite.txt paths")
    ap.add_argument("--root", default=None, help="repo root to resolve paths against")
    ns = ap.parse_args()
    root = Path(ns.root) if ns.root else None
    raise SystemExit(main(root=root))
