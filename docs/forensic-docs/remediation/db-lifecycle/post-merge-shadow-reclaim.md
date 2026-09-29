# Post-Merge Verification — shadow_decisions payload reclaim (PR #575)

**Merged as `3c1760e7`** on `2026-09-29`. Verified against `origin/main` in a
fresh worktree and against the live PostgreSQL ledger.

## 1. Content verification (merged main, `C:/c/tmp/postverify2`)

| Artifact | On merged main |
|---|---|
| `reclaim_mirror_payloads` in `src/nexus_scalp/shadow/store.py` | present (1) |
| `information_schema.columns` introspection in `store.py` | present (2 — PG + SQLite branches) |
| `tests/unit/shadow/test_payload_mirror_reclaim.py` | present |
| `tests/critical_suite.txt` registration | present |

The only `sqlite3` use in the new code path is the existing `isinstance`
provider check and the SQLite connection seam — no new module imports
`sqlite3`, so the fabric guards still pass (823 tests including
`test_fabric_guards.py`).

## 2. Database verification (live PostgreSQL `nexusdb`, 17.10)

Run from the merged-main worktree, not the branch:

| Metric | Value |
|---|---|
| `shadow_decisions.payload` bytes | **0** |
| Rows carrying a mirror | **0** |
| Rows with populated probabilities | **55,342** |
| `shadow_decisions` columns | **60** (46 before this PR) |
| Columns after a fresh `ensure_schema()` | **60** — unchanged, the heal is a no-op on a converged database |
| Idempotent `reclaim_mirror_payloads()` | `eligible=0, reclaimed=0, rounds=0` |

The schema has converged: re-running the heal on merged main against the live
ledger adds no columns and reclaims nothing, which is exactly the
post-migration steady state.

## 3. Lossless audit

300-row random sample, 600 probability vectors, recomputed from the **original
mirror bytes** and compared against the stored column:

- value mismatches: **0**
- byte mismatches: **0**

A pre-byte-identity-fix run measured 0 value mismatches but 99
whitespace-only differences (PostgreSQL's `jsonb` text output is expanded;
the producer writes compact JSON). Since `read_decision_row` prefers the
column over the mirror, that drift would have silently changed every
consumer's reading after migration — the fix strips the separator so the
migrated column is byte-identical.

Read-through verified post-reclaim: `read_decision_row` on a real row returns
`mirror_present=False` with the champion probability vector intact.

## 4. Storage

| Relation | Size |
|---|---|
| `shadow_decisions` heap | 265 MB |
| `pg_toast_668811` (the mirror's storage) | **8 KB, 0 chunk rows** |

The heap does not shrink from a plain `VACUUM` — the freed TOAST space is
reusable by new TOAST rows, not returned to the OS without `VACUUM FULL`,
which was deliberately not run (it rewrites every row of a 55k-row table).
The verified win is the write path plus 108 MB now reusable; the TOAST
relation itself is fully drained.

## 5. Regression (merged main)

`823 passed, 2 skipped` across `tests/unit/shadow/`, `test_operator_routes.py`,
`test_fabric_guards.py`, `tests/unit/news/`. `ruff check .` and
`ruff format --check .` clean across the whole tree.

## 6. Honest caveats

- The heap size is unchanged. This PR reclaimed the duplicate payload's
  storage (the TOAST is empty) but did not return physical disk to the OS.
- `reclaim_mirror_payloads()` is an explicit, opt-in call. It is not wired to
  a scheduled job — the mirror is now empty, so there is nothing left for a
  recurring job to reclaim on this table. The next table that needs the same
  treatment can reuse the primitive.

## 7. Security

CodeQL: pass. OSV Scanner: pass. Trivy: pass. No raw SQL string
interpolation of user input — the only f-string SQL is a placeholder list
built from a fixed `?` count, and the id list is always passed as a bound
parameter.
