# src/nexus_scalp/intelligence/autopsy.py

- PURPOSE: The forensic "WHY did this trade win / lose" engine — packages the
  Phase 08 decomposition (strategy/entry/management/exit/execution quality)
  into an explanatory NARRATIVE with a verdict, and persists it per closed
  ticket.
- ARCHITECTURE LAYER: Application (derived intelligence; reads a merged
  ExperienceRecord + OutcomeDecomposition; persists via the audit queue).
- RESPONSIBILITY (docstring lines 12-23): explicitly separates "the market was
  wrong" from "we managed it badly" so "losing trade" never collapses into "bad
  strategy". An autopsy is a DERIVED, rebuildable object — it never writes
  financial truth and never touches execution; persisted once per closed ticket
  (upsert on ticket).
- DEPENDENCIES: `audit_repository.AuditRepository`,
  `experience.models` (ExperienceRecord, OutcomeDecomposition),
  `experience.quality.OutcomeAnalyzer` (thresholds/analyzer reuse),
  `intelligence.models` (AutopsyVerdict, TradeAutopsy),
  observability.logging.
- CONNECTS TO: worker.py (`_refresh_autopsies` builds autopsies for closed
  experiences lacking one), store.py (load_autopsy / list_autopsies reads),
  web diagnostics; experience intelligence.py records the decomposition that
  this engine consumes.
- KEY CONCEPTS:
  - VERDICT MODEL (docstring lines 15-23 + `_narrate` lines 182-241):
    - CLEAN_WIN: realized_r > 0 AND strategy_quality ≥ 0.35 AND entry_quality
      ≥ 0.0 — the market validated the thesis and management preserved it.
    - LUCKY_WIN: profitable but strategy/entry evidence weak — narrative
      explicitly says "Do not credit a broken thesis for this."
    - EVEN: |realized_r| < 1e-9.
    - MANAGED_LOSS: realized_r ≤ 0 AND management_quality ≥ 0 AND
      execution_quality ≥ 0 — risk was respected (acceptable loss), NOT
      evidence the strategy is broken.
    - COSTLY_LOSS: realized_r ≤ 0 AND (management_quality < 0 OR
      execution_quality < 0) — loss amplified by process failure; reasons
      list includes giveback ≥ captured_giveback_pct (default 0.35).
  - `build_autopsy` (lines 92-180): merges the decision record (entry/SL/giveback
    derived as max(0, 1 − realized/mfe) when mfe_r > 1e-9 and realized > 0)
    with the decomposition; giveback_pct computed from the RECORD's behavior
    MFE (normalized), not from raw USD.
  - `persist` (lines 243-295): upsert into `trade_autopsies` on ticket —
    ON CONFLICT(ticket) DO UPDATE SET …, so re-autopsy of the same ticket
    converges to the latest derived truth rather than duplicating rows.
  - Threshold reuse: `strategy_good = strategy_quality >= 0.35` mirrors
    quality.py's `_verdict` GOOD boundary; entry_good = ≥ 0.0 (a stricter
    line than _verdict's ACCEPTABLE −0.15 — the autopsy demands non-negative
    entry evidence for a clean win).
- HOT PATH / PERFORMANCE: N/A — produced by the background worker for closed
  trades only; bounded to ≤500 experiences per strategy per cycle.
- EDGE CASES & PITFALLS:
  - `build_autopsy` sets `exit_price = record.proposed_entry` when no exit
    price is supplied (line 127) — the autopsy's exit_price can be WRONG for
    stops far from entry; callers should pass the real exit price (worker.py
    currently does NOT pass one, so persisted exit_price = proposed_entry is
    the norm in worker-produced autopsies).
  - `record` may be None: the autopsy then narrates from decomposition inputs
    only, all record-derived fields stay 0/"" — honest but sparser.
  - The upsert rewrites strategy_id/realized numbers per ticket — idempotent
    for the same inputs but NOT content-addressed: a later re-run with a
    different decomposition overwrites the autopsy (documented behavior:
    "once per closed ticket (upsert)").
  - worker.py line 244 uses `record.execution_id or record.idempotency_key`
    as the autopsy ticket — for un-executed records this fabricates a
    pseudo-ticket; the worker filters is_executed AND is_closed first, so only
    reconciled-execution gaps (empty execution_id on closed records) hit this
    path.