# src/nexus_scalp/model_lifecycle/registry.py

- **PURPOSE:** Additive lifecycle extension of the canonical Phase 08
  `experience_model_registry` (spec 5/27/38.25). The existing `ModelRegistry` is
  REUSED — no duplicate registry is created.
- **ARCHITECTURE LAYER:** Research/ML governance — persists lifecycle state;
  no order authority. Reads/writes go through the AuditRepository queue.
- **RESPONSIBILITY:** Stamp lifecycle_status (CANDIDATE/CHALLENGER/CHAMPION/
  REJECTED/ARCHIVED/INVALID), lineage (parent/child models, training_run_id,
  promotion_reason, gate_summary, validation_run_ids) on registry rows. Never
  deletes or rewrites prior rows; promotion lineage is immutable.
- **DEPENDENCIES:** sqlite3, `adapters.database.audit_repository.AuditRepository`
  (private `_is_sqlite`, `_db_path`, `_queue` used), `experience.provenance`
  (ModelRegistry, fingerprint_artifact), features.schema, models.ModelStatus.
- **CONNECTS TO:** orchestrator (registry transition), trainer/worker
  (registration flow), dashboard reads (list_models/summary/champion).

- **KEY CONCEPTS:**
  - `_EXTENSION_COLUMNS` (line 32): 8 additive columns, all TEXT with safe
    defaults — a non-destructive migration. `ensure_schema()` (line 66) is
    idempotent, sqlite-only (returns early for non-sqlite audit repos), uses
    `PRAGMA table_info` then `ALTER TABLE ... ADD COLUMN` per missing column.
  - `set_status` (line 94): UPDATE by (model_id, model_version); enqueues the
    SQL into `audit_repo._queue.put_nowait` (line 136) so the live path is NEVER
    blocked by the write; returns False when the model was never registered or
    when the audit repo is not sqlite (operator must register it first — no
    implicit creation). Logs `[MODEL] event=STATUS` with reason.
  - `register_candidate` (line 149): fingerprints the artifact (missing artifact
    ⇒ error + False), calls the Phase 08 `model_registry.register_model` with
    config_version=build_identity or "0.0.0", then stamps status CANDIDATE with
    run lineage. Idempotent by (model_id, version).
  - Reads (lines 196-268): `get_status` (newest row by registered_at),
    `list_models` bounded to 500 (default 100, `bounded = max(1, min(limit, 500))`),
    `champion()` (first CHAMPION row, newest first), `summary()` (counts per
    lifecycle_status). All reads open short-lived sqlite connections with
    timeout=5.0, closed in finally blocks.

- **HOT PATH / PERFORMANCE:** Writes are queued, never synchronous DB locks;
  reads are short connections. `ensure_schema` runs on every read path call (a
  PRAGMA + zero ALTERs once migrated — cheap but present on the poll path).

- **EDGE CASES & PITFALLS:**
  - Non-sqlite audit backends silently disable the whole registry
    (set_status/reads return False/None/[]) — no error surfaces to the operator;
    a deploy on a non-sqlite audit repo loses lifecycle tracking silently.
  - `UPDATE ... WHERE model_id=? AND model_version=?` overwrites the row in
    place — the "immutable lineage" claim is held by the model row identity +
    accumulated evidence columns, NOT by append-only rows: a repeated
    promotion/rejection on the same (model_id, version) overwrites
    promotion_reason/gate_summary. Distinct versions preserve history.
  - `child_model_id`/`validation_run_ids` columns are declared but never written
    by any method in this module (dead columns, validated_run_ids absent from
    set_status args despite the registry docstring).