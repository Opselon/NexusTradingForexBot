# src/nexus_scalp/hygiene/archive.py

- PURPOSE: Archive Manager + Cleanup Journal (TASK-11) — archive-before-
  delete with checksums (spec §14-16, §44): ACTIVE DB → ARCHIVE (JSONL,
  immutable, checksummed, versioned) → VERIFY ARCHIVE (sha256 matches) →
  MARK ARCHIVED (journal) → REMOVE FROM HOT STORE. Archive layout:
  artifacts/archive/<database>/<table>/<archive_id>.jsonl; never inside
  active query paths; never auto-loaded by runtime discovery.
- ARCHITECTURE LAYER: Application (persistence adapter, filesystem).
- RESPONSIBILITY: ArchiveManager (checksummed archive writer + verifier),
  CleanupJournal (append-only per-run JSONL audit trail of every
  destructive action, spec §44), read_only_connect (URI mode=ro helper).
- DEPENDENCIES: hashlib, json, sqlite3, uuid, datetime, pathlib.
- CONNECTS TO: hygiene worker CleanupExecutor (archive_rows before delete,
  journal.record), reports, operator restores.
- KEY CONCEPTS:
  - archive_rows (line 52): archive_id HYG-<uuid12>; each row dumped
    sort_keys=True (deterministic bytes regardless of dict order); blob =
    lines joined with "\n" and a trailing newline; sha256 computed over the
    blob, file written, RE-READ and re-hashed — mismatch raises
    RuntimeError (verification BEFORE the delete proceeds). Manifest carries
    archive_id/database/table/source_schema/row_count/time_range (derived
    from the first present of ts/timestamp/created_at/published_at/
    analyzed_at/event_timestamp — scalar entries like event_timestamp that
    aren't the row's ts are coerced anyway)/created_at/sha256/
    retention_reason/software_version/path (relative to archive root).
  - Empty row list → {} (no file, no manifest).
  - verify_archive (line 111): re-hashes the archived file and compares to
    manifest.sha256 — false when missing or mismatch (used for
    post-hoc auditability, not just the inline write-back check).
  - CleanupJournal (line 123): journal at <archive_root>/_journal/
    hygiene_<run_id>.jsonl — every record carries run_id, database, table,
    candidate_id, canonical_row_id, reason, action (e.g.
    DELETE_AFTER_ARCHIVE), archive_id, verification (PENDING initially),
    confidence, at. Append-only; the executor's PENDING entries can be
    reconciled against verification results by run_id.
  - read_only_connect (line 168): file:...?mode=ro URI so hygiene scans
    NEVER create a DB file or write; plain-path connection only when the
    input already carries a URI query string.
- HOT PATH / PERFORMANCE: archive writes happen only before deletions
  (once per candidate row, batched per table from the executor);
  per-row READ-BACK re-hash is O(file); journal append is O(1) per entry.
- EDGE CASES & PITFALLS: the executor archives ONE row at a time
  (archive_rows(…,[row_dict])) — the time_range of each archive file is
  a single point; row values are JSON-coerced with default=str so ints/
  dates are strings on restore (lossy round-trip for types); path is
  stored RELATIVE to the archive root but verification resolves it against
  self.root — a manifest moved across roots fails verify (by design);
  timestamps list picks the FIRST present key per row, so mixed-schema
  tables can produce heterogeneous time_range values; journal entries are
  NEVER removed (append-only audit).