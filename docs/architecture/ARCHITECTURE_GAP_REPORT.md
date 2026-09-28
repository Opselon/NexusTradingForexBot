# ARCHITECTURE_GAP_REPORT.md — Phase 0 Observability & UI Readiness Audit

## Scope
This report audits what is MISSING between the current Nexus Scalp Engine architecture and the requirements of the Strategy Command Center (spatial 2.5D lifecycle evolution). It complements `STRATEGY_LIFECYCLE_MAP.md`.

---

## GAP-01 — No Unified Lifecycle Event Stream
**Current state**: Lifecycle transitions are persisted as inline lineage strings (`validation_lineage` JSON list in `strategy_registry`) and scattered log lines. There is no first-class append-only `lifecycle_events` table with (event_id, strategy_id, from_state, to_state, reason, correlation_id, actor, timestamp).

**Impact**: The Time Machine / historical playback feature cannot reconstruct state at arbitrary T without diffing lineage strings. Transition anomaly scoring lacks a proper event stream.

**Recommendation**: Introduce a read-model projection table (`lifecycle_event_projection`) fed by registry upserts and `transition_lifecycle()` calls.

---

## GAP-02 — AI Attribution Not First-Class
**Current state**: Discovery source (`discovery_source`, `discovery_window`, `context_definition`) exists in the registry but there is no `DecisionContribution` record capturing weighted AI vs deterministic influence per decision.

**Impact**: The Command Center cannot honestly display "AI INFLUENCE" without fabricating numbers — violating the non-negotiable truth principle.

**Recommendation**: Extend research pipeline to emit contribution records with evidence references; until measured, UI must show "PARTIALLY MEASURABLE" / "NOT_AVAILABLE".

---

## GAP-03 — Correlation IDs Incomplete Across Boundaries
**Current state**: `ResearchRun.run_id` and `snapshot_id` exist for validation runs, and audit events carry incident trace IDs (`incidents/trace.py`), but lifecycle transitions themselves do not consistently propagate a correlation ID linking research run → gate verdict → transition → shadow trade.

**Impact**: Debug console filtering by correlation ID will have holes.

---

## GAP-04 — No Canonical Read Model for UI
**Current state**: Web routes (`web/server.py`, `factory_routes.py`, `db_console.py`) expose ad-hoc dict payloads assembled per-endpoint. There is no single `StrategyLifecycleSnapshot` DTO aggregating identity + position + gates + eligibility + health + events.

**Impact**: Every new UI surface re-implements assembly logic; risk of divergence between views.

---

## GAP-05 — Execution Eligibility Is Implicit
**Current state**: Eligibility is derived from `_INELIGIBLE` frozenset membership (`lifecycle not in {REJECTED, RETIRED, DEGRADED}`) plus `require_validation_gate()`. There is no explicit, queryable `execution_eligibility` record with reason codes that a UI can render as YES/NO/BLOCKED/UNKNOWN.

**Impact**: UI risks inferring eligibility from visual appearance rather than domain authority.

**Recommendation**: Expose a projection field computed ONLY from domain functions, with explicit UNKNOWN when data missing.

---

## GAP-06 — Health Score Components Exist But Are Scattered
**Current state**: `StrategyScore` already provides decomposable dimensions (performance, risk, stability, OOS, robustness, sample_confidence, regime_coverage, recency, execution_resilience, degradation). However data-quality and lifecycle-stability components are not scored anywhere.

**Impact**: "Strategy Health" panel can reuse StrategyScore but must mark Data Quality / Lifecycle Stability as NOT_MEASURED until implemented.

---

## GAP-07 — Debug Hints / Anomaly Detection Absent
**Current state**: No transition-anomaly scorer, validation-consistency scorer, debug-priority ranker, or fact/inference/hypothesis hint engine exists in the codebase.

**Recommendation**: Build as pure read-side analytics over the event projection (Phase 6); never write back to domain state.

---

## GAP-08 — Web UI Is HTML-Server-Rendered, Not Spatial
**Current state**: Existing web dashboard is server-rendered HTML + polling endpoints (:8080). No canvas/WebGL layer, no camera model, no LOD.

**Recommendation**: Add a spatial 2.5D layer using GPU-accelerated Canvas2D/PixiJS-style rendering inside the existing web UI rather than introducing WPF/3D engine complexity. This preserves the current deployment model (browser on :8080) and satisfies the "spatial 2.5D preferred over heavy 3D" directive.

---

## GAP-09 — Evidence Completeness Validator Missing
**Current state**: `invariant_check()` validates VALIDATED/REJECTED entries but does not compute a per-transition "evidence completeness" ratio exposing which required artifacts are missing.

---

## GAP-10 — Historical Replay Infrastructure Missing
**Current state**: Lineage strings allow reconstructing final state but not efficient time-indexed playback across all strategies simultaneously.

**Recommendation**: Derive replay from the event projection (GAP-01) once it lands.

---

## Priority Ranking
| Gap | Severity | Phase |
| :--- | :--- | :--- |
| GAP-04 | CRITICAL | Phase 1 |
| GAP-01 | CRITICAL | Phase 1/2 |
| GAP-05 | HIGH | Phase 1 |
| GAP-02 | HIGH | Phase 5 |
| GAP-03 | MEDIUM | Phase 2 |
| GAP-06 | MEDIUM | Phase 3 |
| GAP-09 | MEDIUM | Phase 3 |
| GAP-07 | MEDIUM | Phase 6 |
| GAP-10 | MEDIUM | Phase 7 |
| GAP-08 | INFO | Phase 4 |
