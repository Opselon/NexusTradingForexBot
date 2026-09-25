# RTF-002 — PostgreSQL dead-letter write sink permanently `None`

**Lane:** `agent/nse-always-on-runtime-fix`
**Status:** ROOT_CAUSE_FOUND → FIX_IN_PROGRESS → VERIFIED_LOCAL
**First seen:** 2026-09-24 05:46:50 (+03:30), live runtime log
**Severity:** CRITICAL — audit/financial record loss

## SYMPTOM

The live runtime log repeated, hundreds of times per minute:

```
[critical] DEAD-LETTER WRITE IMPOSSIBLE — no write sink on a non-SQLite
dead-letter store; financial record unrecoverable. query= INSERT INTO
audit_signals (...)
```

Every failed `audit_signals` / `audit_account_snapshots` /
`audit_guard_telemetry` INSERT fell through to the dead-letter path, and
the dead-letter path itself could not persist the row. `salvaged=0
dead_lettered=N` with the row discarded — silent loss of audit evidence.

## ROOT CAUSE (verified in source)

`AuditRepository._dead_letter_write_sink()` resolved the fabric backend
**once, at construction time**, and cached the result in the
`DeadLetterStore`:

```
__init__ → DeadLetterStore(write_sink=self._dead_letter_write_sink())
         → get_domain_backend("audit")   # ← returns None
__init__ → _build_write_plane()          # ← provisions the audit domain
```

The `DeadLetterStore` is constructed **before** `_build_write_plane()`
runs, and the write plane is what calls `provision_domain("audit")`. On a
fresh PostgreSQL process the backend registry is therefore still empty at
capture time, so the sink was permanently `None`. The store treats a
`None` sink as "no write sink" and logs `DEAD-LETTER WRITE IMPOSSIBLE`
rather than failing loudly — a silent, permanent disable of the only
fallback persistence path.

This is a **construction-order race**, not a missing feature: the
`write_sink` mechanism existed and worked; it was just captured against a
registry that had not been populated yet.

## FIX

`src/nexus_scalp/adapters/database/audit_repository.py`:
`_dead_letter_write_sink()` now returns a closure that resolves
`get_domain_backend("audit", readonly=False)` **per call**, never caching
at construction. The first dead-letter row arriving after the write plane
provisions the domain lands durably.

Constraints honoured:

* **Reads the registry only.** It does not lazily *provision*: that is the
  write plane's job, and doing it here could double-provision against a
  concurrent plane and close a pool still in use.
* **Returns `False`, never raises.** The dead-letter path is itself a
  recovery path; `record()` must not propagate a second exception into the
  caller that is already handling a failed write.
* SQLite behaviour is unchanged (`_is_sqlite` → `None`, so the store keeps
  using its own `conn_factory` sink and there is no double-write).

## WHY THIS WAS NOT CAUGHT

The dead-letter store had contract tests (`test_bug276_*`,
`test_recon_dead_letter_store_contracts.py`), but they exercised the
SQLite path where the sink comes from the local connection factory — the
PostgreSQL sink path had no coverage of the *construction ordering*, only
of the sink's behaviour once it existed.

## TESTS

`tests/unit/test_rtf002_deadletter_sink_lazy_resolution.py` (new, 5 tests):

1. `test_sink_is_not_none_when_domain_unprovisioned` — on a bare
   repository with the domain unregistered, the sink is a real callable,
   not `None` (the exact pre-fix failure).
2. `test_sink_resolves_backend_lazily_after_provisioning` — the SAME sink
   object returns `False` before provisioning and `True` after; a
   construction-captured `None` can never recover, a lazy resolve can.
3. `test_sink_failure_is_returned_false_not_raised` — a provider error
   inside the sink does not escape into `record()`.
4. `test_sqlite_domain_still_has_no_sink` — SQLite keeps `None` (no
   double-write regression).
5. `test_store_receives_the_sink_and_lands_a_row` — end-to-end through the
   composed store: a failed audit row is persisted to
   `audit_dead_letter` through the fabric's write plane.

## REMAINING

* The dead-letter table `audit_dead_letter` must itself exist on the
  PostgreSQL domain. It is in the baseline CREATEs (verified by the
  RTF-001 baseline scan), so `migrate_domain` creates it; the sink relies
  on the RTF-001 fix being applied to a domain that never had it.
* Replay/reconciliation of accumulated dead-letter rows is a separate
  contract (out of scope for this fix).
