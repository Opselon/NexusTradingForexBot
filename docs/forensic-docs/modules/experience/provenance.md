# src/nexus_scalp/experience/provenance.py

- PURPOSE: The repository's ONLY model registry — append-only descriptive
  metadata about model artifacts used by the decision path. Never weights,
  never file handles, never a torch import.
- ARCHITECTURE LAYER: Application (persistence of domain metadata via
  AuditRepository queue + direct read connections).
- RESPONSIBILITY: Answer "which artifact, schema and config produced this
  decision?" without requiring the artifact to still exist. Consequences
  (docstring lines 6-17): the production model artifact may be deleted,
  retrained, rebuilt on startup, hot-swapped or widened 50D→60D/350D without
  touching a single stored experience; a rebuilt model can read historical
  provenance; historical experiences are NEVER rewritten to match a newly
  registered model. Registration is idempotent per
  (model_id, model_version, artifact_fingerprint).
- DEPENDENCIES: `audit_repository.AuditRepository`, `experience.models`
  (ModelProvenance, schema constants), stdlib (hashlib, sqlite3, pathlib),
  observability.logging.
- CONNECTS TO: `intelligence.py` — `ExperienceIntelligenceEngine.set_provenance`
  consumes `register_model()`'s return value; `ModelProvenance` is stamped on
  every new experience; `experience_model_registry` table is read by web
  status/diagnostics endpoints.
- KEY CONCEPTS:
  - `fingerprint_artifact` (lines 38-60): streaming sha256 (1 MiB chunks) of a
    model artifact, truncated to [:16]; returns "" when the file does not exist
    or is unreadable. A missing artifact is a NORMAL, supported state (cold
    start about to build a fresh model) — memory does not depend on it.
  - `ModelRegistry._current` (lines 73-78): the provenance stamped onto
    experiences recorded RIGHT NOW; replaced by each registration.
  - `register_model` (lines 80-122): constructs deterministic
    `model_id = "{role}_{schema}_{dim}d"`, fingerprints, sets `_current`,
    persists through the audit queue; `replaced=True` marks a hot-swap/retrain
    event. Registering a new model NEVER modifies or deletes any experience.
  - `_persist` (lines 124-154): upsert into `experience_model_registry` with
    `ON CONFLICT(model_id, model_version, artifact_fingerprint) DO UPDATE SET
    registered_at, was_replacement` — so repeated registration of the SAME
    artifact is idempotent, while a retrained artifact (new fingerprint) inserts
    a new row.
  - `list_registered_models` (lines 156-176): bounded read (default 50), newest
    first by registered_at.
- HOT PATH / PERFORMANCE: registration happens on model load/reload only (rare);
    fingerprint is a single streaming pass — O(file size) with 1 MiB chunks,
    never loaded into memory. No per-tick work.
- EDGE CASES & PITFALLS:
  - `_current` starts as a bare `ModelProvenance()` with model_id
    "unregistered" / version "0.0.0" — experiences recorded before the first
    registration honestly carry the "unregistered" identity.
  - Fingerprint is only 16 hex chars (64 bits) — collision-improbable but not
    cryptographic; used for identity, not security.
  - Direct private-member access to `audit_repo._is_sqlite` / `_queue` /
    `_db_path` mirrors the ledger's pattern; registry row persistence is
    best-effort (a queue failure logs, never raises).
  - The registry table name (`experience_model_registry`) lives in the audit
    schema; a missing table (lazy-schema shadow tables) makes
    list_registered_models return [] after logging — never an exception.