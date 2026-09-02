"""Release-build helper actions (PowerShell-invoked, no quoting fragility).

The PowerShell build scripts previously inlined multi-line python with heavy
single/double-quote nesting, which PowerShell parses as a syntax error
(pre-existing: `build_release.ps1` failed to parse under both Windows
PowerShell 5.1 and pwsh 7 — the token-guard regex and the `-c` heredocs
contained apostrophes/`from` keywords inside PS string literals, BUG-090).

This module gives the build script ONE stable entrypoint:

    python update_helpers.py <action> [args...]

actions:
    token-guard            exit 1 if configs/live.yaml carries a real token
    scan-tree              exit 1 if the staged tree contains secret shapes
    manifest               generate release-manifest.json (+ portable-rooted
                           embedded copy for the payload zip)
    sbom                   generate sbom.spdx.json
    verify                 full release-tree self-check (EXE launch, assets,
                           checksums, secrets, identity)

All actions take plain path arguments — no quoting, no reserved keywords.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SECRET_PATTERNS = [
    re.compile(r"(?i)api[_-]?key\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{12,}"),
    re.compile(r"""(?ix)
        bot[_-]?token\s*[=:]\s*['"]
        \d{6,}:([A-Za-z0-9_\-]{25,})['"]
    """),
    re.compile(r"(?i)begin (rsa |ec |openssh )?private key"),
]

TOKEN_RE = re.compile(r"(?i)bot[_-]?token\s*[=:]\s*['\"]?\d{6,}:[A-Za-z0-9_\-]{25,}")


def action_token_guard(args: list[str]) -> int:
    """Refuse to build when configs/live.yaml carries a REAL bot token."""
    cfg = Path(args[0]) / "configs" / "live.yaml"
    if not cfg.exists():
        print("token-guard: configs/live.yaml absent — nothing to guard")
        return 0
    text = cfg.read_text(encoding="utf-8", errors="replace")
    if TOKEN_RE.search(text):
        print("token-guard: real telegram token in configs/live.yaml — mask it first")
        return 1
    print("token-guard: no real telegram token in configs/live.yaml")
    return 0


def action_scan_tree(args: list[str]) -> int:
    """Scan a staged tree for secret-shaped strings (best effort)."""
    root = Path(args[0])
    if not root.exists():
        print("scan-tree: root missing — skipped")
        return 1
    hits: list[str] = []
    scanned = 0
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() in (
            ".pyc",
            ".dll",
            ".exe",
            ".pyd",
            ".pt",
            ".bin",
            ".db",
            ".zip",
            ".7z",
        ):
            continue
        if p.stat().st_size > 2 * 1024 * 1024:
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        scanned += 1
        for pat in SECRET_PATTERNS:
            m = pat.search(text)
            if m:
                hits.append(f"{p.name}: {m.group(0)[:40]}")
                break
    if hits:
        print("scan-tree FAILED:")
        for h in hits[:8]:
            print(f"  {h}")
        return 1
    print(f"scan-tree: clean ({scanned} files)")
    return 0


def action_manifest(args: list[str]) -> int:
    """Generate release-manifest.json + embedded (portable-rooted) copy."""
    out_dir = Path(args[0])
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from nexus_scalp.release import packaging as p

    artifacts = (
        list(out_dir.glob("portable/*.exe"))
        + list(out_dir.glob("cli/*.exe"))
        + list(out_dir.glob("*.zip"))
        + list(out_dir.glob("*-setup.exe"))
    )
    manifest = out_dir / "manifests" / "release-manifest.json"
    manifest.parent.mkdir(parents=True, exist_ok=True)
    p.generate_manifest(artifacts, manifest, channel="stable", base_dir=out_dir)
    # The EMBEDDED copy (inside the portable zip) is rooted at the PORTABLE
    # root and lists ONLY the files actually shipped in the payload — the
    # release-root manifest references cli/*.exe and *.zip that live OUTSIDE
    # the portable tree (real contract bug found during payload verification).
    portable_root = out_dir / "portable"
    portable_manifest = portable_root / "release-manifest.json"
    base_meta = json.loads(manifest.read_text(encoding="utf-8"))
    payload_files = sorted(
        f for f in portable_root.rglob("*") if f.is_file() and f.name != "release-manifest.json"
    )
    embedded_arts = [
        {
            "name": f.relative_to(portable_root).as_posix(),
            "relative_path": f.relative_to(portable_root).as_posix(),
            "size_bytes": f.stat().st_size,
            "sha256": p.sha256_file(f),
        }
        for f in payload_files
    ]
    embedded = {
        "product": base_meta.get("product"),
        "product_display": base_meta.get("product_display"),
        "version": base_meta.get("version"),
        "git_commit": base_meta.get("git_commit"),
        "channel": base_meta.get("channel"),
        "platform": base_meta.get("platform"),
        "architecture": base_meta.get("architecture"),
        "build_mode": base_meta.get("build_mode"),
        "minimum_supported_version": base_meta.get("minimum_supported_version"),
        "migration_required_from": base_meta.get("migration_required_from"),
        "database_schema": base_meta.get("database_schema"),
        "config_schema": base_meta.get("config_schema"),
        "model_runtime_schema": base_meta.get("model_runtime_schema"),
        "artifacts": embedded_arts,
    }
    portable_manifest.write_text(json.dumps(embedded, indent=2), encoding="utf-8")
    print(
        f"manifest: {len(artifacts)} artifacts -> {manifest} (embedded: {len(payload_files)} files)"
    )
    return 0


def action_sbom(args: list[str]) -> int:
    out_dir = Path(args[0])
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from nexus_scalp.release import packaging as p

    (out_dir / "sbom").mkdir(parents=True, exist_ok=True)
    p.generate_sbom(out=out_dir / "sbom" / "sbom.spdx.json")
    print("sbom written")
    return 0


def action_verify(args: list[str]) -> int:
    """Full release-tree self-check (EXE launch + assets + checksums + secrets)."""
    root = Path(args[0])
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from nexus_scalp.release import verify as v

    res = v.verify_release(root)
    print("OVERALL:", res["overall"])
    for c in res["checks"]:
        print(f"{c['status']:5} {c['check']} — {c['detail'][:90]}")
    return 0 if res["valid"] else 1


ACTIONS = {
    "token-guard": action_token_guard,
    "scan-tree": action_scan_tree,
    "manifest": action_manifest,
    "sbom": action_sbom,
    "verify": action_verify,
}


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in ACTIONS:
        print(f"usage: python {Path(argv[0]).name} <{'|'.join(ACTIONS)}> [args...]")
        return 2
    try:
        return int(ACTIONS[argv[1]](argv[2:]) or 0)
    except Exception as e:  # pragma: no cover - defensive
        print(f"error: {e}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
