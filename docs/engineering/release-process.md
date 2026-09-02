---
title: Release Process
description: How a release happens — versioning, artifacts, verification, and the tag rules.
lang: en
---

# Release Process

## Versioning

Semver, single canonical source: `pyproject.toml` (`version = "9.0.6"`)
stamped into every build artifact. The README never hard-codes a conflicting
version — it defers to `nexus version` / the release metadata.

## Pipeline (`.github/workflows/release.yml`)

Tag-triggered **only** (`v*`). Stages:

1. Validate tag vs version metadata
2. Run the critical test suite (a release run that fails the suite does not
   publish — see the v9.0.6 re-cut history in the taskboard)
3. Build Windows x64 artifacts: `NexusScalpEngine-<version>-win-x64-setup.exe`
   + portable `.zip` (PyInstaller)
4. Generate SHA-256 digests, release manifest, SBOM; embed manifest in the
   portable bundle
5. Publish GitHub Release
6. **Post-publish verification** (`release/verify.py`): checksums, manifest
   paths, embedded layout (BUG-160 lineage)

## Tag discipline (BUG-152)

A tag pointing at a commit whose release run failed is **re-cut**, not
explained away: stale tag deleted (remote via REST), re-annotated on the
verified commit.

## Update/rollback (client side)

`nexus update check|latest|download|install|verify|status|history|rollback` —
release-identity lock, draft/revoked filtering, resumable downloads with
checksum verification, `minimum_model_version` matrix, honest
`RELEASE_NOT_FOUND` / `NO_UPDATE` states, update blocked while LIVE,
rollback ends FAILED_SAFE with exit 1 on failure (BUG-173).

Full reference: [`docs/RELEASE.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/RELEASE.md).
