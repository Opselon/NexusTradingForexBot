"""Shared fixtures for rollback-snapshot tests (BUG-263 trust contract).

BUG-263 made ``RollbackEngine.restore_application`` fail closed: a
``.previous-*`` snapshot is only activated when it carries the release
verification contract the CI pipeline embeds in the installed tree
(``release-manifest.json`` — the BUG-166 pre-stage artifact).  Rollback tests
that predate that change describe a snapshot WITHOUT the contract, which the
gate now (correctly) refuses; these helpers let them satisfy the new
precondition without weakening any assertion.

The helper emits the portable-root ``artifacts`` shape used by production
(``scripts/build/update_helpers.py action_manifest``), so tests exercise the
same manifest reader (``nexus_scalp.release.packaging.manifest_records``) the
real installer path feeds it.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

MANIFEST_NAME = "release-manifest.json"


def seed_release_contract(
    snapshot_dir: Path,
    *,
    skip: tuple[str, ...] = ("artifacts", "data", "logs"),
    version: str | None = None,
    extra_entries: dict[str, str] | None = None,
) -> Path:
    """Write a valid embedded ``release-manifest.json`` over ``snapshot_dir``.

    Hashes every file in the tree except the manifest itself and (by default)
    the preserved user-data dirs — mirroring what the release pipeline embeds
    in the installed portable tree.  Returns the manifest path.
    """
    artifacts: list[dict[str, Any]] = []
    for f in sorted(snapshot_dir.rglob("*")):
        if not f.is_file() or f.name == MANIFEST_NAME:
            continue
        rel = f.relative_to(snapshot_dir).as_posix()
        if rel.split("/", 1)[0] in skip:
            continue
        artifacts.append(
            {
                "name": rel,
                "relative_path": rel,
                "size_bytes": f.stat().st_size,
                "sha256": hashlib.sha256(f.read_bytes()).hexdigest(),
            }
        )
    for rel, digest in (extra_entries or {}).items():
        artifacts.append(
            {
                "name": rel,
                "relative_path": rel,
                "size_bytes": 0,
                "sha256": digest,
            }
        )
    manifest: dict[str, Any] = {"artifacts": artifacts}
    if version is not None:
        manifest["version"] = version
    out = snapshot_dir / MANIFEST_NAME
    out.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return out
