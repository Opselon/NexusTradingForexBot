"""Packaging helpers — release manifest, SHA-256 checksums, SBOM.

Used by the build scripts and by ``nexus verify-release`` to generate and
verify machine-readable release metadata, and by the CLI tests.
"""

from __future__ import annotations
import contextlib

import hashlib
import json
import platform
import posixpath
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from .metadata import get_version, get_version_info

#: Canonical name of the release manifest that the build pipeline embeds in the
#: portable/installed tree (BUG-166 pre-stage, ``release.yml`` "Embed release
#: manifest in portable bundle", ``scripts/build/update_helpers.py manifest``).
#: Every consumer of the embedded trust contract resolves this one name.
MANIFEST_FILE_NAME = "release-manifest.json"

#: Sub-directory a CI-staged (non-embedded) tree keeps the manifest in.
MANIFEST_SUBDIR = "manifests"

_SHA256_HEX_CHARS = frozenset("0123456789abcdefABCDEF")


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while block := f.read(chunk_size):
            h.update(block)
    return h.hexdigest()


def checksums_file(paths: list[Path], out: Path, *, base_dir: Path | None = None) -> Path:
    """Write SHA256SUMS.txt (path relative to base_dir)."""
    base_dir = base_dir or out.parent
    lines: list[str] = []
    for p in paths:
        try:
            rel = p.relative_to(base_dir).as_posix()
        except ValueError:
            rel = p.name
        lines.append(f"{sha256_file(p)}  {rel}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(sorted(lines)) + "\n", encoding="utf-8")
    return out


def verify_checksums_file(sums_file: Path, base_dir: Path | None = None) -> dict[str, Any]:
    """Verify a SHA256SUMS.txt against its referenced files."""
    base_dir = base_dir or sums_file.parent
    results: list[dict[str, Any]] = []
    ok = True
    for line in sums_file.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        parts = line.split("  ", 1)
        if len(parts) != 2:
            results.append({"line": line, "status": "MALFORMED"})
            ok = False
            continue
        expect, rel = parts
        f = (base_dir / rel).resolve()
        if not f.exists():
            results.append({"file": rel, "status": "MISSING"})
            ok = False
            continue
        actual = sha256_file(f)
        match = actual.lower() == expect.lower()
        results.append({"file": rel, "status": "OK" if match else "MISMATCH"})
        ok = ok and match
    return {"valid": ok, "files": results}


def _manifest_feature_schema(info: dict[str, Any]) -> str:
    """Canonical active feature schema id (registry-derived, brief 37).

    Precedence: stamped build-info feature_schema -> active schema id in
    the registry -> scalar fallback.  Never invent a schema id.
    """
    stamped = str(info.get("feature_schema") or "").strip()
    if stamped:
        try:
            from nexus_scalp.features.schema import FEATURE_SCHEMAS

            FEATURE_SCHEMAS.resolve(stamped)
            return stamped
        except Exception:
            pass
    from nexus_scalp.features.schema import ACTIVE_SCHEMA_ID

    return ACTIVE_SCHEMA_ID


def _manifest_feature_dimension(info: dict[str, Any]) -> int:
    """Registered dimension of the manifest feature schema (0 when unknown)."""
    sid = _manifest_feature_schema(info)
    try:
        from nexus_scalp.features.schema import FEATURE_SCHEMAS

        return FEATURE_SCHEMAS.resolve(sid).dimension
    except Exception:
        return 0


def _manifest_supported_model_schemas() -> list[str]:
    """Every REGISTERED schema id — the set of model schemas this release
    can load without conversion (brief 37: scalp_v1..scalp_v4 all included)."""
    try:
        from nexus_scalp.features.schema import FEATURE_SCHEMAS

        return sorted(s.schema_id for s in FEATURE_SCHEMAS.list_schemas())
    except Exception:
        return ["scalp_v1"]


def _manifest_db_schema_version(info: dict[str, Any]) -> int:
    """Highest expected schema version across managed DB domains.

    Falls back to the stamped build-info (db_schema_version) when the
    migration registry is not importable in the build environment.
    """
    try:
        from nexus_scalp.database.models import DatabaseDomain
        from nexus_scalp.database.registry import expected_version_for_domain

        return max(
            expected_version_for_domain(d)
            for d in (
                DatabaseDomain.AUDIT,
                DatabaseDomain.NEWS,
                DatabaseDomain.CANDLE_INTEL,
            )
        )
    except Exception:
        return int(info.get("db_schema_version") or 0)


def _manifest_required_migrations() -> list[str]:
    """All migration ids the release carries (ordered, brief 37)."""
    try:
        from nexus_scalp.database.models import DatabaseDomain
        from nexus_scalp.database.registry import all_migration_ids

        out: list[str] = []
        for d in (
            DatabaseDomain.AUDIT,
            DatabaseDomain.NEWS,
            DatabaseDomain.CANDLE_INTEL,
        ):
            out.extend(all_migration_ids(d))
        return out
    except Exception:
        return []


def _manifest_model_compatibility(info: dict[str, Any]) -> str:
    """Human-readable model compatibility line (never hardcoded)."""
    schemas = _manifest_supported_model_schemas()
    return " / ".join(f"{s} ({_schema_dim(s)}D)" for s in schemas) or "none"


def _schema_dim(schema_id: str) -> int:
    try:
        from nexus_scalp.features.schema import FEATURE_SCHEMAS

        return FEATURE_SCHEMAS.resolve(schema_id).dimension
    except Exception:
        return 0


def generate_manifest(
    artifacts: list[Path],
    out: Path,
    *,
    channel: str = "stable",
    build_mode: str = "Release",
    installer_version: str = "1.0.0",
    base_dir: Path | None = None,
) -> Path:
    """Write release-manifest.json for a set of artifacts.

    Architecture/channel/version come from the canonical build identity
    (build-info.json when present, else runtime platform) so the manifest
    never disagrees with the packaged bundle.
    """
    info = get_version_info()
    base_dir = base_dir or out.parent
    # Prefer the STAMPED build-info.json at the release root (the canonical
    # build identity for this exact artifact set) over runtime introspection,
    # so the manifest never disagrees with the packaged bundle.
    stamped = base_dir / "portable" / "build-info.json"
    if not stamped.exists():
        stamped = base_dir / "build-info.json"
    if stamped.exists():
        with contextlib.suppress(Exception):
            info = {**info, **json.loads(stamped.read_text(encoding="utf-8"))}
    manifest: dict[str, Any] = {
        "product": info["product"],
        "product_display": info["product_display"],
        "version": info.get("version") or get_version(),
        "git_commit": info.get("git_commit") or info.get("commit"),
        # CHG-0043: idempotent truth — emit the timestamp ONLY when recorded.
        # A datetime.now() fallback fabricated a fresh value per call (breaking
        # manifest idempotency) and lied about the actual build time.
        **({"build_timestamp": info["build_timestamp"]} if info.get("build_timestamp") else {}),
        "channel": channel or info.get("channel") or "stable",
        "platform": "windows",
        "architecture": info.get("architecture") or platform.machine(),
        "build_mode": build_mode or info.get("build_mode") or "Release",
        "python_compatibility": "3.11.x",
        # Schema coverage (TASK-9): feature_schema derives from the CANONICAL
        # registry (never hardcoded) — a future 70D release cannot silently
        # drift the manifest; web_bundle_version + supported_model_schemas +
        # db_schema_version + required_migrations make the manifest the
        # release contract (brief section 37).
        "feature_schema": _manifest_feature_schema(info),
        "feature_schema_dimension": _manifest_feature_dimension(info),
        "supported_model_schemas": _manifest_supported_model_schemas(),
        "web_bundle_version": str(info.get("web_bundle_version") or ""),
        "db_schema_version": _manifest_db_schema_version(info),
        "required_migrations": _manifest_required_migrations(),
        "model_compatibility": _manifest_model_compatibility(info),
        "installer_version": installer_version,
        "build_environment": {
            "os": platform.platform(),
            "python": platform.python_version(),
            "machine": platform.machine(),
        },
        "test_status": "not_run",
        "verification_status": "not_run",
        "artifacts": [
            {
                "name": a.name,
                "relative_path": a.relative_to(base_dir).as_posix() if base_dir else a.name,
                "size_bytes": a.stat().st_size,
                "sha256": sha256_file(a),
            }
            for a in artifacts
            if a.exists()
        ],
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return out


#: The embedded copy shipped inside the portable/installed tree is rooted at
#: that tree, while the CI release-root manifest records release-root-relative
#: paths (``portable/...``, ``cli/...``, the payload zip). The BUG-160
#: embedded-layout remap (``release.verify``) maps the leading payload segment
#: onto the install root; every reader of the contract must agree on it.
_PAYLOAD_ROOT_SEGMENTS = ("portable/", "cli/")

#: SHA-256 digests are 64 hex characters; a record claiming anything else is
#: malformed and must fail closed (never be coerced into a comparable value).
_SHA256_HEX_LENGTH = 64


def manifest_records(data: Any) -> list[dict[str, str]] | None:
    """Canonical logical entry view of a parsed ``release-manifest.json``.

    ONE source of truth for the manifest contract shared by every consumer —
    ``verify-release`` (``release.verify.ReleaseVerifier._manifest_checksums``),
    the in-payload gate (``UpdateOrchestrator._verify_payload_manifest``), the
    post-install ``embedded_manifest`` check and the BUG-263 restore-time
    snapshot re-verification. The pipeline writes the entry list in two
    equivalent shapes:

    * ``artifacts`` records — ``generate_manifest`` /
      ``update_helpers.action_manifest``: ``[{name, relative_path, size_bytes,
      sha256}, ...]``
    * ``files`` mapping — portable-root embedded variant, same path spelling
      ``release.yml``'s generate_manifest step produces: ``{rel: sha256}``

    Returns ``[{"rel", "sha256"}, ...]`` (empty list = an empty entry set,
    which every caller must treat as malformed) or ``None`` when no valid
    entry list exists. A record's own ``relative_path``/``name`` fallback is
    honoured exactly like :func:`verify_manifest` has always done; malformed
    records drop the WHOLE manifest (fail-closed) instead of silently
    skipping entries, so a tampered manifest cannot hide files by carrying a
    broken sibling entry.
    """
    if not isinstance(data, Mapping):
        return None
    entries: list[dict[str, str]] = []
    seen: set[str] = set()

    def _add(rel: Any, digest: Any) -> bool:
        if not isinstance(rel, str) or not rel:
            return False
        if not isinstance(digest, str) or not digest:
            return False
        key = rel.replace("\\", "/")
        if key in seen:
            return False
        seen.add(key)
        entries.append({"rel": rel, "sha256": digest})
        return True

    raw_artifacts = data.get("artifacts")
    if raw_artifacts is not None:
        if not isinstance(raw_artifacts, list):
            return None
        for record in raw_artifacts:
            if not isinstance(record, Mapping):
                return None
            rel = record.get("relative_path") or record.get("name")
            if not _add(rel, record.get("sha256")):
                return None
        return entries
    files = data.get("files")
    if files is not None:
        if not isinstance(files, Mapping):
            return None
        for rel, digest in files.items():
            if not _add(rel, digest):
                return None
        return entries
    return None


def manifest_rel_candidates(rel: str) -> list[str]:
    """Path spellings of one manifest record (BUG-160 embedded-layout remap).

    Release-root manifests record payload paths as ``portable/<file>`` /
    ``cli/<file>``; inside an installed/embedded tree that leading segment IS
    the tree root, so the stripped spelling must resolve too. Verbatim first,
    then the stripped spellings.
    """
    unified = str(rel).replace("\\", "/")
    candidates = [unified]
    for segment in _PAYLOAD_ROOT_SEGMENTS:
        if unified.startswith(segment):
            candidates.append(unified[len(segment) :])
    return candidates


def resolve_manifest_entry(rel: str, base_dir: Path) -> Path | None:
    """Resolve one manifest record inside ``base_dir``, fail-closed.

    Returns ``None`` when the recorded path escapes ``base_dir`` (traversal,
    absolute paths, drive letters), when none of its spellings exists, or when
    the candidate cannot be built at all. Callers distinguish MISSING from
    ESCAPE with :func:`manifest_entry_escape_reason`, which applies the same
    guard independently.
    """
    matches = resolve_manifest_entries(rel, base_dir)
    return matches[0] if matches else None


def resolve_manifest_entries(rel: str, base_dir: Path) -> list[Path]:
    """EVERY existing spelling of one manifest record, inside ``base_dir``.

    The BUG-160 embedded layout means a release-root record
    (``portable/NexusScalpEngine.exe``) legitimately names the install-root
    file ``NexusScalpEngine.exe``; a tree can therefore hold BOTH spellings.
    Verification must then check the claim against both: accepting only the
    first hit would let an attacker plant a matching ``portable/x`` shadow
    while the ``x`` that actually gets restored stays tampered.  Paths that
    escape the tree are never returned.
    """
    if manifest_entry_escape_reason(rel) is not None:
        return []
    try:
        root = base_dir.resolve()
    except OSError:
        root = base_dir
    found: list[Path] = []
    for spelling in manifest_rel_candidates(rel):
        try:
            candidate = (root / spelling).resolve()
        except OSError:
            continue
        if not candidate.is_relative_to(root):
            continue
        if candidate.is_file() and candidate not in found:
            found.append(candidate)
    return found


def manifest_entry_escape_reason(rel: str) -> str | None:
    """Why a recorded path cannot be trusted to live inside the tree (or None).

    Backslash spellings are normalised first (the pipeline writes manifest keys
    with forward slashes — ``relative_to(...).as_posix()`` /
    ``replace("\\\\", "/")`` — and Windows-shaped keys are still Windows-shaped
    paths). Absolute paths, drive letters, UNC roots and any ``..`` segment that
    climbs out of the tree are rejected.
    """
    unified = str(rel).replace("\\", "/")
    windows = PureWindowsPath(unified)
    if windows.is_absolute() or windows.drive or unified.startswith("/"):
        return "ABSOLUTE_PATH"
    normalized = posixpath.normpath(unified)
    if normalized == ".." or normalized.startswith("../"):
        return "TRAVERSAL"
    if any(part == ".." for part in PurePosixPath(unified).parts):
        return "TRAVERSAL"
    return None


#: Release-root package spellings that live OUTSIDE the portable/installed
#: tree by design (the CI embed step copies the release-root manifest into the
#: payload, where the CLI exe / payload zip / setup exe are not present).
#: Their absence says nothing about the payload tree's integrity; their
#: presence still forces a hash match.
_EXTERNAL_PACKAGE_SUFFIXES = (".zip", "-setup.exe")


def manifest_entry_is_external(rel: str) -> bool:
    """True when a release-root manifest record can never name an app-tree file.

    ``cli/...`` records and top-level release packages (``*.zip``,
    ``*-setup.exe``) are written by :func:`generate_manifest` with
    release-root-relative paths; an installed/portable tree (and therefore a
    rollback snapshot of one) legitimately does not contain them.  This is a
    layout judgement only — it NEVER exempts an entry from hash verification
    when the file IS present in the tree.
    """
    unified = str(rel).replace("\\", "/")
    if unified.startswith("portable/"):
        return False
    if unified.startswith("cli/"):
        return True
    if "/" in unified:
        return False
    lowered = unified.lower()
    return lowered.endswith(_EXTERNAL_PACKAGE_SUFFIXES)


def manifest_identity_conflict(manifest_data: Any, tree_root: Path) -> str | None:
    """Why the manifest contradicts the tree's own build identity (or None).

    The same identity cross-check ``verify-release`` performs
    (``ReleaseVerifier._identity_check``) applied to a snapshot: a manifest
    copy-pasted from a DIFFERENT release is a forgery signal even when every
    listed hash matches its (equally swapped) files.  Only fields BOTH sides
    declare are compared — a missing field is never fabricated into a match,
    and it never becomes a mismatch either.
    """
    if not isinstance(manifest_data, Mapping):
        return None
    build_info = tree_root / "build-info.json"
    if not build_info.is_file():
        build_info = tree_root / "_internal" / "build-info.json"
    if not build_info.is_file():
        return None
    try:
        info = json.loads(build_info.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    if not isinstance(info, dict):
        return None
    for key in ("version", "git_commit"):
        declared = str(manifest_data.get(key) or "").strip()
        stamped = str(info.get(key) or "").strip()
        if declared and stamped:
            if declared.lstrip("v").lower() != stamped.lstrip("v").lower():
                return f"{key}: manifest={declared[:40]} build-info={stamped[:40]}"
    return None


def locate_embedded_manifest(tree_root: Path) -> Path | None:
    """The release manifest embedded in an installed/portable tree, if any.

    Same candidate order ``release.verify`` uses: the portable-root copy the
    release pipeline embeds (``release.yml`` "Embed release manifest in portable
    bundle" / ``build_release.ps1``), then the CI-staged ``manifests/`` spelling.
    """
    candidates = (
        tree_root / MANIFEST_FILE_NAME,
        tree_root / MANIFEST_SUBDIR / MANIFEST_FILE_NAME,
    )
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def verify_manifest(manifest_path: Path, base_dir: Path | None = None) -> dict[str, Any]:
    """Verify a manifest: every listed artifact exists and matches its hash.

    Entry parsing goes through :func:`manifest_records` (the single contract
    reader), so the release-root ``artifacts`` shape and the portable-root
    ``files`` shape are both honoured without forking the verification logic.
    Recorded paths are resolved with the BUG-160 embedded-layout remap and are
    refused when they escape ``base_dir`` (BUG-263: the same guard the
    restore-time snapshot gate applies).
    """
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {"valid": False, "error": str(e)}
    base_dir = base_dir or manifest_path.parent
    records = manifest_records(data)
    if records is None:
        return {"valid": False, "error": "MANIFEST_MALFORMED", "files": [], "manifest": data}
    results: list[dict[str, Any]] = []
    ok = True
    for record in records:
        rel = record["rel"]
        matches = resolve_manifest_entries(rel, base_dir)
        if not matches:
            results.append({"name": rel, "status": "MISSING"})
            ok = False
            continue
        expected = str(record["sha256"]).lower()
        # EVERY in-tree spelling must match the claim (anti-shadowing: a
        # planted portable/x twin must not vouch for a tampered x).
        match = all(sha256_file(m).lower() == expected for m in matches)
        results.append({"name": rel, "status": "OK" if match else "MISMATCH"})
        ok = ok and match
    return {"valid": ok and bool(results), "files": results, "manifest": data}


def _entry_in_prefixes(rel: str, prefixes: tuple[str, ...]) -> bool:
    """True when a manifest record names a path under a runtime (user-data)
    directory that rollback deliberately does NOT restore.

    ``ApplicationInstaller`` preserves ``artifacts/``, ``data/`` and ``logs/``
    out of the snapshot (version-aware rollback keeps the NEWER user data in
    place), and the release payload may legitimately ship defaults for them.
    Their absence from the snapshot therefore proves nothing about integrity —
    but nothing under these prefixes is ever copied back either, so no
    unverified byte can reach the live tree.  Presence still forces a hash
    match.
    """
    if not prefixes:
        return False
    unified = str(rel).replace("\\", "/")
    if unified.startswith("portable/"):
        unified = unified[len("portable/") :]
    parts = unified.split("/")
    return bool(parts) and parts[0] in prefixes


def verify_snapshot_integrity(
    snapshot_dir: Path,
    manifest_path: Path | None = None,
    not_restored_prefixes: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Re-verify a rollback snapshot's embedded release contract, fail-closed.

    BUG-263: a ``.previous-*`` snapshot is UNTRUSTED at restore time — the live
    tree it protects may have been modified by malware, a mistaken rsync or
    disk corruption, and restoring it without verification turns rollback into
    an integrity bypass around the whole update trust chain (Ed25519 signed
    manifest -> payload SHA-256 -> in-payload ``release-manifest.json``).

    Policy (never weaker than :func:`verify_manifest`, and it REUSES
    ``verify_manifest`` for the hash pass — one source of truth):

    * the snapshot must carry ``release-manifest.json`` (the artifact CI embeds
      via the BUG-166 pre-stage / release pipeline), parse as JSON, and declare
      a non-empty entry set;
    * every recorded path must be traversal-free and resolve INSIDE the
      snapshot (structural pass over ALL entries completes BEFORE any file is
      hashed, so a bad entry can never be half-processed);
    * every listed file must exist and its recomputed SHA-256 must match the
      manifest claim — digests are recomputed from the snapshot bytes, never
      copied, defaulted or fabricated;
    * a ``SHA256SUMS.txt`` embedded next to the manifest (the other BUG-166
      contract file) must agree with the bytes on disk;
    * the manifest must not contradict the tree's own ``build-info.json``
      identity (forgery signal, same fields verify-release compares).

    Pure read: it never writes, moves, or creates anything. Reasons:
    ``MANIFEST_MISSING`` | ``MANIFEST_UNREADABLE`` | ``MANIFEST_MALFORMED`` |
    ``MANIFEST_TRAVERSAL`` | ``FILE_MISSING`` | ``HASH_MISMATCH`` |
    ``SUMS_MISMATCH`` | ``IDENTITY_MISMATCH``.
    """
    result: dict[str, Any] = {
        "valid": False,
        "reason": "",
        "problems": [],
        "entries": 0,
        "verified_files": 0,
        "verified_bytes": 0,
    }
    if manifest_path is None:
        manifest_path = locate_embedded_manifest(snapshot_dir)
    if manifest_path is None or not manifest_path.is_file():
        result["reason"] = "MANIFEST_MISSING"
        return result
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, ValueError):
        result["reason"] = "MANIFEST_UNREADABLE"
        return result
    records = manifest_records(data)
    if records is None or not records:
        result["reason"] = "MANIFEST_MALFORMED"
        return result

    # Phase 1 — structure: reject traversal/absolute/undigestable claims before
    # any hash is consulted, and never half-process the entry set.
    problems: list[str] = []
    for record in records:
        rel = record["rel"]
        escape = manifest_entry_escape_reason(rel)
        if escape is not None:
            problems.append(f"ESCAPE {escape}: {rel}")
            continue
        digest = str(record["sha256"])
        if len(digest) != _SHA256_HEX_LENGTH or any(ch not in _SHA256_HEX_CHARS for ch in digest):
            problems.append(f"MALFORMED_DIGEST {rel}: {digest[:20]!r}")
    if problems:
        result["reason"] = (
            "MANIFEST_TRAVERSAL"
            if any(p.startswith("ESCAPE") for p in problems)
            else "MANIFEST_MALFORMED"
        )
        result["problems"] = problems[:8]
        result["entries"] = len(records)
        return result

    # Phase 2 — content: every entry exists and re-hashes to the claim, via
    # the canonical contract reader (BUG-160 path spellings included).  The
    # embedded production manifest is a COPY of the release-root contract
    # (release.yml "Embed release manifest in portable bundle" / the Inno
    # {app} embed), so it also lists release packages that legitimately never
    # land inside the app tree — ``cli/...``, the payload ``*.zip``, the
    # ``*-setup.exe``.  Their ABSENCE is a layout fact, not an integrity
    # signal, and is tolerated (and reported); their PRESENCE always forces a
    # hash match.  Every app-tree entry (anything under ``portable/`` or a
    # nested relative path) must exist and match.
    verified = verify_manifest(manifest_path, snapshot_dir)
    result["entries"] = len(records)
    external_absent: list[str] = []
    failures: list[str] = []
    for item in verified.get("files", []):
        status = str(item.get("status"))
        name = str(item.get("name"))
        if status == "OK":
            continue
        if status == "MISSING" and (
            manifest_entry_is_external(name) or _entry_in_prefixes(name, not_restored_prefixes)
        ):
            external_absent.append(name)
            continue
        failures.append(f"{name}: {status}")
    if failures:
        result["reason"] = (
            "FILE_MISSING" if any(f.endswith("MISSING") for f in failures) else "HASH_MISMATCH"
        )
        result["problems"] = failures[:8]
        return result
    result["external_absent"] = external_absent

    # Phase 3 — the other BUG-166 contract file: an embedded SHA256SUMS.txt
    # must not contradict the bytes on disk.  Verified through the canonical
    # sums reader (:func:`verify_checksums_file`) so the line format is parsed
    # in exactly one place.  A sums line whose path does not exist in THIS tree
    # is a layout fact (release-root spellings such as ``portable/...``,
    # ``cli/...`` and the payload zip live outside an app tree) — reported,
    # never a pass; a line contradicted by bytes that ARE in the tree is a
    # tamper signal and refuses the snapshot.
    sums_path = snapshot_dir / "SHA256SUMS.txt"
    if not sums_path.is_file():
        staged_sums = snapshot_dir / "checksums" / "SHA256SUMS.txt"
        if staged_sums.is_file():
            sums_path = staged_sums
    if sums_path.is_file():
        sums = verify_checksums_file(sums_path, snapshot_dir)
        bad = [
            f"{item.get('file', '?')}: {item.get('status')}"
            for item in sums.get("files", [])
            if item.get("status") not in ("OK", "MISSING")
        ]
        if bad:
            result["reason"] = "SUMS_MISMATCH"
            result["problems"] = bad[:8]
            return result
        result["sums_absent"] = [
            str(item.get("file"))
            for item in sums.get("files", [])
            if item.get("status") == "MISSING"
        ]

    identity = manifest_identity_conflict(data, snapshot_dir)
    if identity is not None:
        result["reason"] = "IDENTITY_MISMATCH"
        result["problems"] = [identity]
        return result

    total_bytes = 0
    for record in records:
        f = resolve_manifest_entry(record["rel"], snapshot_dir)
        if f is not None:
            with contextlib.suppress(OSError):
                total_bytes += f.stat().st_size
    result["valid"] = True
    result["reason"] = "OK"
    result["verified_files"] = len(records)
    result["verified_bytes"] = total_bytes
    return result


def generate_sbom(
    dependencies: dict[str, str] | None = None, out: Path | None = None
) -> dict[str, Any]:
    """SPDX-lite SBOM (dependency inventory). Not a security guarantee."""
    deps = dependencies or _installed_versions()
    sbom: dict[str, Any] = {
        "bomFormat": "SPDX",
        "spdxVersion": "SPDX-2.3",
        "name": f"nexus-scalp-engine-{get_version()}",
        "created": datetime.now(UTC).isoformat(),
        "packages": [{"name": name, "versionInfo": ver} for name, ver in sorted(deps.items())],
    }
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(sbom, indent=2), encoding="utf-8")
    return sbom


def _installed_versions() -> dict[str, str]:
    out: dict[str, str] = {}
    for mod in (
        "pydantic",
        "pydantic_settings",
        "yaml",
        "structlog",
        "typer",
        "rich",
        "polars",
        "pyarrow",
        "numpy",
        "torch",
        "fastapi",
        "uvicorn",
        "httpx",
        "pytest",
        "ruff",
        "mypy",
    ):
        try:
            m = __import__(mod)
            out[mod] = str(getattr(m, "__version__", "unknown"))
        except Exception:
            continue
    return out
