# src/nexus_scalp/model_lifecycle/dataset.py

- **PURPOSE:** PHASE 10 deterministic, causally-safe training dataset builder.
  Consumes the immutable Phase 08 experience ledger (NEVER raw DB rows) and
  produces a deterministic `TrainingDataset` artifact.
- **ARCHITECTURE LAYER:** Research/ML — dataset research artifact builder, no
  order authority.
- **RESPONSIBILITY:** (a) preserve full provenance per sample; (b) represent ALL
  outcome classes (wins, losses, neutral, rejected, bad-execution, large-loss —
  never train on winners only, spec 8); (c) enforce strict temporal causality —
  `as_of` never leaks future outcomes; (d) deterministic identity — same input +
  config ⇒ same `dataset_id`.
- **DEPENDENCIES:** `experience.ledger.ExperienceLedger`,
  `experience.models.ExperienceRecord`, `features.schema.FEATURE_SCHEMAS`,
  `domain.enums.ActionType`, `model_lifecycle.models`, observability logger.
- **CONNECTS TO:** orchestrator (`TrainingDatasetBuilder`), trainer (frame
  conversion), gates (dataset integrity/label gates).

- **KEY CONCEPTS:**
  - `LABEL_MAP` (line 41): ActionType.NO_TRADE→0, BUY_MARKET→1, SELL_MARKET→2 —
    matches the TripleBarrierLabeler + WalkForwardTrainer contract; labels are
    NEVER invented (spec 9). Unknown actions fall back to 0 (NO_TRADE) via
    `LABEL_MAP.get(..., 0)`.
  - `TrainingDatasetBuilder._row_from_record` (line 64): skips rows with no
    feature snapshot or with a schema mismatch (logged `[TRAINING_DATASET] sample
    schema mismatch, skipped`, line 79); sample_id = `ts_<sha256(idempotency_key)>
    [:16]` (line 87); NO_TRADE rows get weight `weight_no_trade` (default 0.25 —
    the dominant class in scalping data is down-weighted, line 89-93) and are
    dropped entirely when `include_no_trade=False`.
  - `build()` (line 118): per-strategy loop over the ledger
    (`list_strategy_ids` + `get_experiences_for_strategy(sid, limit=10000)` —
    the 10k limit is a hard per-strategy cap). Causality wall: rows with
    `decision_timestamp >= as_of` are skipped (line 157). Open positions
    (`is_executed and not is_closed`) are never labeled evidence (line 160).
    With `only_executed=True` (default), never-executed decisions only enter as
    NO_TRADE samples when explicitly wanted (line 162-166). Rows sorted by
    decision_timestamp; `dataset_id` computed over (cfg, idempotency_key|ts)
    via `_dataset_id` (line 203) — the deterministic identity input, plus
    `config_hash` sha256[:16] of the sorted JSON config.
  - `validate_no_future_leakage(dataset, as_of)` (line 211): hard invariant —
    raises ValueError on any row at/after `as_of`. This is the programmatic
    guarantee of no-lookahead for downstream consumers.
  - `resolve_schema()` (line 48): resolves the active feature schema
    (defaults to the registry's active schema, used for schema_id + dimension
    stamped on the dataset).

- **HOT PATH / PERFORMANCE:** Offline only. The per-strategy
  `get_experiences_for_strategy` cap of 10 000 rows bounds memory but also caps
  dataset size per strategy; `_row_from_record` clones full feature vectors into
  frozen Pydantic rows (10k+ rows is fine for research).

- **EDGE CASES & PITFALLS:**
  - `LABEL_MAP.get(str(rec.action), 0)` (line 62) silently maps ANY unlisted
    action (e.g. LIMIT/STOP/CLOSE orders, WAIT) to NO_TRADE label 0 — a rejected
    or unexecuted decision becomes a negative sample by design (spec 8), but an
    exotic action with `is_executed=True` would be mislabeled 0 rather than
    skipped. The guard `only_executed and not is_executed` does not filter
    executed rows of unknown action types.
  - `strategy_ids` from the ledger may include strategies with no samples —
    harmless; empty iterations add nothing.
  - `source_range` uses timestamps of sorted rows; the dataset id covers
    (cfg,row identity) but NOT the feature values — two builds with identical
    rows/cfg produce the same id even if features changed under the same
    idempotency keys (intended: identity is provenance-based).