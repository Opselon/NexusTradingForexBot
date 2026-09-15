"""BUG-286 class guard: shell-hook sentinel files must never be tracked.

Provenance (three incidents, one class):
  * 94ecf2c7 tracked ``the`` + ``min_samples_to_reject`` (reached main via
    the #199 squash; removed by cb5dd80c / PR #205).
  * 701bc50c (PR #204 head) tracked ``forwarded`` + ``non-finite`` — this
    time produced by a *commit-message* payload containing ``<`` eaten by a
    cmd-shell redirect hook. Caught only because the branch went CI-red for
    an unrelated reason.
All five were ZERO-BYTE files at the REPO ROOT with IDENTIFIER-SHAPED names:
words swept out of ``grep foo <file``-style command lines by Windows cmd
redirect parsing, then committed by ``git add -A``.

This is the structural guard the BUG-286 ledger entry asked for: a pinned,
critical-suite-gated assertion that the worktree tracks no such file, so the
next ``git add -A`` incident fails CI at the PR that introduces it instead of
reaching main. Read-only against the tree (git ls-files + stat) — no cleanup
side effects, no assumptions about who is running.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Identifier-shaped: starts letter/underscore, only word chars + hyphen,
#: NO dot (a dot means it is an extension-bearing file like main.py or
#: .gitignore-style dotfiles, which are never cmd-hook redirect artifacts).
_IDENTIFIER_SHAPE = re.compile(r"^[A-Za-z_][A-Za-z0-9_-]*$")

#: Names the class has ALREADY shipped with (regression memory: the ignore
#: guard for these must stay in .gitignore, see test_known_offenders_ignored).
KNOWN_OFFENDERS = ("the", "min_samples_to_reject", "forwarded", "non-finite")


def is_sentinel_shape(rel_path: str, size_bytes: int) -> bool:
    """Pure predicate: root-level, identifier-shaped, zero-byte = sentinel."""
    if size_bytes != 0:
        return False
    if "/" in rel_path:  # root-level only: nested paths are deliberate
        return False
    return bool(_IDENTIFIER_SHAPE.match(rel_path))


def _tracked_files_with_sizes(root: Path) -> list[tuple[str, int]]:
    """(root-relative path, size) for every tracked file, via git plumbing."""
    listing = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    out: list[tuple[str, int]] = []
    for rel in filter(None, listing.split("\0")):
        p = root / rel
        try:
            out.append((rel, p.stat().st_size))
        except OSError:  # deleted-but-tracked edge case: not a sentinel
            continue
    return out


# ---------------------------------------------------------------------------
# 1. the predicate itself (RED shapes are the exact five that shipped)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", KNOWN_OFFENDERS)
def test_known_sentinel_shapes_are_flagged(name: str) -> None:
    assert is_sentinel_shape(name, 0), f"class regression: '{name}' no longer flagged"


@pytest.mark.parametrize(
    ("path", "size"),
    [
        ("main.py", 0),  # dot => extension file, deliberate
        ("docs/the", 0),  # nested => not a root redirect artifact
        ("Dockerfile", 1065),  # identifier-shaped but NON-zero => content
        ("tests/unit/__init__.py", 0),  # legit zero-byte, nested + dotted
        (".gitignore", 0),  # dotfile => never swept by '<' hooks
    ],
)
def test_predicate_does_not_overmatch(path: str, size: int) -> None:
    assert not is_sentinel_shape(path, size), f"guard over-match: {path} ({size}B)"


# ---------------------------------------------------------------------------
# 2. the live-tree gate: what actually blocks the next incident in CI
# ---------------------------------------------------------------------------


def test_repo_tracks_no_sentinel_files() -> None:
    if not (REPO_ROOT / ".git").exists():  # source-distribution checkout
        pytest.skip("git metadata absent — nothing tracked to guard")
    hits = [
        rel for rel, size in _tracked_files_with_sizes(REPO_ROOT) if is_sentinel_shape(rel, size)
    ]
    assert not hits, (
        f"SENTINEL CLASS REGRESSION (BUG-286): zero-byte identifier-shaped "
        f"file(s) tracked at repo root: {hits}. These are words eaten by cmd-shell "
        f"redirect hooks out of '<' command lines and swept in by `git add -A`. "
        f"git rm them; do not merely extend the ignore list."
    )


# ---------------------------------------------------------------------------
# 3. guard-memory pin: .gitignore keeps carrying the known offender literals
# ---------------------------------------------------------------------------


def test_known_offenders_ignored() -> None:
    lines = {
        ln.strip() for ln in (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    }
    # BUG-293: the guard entries are ROOT-ANCHORED ('/the', not 'the').
    # An unanchored single-segment pattern matches at any depth and on
    # case-insensitive (Windows) filesystems shadows real source trees —
    # pattern 'PAPER' was shadowing src/nexus_scalp/adapters/paper/
    # directory-wide (git add of the TRACKED paper_adapter.py needed -f).
    # Anchored form is REQUIRED here so a future re-unanchor fails CI.
    missing = [n for n in ("/the", "/min_samples_to_reject") if n not in lines]
    assert not missing, f".gitignore lost sentinel-class guard entries: {missing}"
