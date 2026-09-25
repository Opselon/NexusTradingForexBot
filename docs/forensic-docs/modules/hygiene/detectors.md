# src/nexus_scalp/hygiene/detectors.py

- PURPOSE: Duplicate Detector + Orphan Detector (TASK-11) — deterministic
  duplicate detection using CANONICAL IDENTITIES, never same-PnL/same-price/
  same-timestamp heuristics. CRITICAL SPLIT-FILL SAFETY (spec §8): multiple
  broker fills from one economic order are NOT duplicates — siblings
  sharing order_id/request_id are one canonical family and MUST remain
  linked; the detector never proposes a ledger row for deletion when it
  belongs to a multi-ticket family. Only EXACT_DUPLICATE (confidence 1.0)
  may enter automatic deletion consideration, and only the duplicate row
  whose canonical row exists — never the canonical itself.
- ARCHITECTURE LAYER: Application (read-only classification).
- RESPONSIBILITY: DuplicateDetector (per-db scans), OrphanDetector
  (classification — NEVER deletes), DuplicateCandidate record.
- DEPENDENCIES: hygiene package Confidence enum, sqlite3.
- CONNECTS TO: hygiene worker planner (build_plan), reports, web diagnostics.
- KEY CONCEPTS:
  - Identity layers (docstring): audit — broker deals/trades →
    (position_id, deal ticket set, order set); experiences/outcomes →
    idempotency_key (UNIQUE by construction); ledger → ticket PK, family =
    order_id; news — articles → article_hash; analysis → (article_id,
    run_id); candle_intel — derived rows → (ts, symbol, timeframe, pattern).
  - scan_audit (line 59): (1) outcome idempotency_key GROUP BY HAVING >1
    (drift check — UNIQUE enforced by DB): rows[1:] are EXACT_DUPLICATE
    candidates with rows[0] canonical, row_id = outcome "id"; (2)
    audit_ledger order_id families with >1 DISTINCT ticket are recorded as
    PROTECTED NOT_DUPLICATE candidates (the documented guard proving the
    family check ran) — never deletions; (3) audit_broker_trades GROUP BY
    trade_id HAVING >1 (duplicate broker rows, rowid-keyed, rows[1:]
    candidates).
  - scan_news (line 140): articles flagged is_duplicate=1 with
    duplicate_of hash → canonical row must EXIST (article_hash equal,
    article_id different) else UNKNOWN confidence "NOT deletable"; analysis
    (article_id, run_id) tuples GROUP BY >1 → rows[1:] EXACT_DUPLICATE by
    analysis_id.
  - scan_candle (line 219): returns [] BY DESIGN — derived tables are
    append-per-bar, rows are NOT duplicates of one another (rebuildable,
    not duplicates).
  - OrphanDetector.scan_audit (line 241): outcome w/o experience →
    CORRUPTION; autopsy w/o ledger row → RECOVERABLE (rebuildable); broker
    trade w/o ledger ticket → EXPECTED_ORPHAN (broker-only/historical
    reconciliation). scan_news: analysis referencing missing article →
    UNKNOWN (archive check needed). scan_candle: [] by design.
  - Orphans are CLASSIFIED and REPORTED — never deleted (the worker only
    ever plans deletes for duplicate/retention candidates).
- HOT PATH / PERFORMANCE: GROUP BY scans are full-table (only during
  hygiene cycles, not on tick path); all queries read-only.
- EDGE CASES & PITFALLS: split-fill families in audit_ledger are recorded
  as NOT_DUPLICATE candidates and land in plan.duplicates — report code
  must treat NOT_DUPLICATE as an informative row, not a candidate, or the
  "duplicates_found" counts include protected families; news duplicate
  detection compares int(article_id) — non-integer ids (uuid article ids)
  would raise ValueError; broker trade duplicates delete by rowid while the
  executor's _pk_col has no rowid path for audit_broker_trades — detected
  broker-trade duplicates have NO delete path in the executor (safe by
  omission); missing tables (OperationalError) degrade to empty scans.