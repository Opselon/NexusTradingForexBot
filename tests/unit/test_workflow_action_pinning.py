"""Finding 4 — workflow action-pinning policy (release wave).

Every third-party GitHub Action reference in .github/workflows/*.yml must be
pinned to a full 40-hex immutable commit SHA (a trailing "# vX.Y.Z" comment
is allowed and is informational only). Floating tags (@v4, @main, @latest),
branch names and full-length annotations without a SHA are rejected.

Reusable-workflow references (jobs.uses with .github/workflows/...) are
third-party supply-chain surface too and must equally be SHA-pinned.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WORKFLOWS = REPO / ".github" / "workflows"
FULL_SHA = re.compile(r"^[0-9a-f]{40}$")

#: local/reusable references that are IN-REPO (not third-party). The repo
#: currently has none; anything registered here must stay inside this repo.
LOCAL_ALLOWED: tuple[str, ...] = ()


def _uses_refs(text: str) -> list[tuple[int, str, str]]:
    refs = []
    for i, line in enumerate(text.splitlines(), 1):
        m = re.search(r"uses:\s*(\S+)\s*(?:#\s*(.*))?$", line.strip())
        if not m:
            continue
        target = m.group(1).strip()
        comment = (m.group(2) or "").strip()
        refs.append((i, target, comment))
    return refs


def test_all_workflows_pin_third_party_actions_to_full_sha() -> None:
    violations: list[str] = []
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        for lineno, target, _comment in _uses_refs(wf.read_text(encoding="utf-8")):
            if target in LOCAL_ALLOWED:
                continue
            if "@/" in target or "@" not in target:
                violations.append(f"{wf.name}:{lineno}: malformed ref {target!r}")
                continue
            ref = target.rsplit("@", 1)[1]
            if not FULL_SHA.match(ref):
                violations.append(
                    f"{wf.name}:{lineno}: third-party action not SHA-pinned: {target!r}"
                )
    assert not violations, "floating action references found:\n" + "\n".join(violations)


def test_no_floating_mutable_refs_anywhere() -> None:
    """Explicit rejection of the exact classes named in the policy."""
    banned = re.compile(r"uses:\s*\S+@(v\d+(\.\d+)*|main|master|latest)\s*$", re.M)
    offenders = []
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        text = wf.read_text(encoding="utf-8")
        for i, line in enumerate(text.splitlines(), 1):
            # a SHA pin with a version comment is fine; a bare version ref is not
            if banned.search(line + " ") or banned.search(line):
                if not re.search(r"@[0-9a-f]{40}\b", line):
                    offenders.append(f"{wf.name}:{i}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


def test_version_comments_do_not_hide_floating_refs() -> None:
    """ "uses: x@v4 # some note" must fail; "uses: x@<sha> # v4" must pass."""
    for wf in sorted(WORKFLOWS.glob("*.yml")):
        for lineno, target, _c in _uses_refs(wf.read_text(encoding="utf-8")):
            if target in LOCAL_ALLOWED:
                continue
            ref = target.rsplit("@", 1)[1]
            assert FULL_SHA.match(ref), (
                f"{wf.name}:{lineno}: {target!r} — the comment is not authoritative; "
                "the ref itself must be a full SHA"
            )


def test_release_and_docker_and_security_lanes_pinned() -> None:
    """Security-sensitive lanes keep their hard SHA pins (they already had
    them — this test prevents regression)."""
    for name in ("release.yml", "docker.yml", "security.yml"):
        text = (WORKFLOWS / name).read_text(encoding="utf-8")
        refs = _uses_refs(text)
        assert refs, f"{name} has no uses: references"
        for _lineno, target, _c in refs:
            ref = target.rsplit("@", 1)[1]
            assert FULL_SHA.match(ref), f"{name}: {target!r} not SHA-pinned"
