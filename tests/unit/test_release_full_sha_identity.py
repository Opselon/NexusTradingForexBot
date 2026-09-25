"""Finding 2 — full-SHA release identity (release wave).

Every machine-consumed release identity surface must bind to the FULL
40-hex immutable commit SHA:

  * src/nexus_scalp/release/metadata.py:_git_commit() -> full SHA
  * release.yml "Write build-info.json" step -> full SHA + format guard
  * scripts/build/build_release.ps1 -> full SHA + format guard

Short SHAs remain presentation-only in human-facing logs (e.g. beforePush
statements, swarm-log lines) — those are intentionally NOT touched.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from nexus_scalp.release import metadata
from nexus_scalp.release.metadata import _git_commit

REPO = Path(__file__).resolve().parents[2]
FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")


def test_git_commit_returns_full_40_hex_sha() -> None:
    sha = _git_commit("HEAD")
    if sha is None:  # pragma: no cover - not a git repo (packaged envs)
        pytest.skip("no git repository available")
    assert FULL_SHA_RE.match(sha), f"identity must be full 40-hex SHA, got {sha!r}"


def test_git_commit_matches_rev_parse_head() -> None:
    sha = _git_commit("HEAD")
    if sha is None:  # pragma: no cover
        pytest.skip("no git repository available")
    expected = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True, cwd=REPO
    ).stdout.strip()
    assert sha == expected


def test_build_info_step_pins_full_sha() -> None:
    src = (REPO / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "git rev-parse --short" not in src, "release.yml must not stamp short SHAs"
    assert "git rev-parse HEAD" in src, "release.yml must stamp the full SHA"
    assert "RELEASE_IDENTITY_FULL_SHA_REQUIRED" in src, (
        "release.yml must hard-fail when the identity is not a full 40-hex SHA"
    )


def test_build_release_script_pins_full_sha() -> None:
    src = (REPO / "scripts" / "build" / "build_release.ps1").read_text(encoding="utf-8")
    assert "rev-parse --short" not in src
    assert "RELEASE_IDENTITY_FULL_SHA_REQUIRED" in src


def test_release_manifest_carries_build_info_commit_untruncated(tmp_path) -> None:
    """packaging.generate_manifest passes the stamped git_commit through
    verbatim — no consumer may truncate the SHA during verification."""
    import json

    from nexus_scalp.release import packaging

    sha = _git_commit("HEAD") or ("a" * 40)
    base = Path(tmp_path)
    stamped = base / "build-info.json"
    payload = base / "portable" / "NexusScalpEngine.exe"
    payload.parent.mkdir(parents=True, exist_ok=True)
    payload.write_bytes(b"exe")
    stamped.write_text(
        json.dumps(
            {
                "product": "NexusScalpEngine",
                "product_display": "Nexus Trading Forex Bot",
                "version": "9.9.9",
                "git_commit": sha,
                "channel": "stable",
                "platform": "windows",
                "architecture": "x64",
            }
        ),
        encoding="utf-8",
    )
    out = base / "manifests" / "release-manifest.json"
    packaging.generate_manifest([payload], out, base_dir=base)
    manifest = json.loads(out.read_text(encoding="utf-8"))
    assert manifest["git_commit"] == sha, "manifest must carry the FULL SHA untruncated"
    assert FULL_SHA_RE.match(manifest["git_commit"])
    # silence unused-import linters about the metadata import used above
    assert metadata is not None
