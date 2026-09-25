# src/nexus_scalp/shadow/engine.py

- PURPOSE: ShadowEngine — the ONLY entry point the LiveEngine uses to
  record a shadow decision (PHASE 11 spec 4/5/6/21). Wires runtime +
  comparer + store into one bounded engine; guarantees same-input
  integrity (champion's live feature hash stamped), schema-safety
  (incompatible challenger never used), zero order authority, and every
  recorded decision is flagged simulated=True.
- ARCHITECTURE LAYER: Domain/application boundary (evaluation harness).
- RESPONSIBILITY: run lifecycle (start_run/finish_run/attach_challenger),
  record_shadow_decision (live-path entry), current_evidence, champion
  ref plumbing (set_champion_ref).
- DEPENDENCIES: shadow.challenger, shadow.comparison, shadow.models,
  shadow.store, uuid, logging.
- CONNECTS TO: LiveEngine tick path (record_shadow_decision),
  ShadowWorker.tick (finish_run), governance shadow runtime (separate
  path via ChallengerRuntime), forensics shadow checks.
- KEY CONCEPTS:
  - start_run: idempotent by run_id; a DIFFERENT new run automatically
    completes the previous run first; persists RUNNING row;
    _decisions reset.
  - attach_challenger(None) disables shadow recording (record returns
    None).
  - record_shadow_decision: builds SharedInputRef from the LIVE feature
    hash/schema/dimension/regime/session/configuration (the same-input
    stamp); schema-safety: runtime.ref.schema_id + dimension must match
    the live values, else the record is INVALID (challenger never runs);
    inference runs on the SAME feature_vector passed by the caller (a
    missing vector invalidates — "feature vector not supplied");
    challenger faults mark the decision invalid with the reason and are
    logged ([CHALLENGER] event=INFERENCE_FAILED), never propagated;
    hypothetical_r left 0.0 (resolved on exit simulation — i.e. never
    resolved here); action_agreement = valid AND actions equal;
    persisted via store.save_decision. NEVER EXECUTES ANYTHING — the
    Challenger output is a hypothetical proposal.
  - finish_run: persists the run row (COMPLETED/FAILED/CANCELLED with
    decision_count/error), then aggregates ALL decisions through
    ShadowComparer.compare and persists the ShadowComparison; resets
    active_run_id. Aggregation only when decisions exist AND challenger
    ref is present.
  - Internal refs: _champion set via set_champion_ref; _started module-
    level class default datetime.now(UTC) evaluated ONCE at class
    definition (see pitfalls).
- HOT PATH / PERFORMANCE: record_shadow_decision is the tick-path entry —
    only inference (ms) + in-memory append + queue put (non-blocking);
  aggregation happens at run finalize (worker), never in tick pipeline.
- EDGE CASES & PITFALLS:
  - `_started` is a CLASS attribute initialized at import time —
    ShadowRun(started_at=self._run_started_at()) in finish_run uses the
    process-start timestamp, NOT the actual run start; every run's
    started_at is therefore wrong (the real started_at was captured in
    the start_run row but discarded from the final row, which rebuilds
    ShadowRun with _run_started_at()).
  - `_champion` similarly class-level None; set_champion_ref sets an
    instance attr — finish_run calls self._champion_ref() correctly, but
    the class default shadows only before set (ok by wiring convention).
  - A challenger whose schema differs marks every decision invalid —
    comparisons still persist with valid_comparison=0; shadow store keeps
    the run row with decision_count.
  - finish_run error paths: an exception inside comparer.compare
    propagates out of finish_run (worker catches and logs FAILURE).