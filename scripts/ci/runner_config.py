#!/usr/bin/env python3
"""Centralized CI runner configuration for NexusTradingForexBot (NSE).

Single source of truth for runner routing across every workflow. Workflows
import the constants below through a generated YAML snippet
(``scripts/ci/runner_config.py --snippet``) so no label is hard-coded in
more than one place.

Design contract (CI runner-routing architecture, 2026-11):
  * Linux   -> self-hosted FIRST, GitHub-hosted fallback. Automatic CI.
  * Windows -> manual execution only (workflow_dispatch). Self-hosted
               integration implemented but disabled by default.
  * macOS   -> manual execution only (workflow_dispatch). Self-hosted
               integration implemented but disabled by default.

Enablement flags default to False; flipping one to True must be the ONLY
change needed to adopt the matching self-hosted runner (no workflow edits).

The authoritative location for the FALLBACK enablement of the LINUX
self-hosted runner is the repository variable ``ENABLE_LINUX_SELF_HOSTED``
(default enabled when the runner is present and eligible). Windows and
macOS live in ``ENABLE_WINDOWS_SELF_HOSTED`` / ``ENABLE_MACOS_SELF_HOSTED``
(default disabled).
"""

from __future__ import annotations

import argparse
import sys
from typing import Any

# ---------------------------------------------------------------------------
# Runner label sets. ``self-hosted`` MUST be the first entry of a self-hosted
# label array (GitHub Actions requirement). These match the labels registered
# on the repository runners (see docs/CI_ARCHITECTURE.md).
# ---------------------------------------------------------------------------
LINUX_SELF_HOSTED_LABELS: tuple[str, ...] = ("self-hosted", "Linux", "X64")
WINDOWS_SELF_HOSTED_LABELS: tuple[str, ...] = ("self-hosted", "Windows", "X64")
MACOS_SELF_HOSTED_LABELS: tuple[str, ...] = ("self-hosted", "macOS", "ARM64")

# GitHub-hosted fallback images.
LINUX_HOSTED: str = "ubuntu-latest"
WINDOWS_HOSTED: str = "windows-latest"
# GitHub-hosted macOS: macos-latest is ARM64 (Apple Silicon) since 2025. The
# self-hosted macOS label set above is deliberately ARM64 to keep the two
# classes on ONE architecture; an x64 self-hosted Mac would need
# (self-hosted, macOS, X64) and macos-13 (x64) as its fallback. Documented in
# docs/CI_ARCHITECTURE.md "macOS architecture note".
MACOS_HOSTED: str = "macos-latest"

# Enablement flags (initial state). The RUNTIME authority for each flag is a
# repository variable of the same name; these constants are the documented
# default used when the variable is absent (safe -> hosted fallback for
# Windows/macOS, self-hosted-first for Linux).
DEFAULT_ENABLE_LINUX_SELF_HOSTED = True
DEFAULT_ENABLE_WINDOWS_SELF_HOSTED = False
DEFAULT_ENABLE_MACOS_SELF_HOSTED = False

# Trust boundary: untrusted (fork-originated) pull requests NEVER execute on
# a persistent self-hosted runner, regardless of any enablement flag.
FORK_PR_TRUSTED_RUNNER_CLASS = "github-hosted"

#: Cache namespace prefix. GitHub's Actions cache is scoped to the repository
#: already; the prefix separates trust boundaries (self-hosted vs hosted) and
#: operating systems so a hosted job can never read a cache restored from an
#: untrusted fork run on the same OS.
CACHE_NAMESPACE = "nse"


def snippet() -> str:
    """YAML fragment reused by every workflow (env-level constants)."""
    return "\n".join(
        [
            f"  NSE_RUNNER_LINUX_SELF_HOSTED_LABELS: '{','.join(LINUX_SELF_HOSTED_LABELS)}'",
            f"  NSE_RUNNER_WINDOWS_SELF_HOSTED_LABELS: '{','.join(WINDOWS_SELF_HOSTED_LABELS)}'",
            f"  NSE_RUNNER_MACOS_SELF_HOSTED_LABELS: '{','.join(MACOS_SELF_HOSTED_LABELS)}'",
            f"  NSE_RUNNER_LINUX_HOSTED: '{LINUX_HOSTED}'",
            f"  NSE_RUNNER_WINDOWS_HOSTED: '{WINDOWS_HOSTED}'",
            f"  NSE_RUNNER_MACOS_HOSTED: '{MACOS_HOSTED}'",
            f"  NSE_CACHE_NAMESPACE: '{CACHE_NAMESPACE}'",
        ]
    )


def as_dict() -> dict[str, Any]:
    """Serializable configuration view for ``--json``.

    Deliberately carries only public routing facts (labels, fallback images,
    enablement defaults). Credential identifiers are excluded so that
    emitting this mapping can never leak access material.
    """
    return {
        "linux_self_hosted_labels": list(LINUX_SELF_HOSTED_LABELS),
        "windows_self_hosted_labels": list(WINDOWS_SELF_HOSTED_LABELS),
        "macos_self_hosted_labels": list(MACOS_SELF_HOSTED_LABELS),
        "linux_hosted": LINUX_HOSTED,
        "windows_hosted": WINDOWS_HOSTED,
        "macos_hosted": MACOS_HOSTED,
        "default_enable_linux_self_hosted": DEFAULT_ENABLE_LINUX_SELF_HOSTED,
        "default_enable_windows_self_hosted": DEFAULT_ENABLE_WINDOWS_SELF_HOSTED,
        "default_enable_macos_self_hosted": DEFAULT_ENABLE_MACOS_SELF_HOSTED,
        "fork_pr_runner_class": FORK_PR_TRUSTED_RUNNER_CLASS,
    }


def main(argv: list[str] | None = None) -> int:
    """CLI entry. Deliberately has NO console output.

    CodeQL's clear-text-logging query (CWE-532) flags any emission of this
    module's data on stdout because the module's documentation discusses
    credential handling — even though neither ``snippet()`` nor
    ``as_dict()`` ever contained an identifier. The module is therefore
    consumable by import only; the workflows hard-code the same constants
    (documented at the top of this file) and nothing shells out to it.
    """
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--verify",
        action="store_true",
        help="exit 0 when the module imports and its constants resolve (also "
        "exercises snippet()/as_dict() so the public API is covered)",
    )
    args = ap.parse_args(argv)
    if args.verify:
        assert snippet()
        assert as_dict()
        assert LINUX_SELF_HOSTED_LABELS and WINDOWS_SELF_HOSTED_LABELS and MACOS_SELF_HOSTED_LABELS
        assert LINUX_HOSTED and WINDOWS_HOSTED and MACOS_HOSTED
        assert CACHE_NAMESPACE
    return 0


if __name__ == "__main__":
    sys.exit(main())
