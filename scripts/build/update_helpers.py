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

# CONSTANT-NAME SUPPRESSION (precision fix, FAIL-CLOSED).
#
# `SECRET_ENV_API_KEY` (holding the name NSE_GATEWAY_API_KEY) and
# `DEFAULT_API_KEY` (holding the placeholder name default_local_key) in
# src/nexus_scalp/gateway/server.py are constant declarations whose VALUE is a
# name (an env-var / placeholder name), not leaked credentials. Those inherited
# base lines turned the release secrets gate red on a pristine base (the gate
# had never been run end-to-end before this wave).
#
# Note the matcher cannot be judged on its own text: api[_-]?key matches the
# MID-identifier suffix of SECRET_ENV_API_KEY, so the match text already begins
# at API_KEY — its own left side is truncated. The suppression is therefore
# verified against the FULL SOURCE LINE that contains the match — never against
# the match fragment alone. (Documentation examples below are written without a
# literal `KEY = "quoted"` shape on purpose: this scanner is run against
# scripts/ too, and a comment that mimics a secret would trip its own gate.)
#
# Suppression rules (all must hold, verified on that one line):
#   1. the line parses as an exact `SCREAMING_CONST = <name>` assignment —
#      if the shape cannot be parsed (no `=`, junk around it, a private-key
#      header), the match is REPORTED: default is report, never drop;
#   2. the left side is a SCREAMING_CASE constant (a module-level constant
#      declaration, not a lowercase config key such as `api_key = ...`, which
#      is where real credentials are assigned);
#   3. the value is a plain name (identifier, quotes optional, no spaces);
#   4. the value ENDS WITH the same trailing NAME as the constant — so an
#      all-caps high-entropy token that merely *looks* like SCREAMING_CASE
#      (an API_KEY constant holding an own-secret-shaped token) still fails.
# Any real secret therefore still fires; only a value that is verifiably the
# same NAME as the constant it is assigned to is skipped.
_CONSTANT_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]*_[A-Z0-9_]*$")
_CONST_LINE_RE = re.compile(
    r"""(?x)
    ^
    (?P<lhs>[A-Z][A-Z0-9_]*_[A-Z0-9_]*)
    \s*[=:]\s*
    (?P<q>['"]?)
    (?P<rhs>[A-Za-z0-9_][A-Za-z0-9_\-]*)
    (?P=q)
    $
    """
)


def _looks_like_a_constant_name(value: str) -> bool:
    """True only for screaming-case snake identifiers (a NAME, not a secret)."""
    return bool(value) and bool(_CONSTANT_NAME_RE.match(value))


def _trailing_name(identifier: str) -> str:
    return identifier.rsplit("_", 1)[-1].upper()


def _is_self_named_constant(line: str) -> bool:
    """True ONLY for a verified ``NAME = NAME``-shaped constant declaration.

    Both sides are parsed from the SAME source line; anything that does not
    parse returns False, which in action_scan_tree means REPORT.
    """
    m = _CONST_LINE_RE.match(line.strip())
    if m is None:
        return False
    # value must end with the same NAME as the constant (rule 4 above)
    return _trailing_name(m.group("rhs")) == _trailing_name(m.group("lhs"))


def _is_suppressible(text: str, match: re.Match[str]) -> bool:
    """Report by default; suppress only a verified constant declaration."""
    line_start = text.rfind("\n", 0, match.start()) + 1
    line_end = text.find("\n", match.end())
    if line_end == -1:
        line_end = len(text)
    return _is_self_named_constant(text[line_start:line_end])


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
            for m in pat.finditer(text):
                # Default: REPORT. A match is suppressed only for a verified
                # constant declaration (exact `NAME = NAME` shape on this line);
                # anything unparseable still hits.
                if _is_suppressible(text, m):
                    continue
                hits.append(f"{p.name}: {m.group(0)[:40]}")
                break
            else:
                continue
            break
    if hits:
        print("scan-tree FAILED:")
        for h in hits[:8]:
            print(f"  {h}")
        return 1
    print(f"scan-tree: clean ({scanned} files)")
    return 0


def action_manifest(args: list[str]) -> int:
    """Generate release-manifest.json + embedded (portable-rooted) copy.

    CONTRACT frozen decision #11: the packaged frontend bundle is recorded
    in the manifest payload so a tampered/rolled-back Control Center is
    detectable (hash exists but the file is absent, or the file exists with
    a different sha256).
    """
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
    embedded["frontend_bundle"] = _frontend_bundle_record(portable_root)
    portable_manifest.write_text(json.dumps(embedded, indent=2), encoding="utf-8")
    print(
        f"manifest: {len(artifacts)} artifacts -> {manifest} (embedded: {len(payload_files)} files)"
    )
    return 0


def _stamped_frontend_hash(portable_root: Path) -> str | None:
    """``frontend_index_hash`` from the staged build-info.json (CONTRACT #11)."""
    for candidate in (
        portable_root / "build-info.json",
        portable_root / "_internal" / "build-info.json",
    ):
        if not candidate.is_file():
            continue
        try:
            value = json.loads(candidate.read_text(encoding="utf-8")).get("frontend_index_hash")
        except (OSError, ValueError):
            continue
        if value:
            return str(value)
    return None


def _frontend_bundle_record(portable_root: Path) -> dict:
    """Record the shipped Control Center bundle (CONTRACT #11).

    ``frontend_index_hash`` is stamped into build-info.json by the release
    build; the packaged entry mirrors it so a verifier can tell "the bundle
    the build described" from "the bundle that actually landed". Missing
    bundle, missing index, or a hash mismatch is reported explicitly rather
    than being silently absent from the manifest.

    The hash is read from the STAGED build-info.json (not from the
    release-root manifest), because generate_manifest copies only identity
    fields and never carried the frontend hash.
    """
    record: dict[str, object] = {"relative_path": "frontend/dist"}
    index = portable_root / "_internal" / "frontend" / "dist" / "index.html"
    if index.is_file():
        bundled = [f for f in index.parent.rglob("*") if f.is_file()]
        record["index_present"] = True
        record["index_sha256"] = _sha256(index)
        record["dist_size_bytes"] = sum(f.stat().st_size for f in bundled)
        record["dist_file_count"] = len(bundled)
    else:
        record["index_present"] = False
    expected = _stamped_frontend_hash(portable_root)
    if expected:
        record["expected_index_sha256"] = expected
        if record.get("index_sha256"):
            record["index_sha256_matches_build_info"] = (
                str(record["index_sha256"]).lower() == expected.lower()
            )
    return record


def _sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


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
