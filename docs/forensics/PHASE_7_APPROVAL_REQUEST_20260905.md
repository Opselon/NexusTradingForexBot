# PHASE_7_APPROVAL_REQUEST — 2026-09-05

**Gate:** Stage 7 — Explicit Deletion Authorization (no drops without this)

**Proposed removal:** 78 stashes (descending index, one-by-one, re-enumerate each step)
**Preserved:** 7 stashes (5 research p3 + 1 active smoke + 1 lint UNIQUE)
**Indices proposed for removal (descending):** 84, 83, 82, 81, 80, 79, 78, 77, 76, 75, 74, 73, 72, 71, 70, 69, 68, 67, 66, 64, 63, 62, 61, 60, 59, 58, 57, 56, 55, 53, 52, 51, 50, 49, 48, 47, 46, 45, 44, 43, 42, 41, 39, 38, 36, 35, 33, 32, 31, 30, 29, 28, 27, 26, 25, 24, 23, 22, 21, 20, 19, 18, 17, 15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1

**Preserved indices:** 0, 16, 34, 37, 40, 54, 65

**Backup-ref guarantee:** 91/91 `refs/forensic/stash-backup/*` verified reachable before any drop; every drop re-verifies its backup ref.

**Awaiting approvals:** @nexus-researcher, @nexus-data, @nexus-ml, @nexus-qa, @nexus-architect, @nexus-github, forensic lead.

**Forensic lead decision required:** `PHASE_7_APPROVED` or `PHASE_7_BLOCKED` — do not delete until APPROVED.

**Full ledger:** `docs/forensics/FORENSIC_STASH_RECONCILIATION_20260905.md` + `%LOCALAPPDATA%/Temp/nse_85_enriched.json`.

**Expected post-pruning:** 85 - 78 = 7 ordinary stashes remain; 91/91 forensic refs intact.
