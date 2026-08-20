# src/nexus_scalp/research/evidence.py

- PURPOSE: TASK-21-RESEARCH-OBSERVABILITY research observability & evidence
  DOMAIN MODELS (2026-08-20): first-class research entities so the engine
  can answer WHERE it is stuck, WHY, WHAT evidence exists, WHICH
  configuration produced it, and whether the whole run can be replayed.
- ARCHITECTURE LAYER: Research Domain (frozen pydantic; no I/O; no order
  authority).
- RESPONSIBILITY: define ResearchGate (one evaluation step), ResearchRun
  Snapshot (immutable reproducibility fingerprint), ResearchEvent (timeline
  entry), EvidenceArtifact (immutable evidence-vault record), OutcomeLineage
  (per-outcome source attribution), plus the status/failure enums and the
  canonical gate chain.
- DEPENDENCIES: pydantic, stdlib (hashlib, json, datetime).
- CONNECTS TO: observability.py (ResearchObservabilityStore persists all
  entities), pipeline.validate_candidate (creates gates/events/evidence,
  builds snapshots), worker (heartbeat status), web/API trace endpoints.

- KEY CONCEPTS:
  - Enums: GateType (STATIC_VALIDATION, BACKTEST, WALK_FORWARD, OOS,
    ROBUSTNESS, SCORING — the six gate kinds), GateStatus (PENDING/QUEUED/
    RUNNING/PASSED/FAILED/SKIPPED/BLOCKED/ERROR/CANCELLED), RunStatus,
    RunOutcome (VALIDATED/REJECTED/INCONCLUSIVE), WorkerHealth
    (HEALTHY/DEGRADED/STUCK/FAILED/IDLE/UNKNOWN), FailureClass
    (TECHNICAL/RESEARCH/DATA/UNKNOWN — distinguishes infra from statistical
    failure), EvidenceKind (BACKTEST_RESULT ... SNAPSHOT/EVENT).
  - `GATE_CHAIN` (lines 127-134): canonical order; `next_gate` (165-171)
    walks it. REQUIRED_GATES_FOR_VALIDATION (137-145): BACKTEST,
    WALK_FORWARD, OOS, ROBUSTNESS, SCORING — the VALIDATED invariant set.
    `_TERMINAL_GATE` = {PASSED, FAILED, CANCELLED}; `is_terminal_gate` /
    `is_research_failure` (FAILED only).
  - `stable_digest` (183-186): deterministic sha256 of sort-keys JSON — the
    research hash primitive.
  - `ResearchGate` (189-225): per-gate identity + strategy/run + type +
    status + started/completed/duration_ms + configuration/dataset/engine
    version stamps + result dict + failure_reason + failure_class +
    evidence_id link + retryable + order_index; properties is_terminal /
    is_failed (FAILED|ERROR).
  - `ResearchRunSnapshot` (228-274): full reproducibility fingerprint —
    strategy_definition_hash, dataset_version + dataset_hash (over
    sorted idempotency keys), schema/model/rule/runtime/engine version
    fields, random_seed, configuration_hash; `fingerprint()` = stable
    digest over the meaningful subset. `build_run_snapshot` (277-323):
    captured from the LIVE candidate definition + actual dataset artifact;
    absent inputs stay empty strings ("NOT_RECORDED" rendering), never
    fabricated.
  - `ResearchEvent` (334-351): one persisted timeline entry with payload.
  - `EvidenceArtifact` (354-400): immutable vault record — deterministic
    evidence_id `EV-<digest[:12]>.upper()`, canonical sorted content,
    content_hash, dataset/engine version stamps; `create()` builds it.
  - `OutcomeLineage` (403-417): per-outcome source attribution — NONE means
    the outcome row carries NO reconstruction source (unattributed/legacy,
    never "derived"); BROKER_DEALS / BROKER_DEALS_AGGREGATED /
    RECONSTRUCTED explicit; repair_state UNTOUCHED/REPAIRED/ALREADY_VALID/
    NO_BROKER/AMBIGUOUS/FAILED.
  - Invariant discipline (docstring 27-33): VALIDATED requires all gates
    PASSED + SCORING PASSED + a closed evidence artifact per gate; REJECTED
    requires at least one failed gate or terminal failure; runs are
    immutable/append-only.
- HOT PATH / PERFORMANCE: pure models; hashing is per-artifact; used on
  worker/pipeline paths, never per tick.
- EDGE CASES & PITFALLS:
  - The VALIDATED invariant mentioned in the docstring ("closed evidence
    artifact per gate") is NOT enforced by this module — enforcement is
    split between registry.invariant_check (results-only) and the pipeline's
    gate bookkeeping; nothing here verifies evidence_id links actually
    resolve to stored artifacts.
  - `EvidenceArtifact.create` hashes the RAW content but stores the SORTED
    content — content_hash equals stable_digest(content) which sorts
    internally, so hash verification is consistent with the stored copy
    only via the sort; a consumer hashing the STORED (sorted) content with a
    plain json.dumps without sort_keys gets a different digest.
  - `build_run_snapshot`'s dataset_hash hashes dataset_id + sorted
    idempotency keys — two datasets with identical keys but different
    realized values hash identically (provenance-level fingerprint, not an
    evidence-level one).
  - `ResearchRunSnapshot.fingerprint()` excludes several fields (rule
    matrix, prompt, engine versions) from the digest by design — the digest
    is stable across cosmetic version fields, but two runs differing only in
    those fields share a fingerprint.