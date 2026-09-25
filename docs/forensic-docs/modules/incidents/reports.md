# src/nexus_scalp/incidents/reports.py

- PURPOSE: Incident reports — machine-readable JSON + human-readable
  Markdown (spec 34/46). Every incident produces artifacts/incidents/
  <incident_id>.json (secret-masked) and .md. Secret masking (spec 47) is
  applied to EVERY export — API keys, bot tokens, passwords, credentials
  are never included (CodeQL #77/#86 clear-text storage compliance).
- ARCHITECTURE LAYER: Application output adapter (report rendering).
- RESPONSIBILITY: mask_secrets (recursive redaction), incident_json /
  incident_markdown renderers, write_incident_reports, export_zip_bundle
  (spec 46), restrictive umask helper.
- DEPENDENCIES: models.Incident, re, math, json, zipfile (lazy),
  collections.Counter, os (umask).
- CONNECTS TO: incidents worker/save paths, CLI forensics, web download
  endpoints, Telegram (matching masking rules conceptually).
- KEY CONCEPTS:
  - mask_secrets (line 98): recursive — dict KEYS containing any
    SENSITIVE_FRAGMENTS (token/password/secret/api_key/bot_token/admin_id/
    credential/authorization/private_key/passwd) → value replaced with
    "[REDACTED]"; string VALUES are scrubbed by three layers: (1)
    _SECRET_RE pattern for `key=value` secret assignments; (2)
    _SECRET_VALUE_RE — JWT (eyJ…), Telegram bot token (`\d{6,}:…`),
    sk/pk/GitHub (ghp/gho/ghs)/Slack (xox)/AWS (AKIA) keys, Google AIza,
    PEM PRIVATE KEY headers, 40- and 64-char hex runs — replaced wholly or
    per-match; (3) _scrub_high_entropy catch-all: runs ≥24 chars that are
    ≥75% alnum with Shannon entropy ≥3.2 bits/char → [REDACTED]+last char
    (leaves normal prose/identifiers intact).
  - incident_markdown (line 144): header block (status/severity/category/
    detected/first/last seen/component/operation/correlation id/root cause
    status/fingerprint/repeated count), root cause section, evidence list,
    impact (records/trades/models/research runs/blast radius/UI endpoints),
    timeline, recovery plan (state/what failed/why/trustworthy/suspect/
    must-not-change/options with status), quarantine entries, BUG linkage
    (related_bug_id/fix_commit/regression_test + REGRESSION-of note).
  - write_incident_reports (line 228): lands under <base_dir>/incidents/
    <incident_id>.{json,md}; tolerates an accidentally-passed db path
    (endswith ".db" → parent.parent); umask 0o077 wraps writes on POSIX
    (owner-only), no-op on Windows.
  - export_zip_bundle (line 254): ZIP-DEFLATED bundle with report pair +
    optional log_excerpts/db_query_results/model_manifest/runtime_snapshot
    — every content masked BEFORE zipping; deterministic member names.
- HOT PATH / PERFORMANCE: on-write per incident (worker saves /
  operator export); strings are O(len) scrubbed with compiled regexes.
- EDGE CASES & PITFALLS: dict masking replaces the VALUE but keeps the
  key name — key leaks the fact a secret exists; _SECRET_RE requires
  `[:=]` after the key name so a bare token VALUE without key context is
  only caught by _SECRET_VALUE_RE / entropy scrub (a short random token
  <24 chars with no key shape leaks); integer/float values are returned
  unchanged (mask_secrets only handles dict/list/str) — a numeric secret
  in a payload survives; high-entropy redaction keeps the LAST character
  ("[REDACTED]g") which for single-char-varying tokens is a minor leak;
  markdown joins with "\n" (LF) regardless of OS.