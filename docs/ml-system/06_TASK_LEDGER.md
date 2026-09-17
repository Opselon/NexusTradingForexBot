# ML System Task Ledger & Master Roadmap

## Purpose
This document provides the definitive, prioritized engineering task plan for hardening, verifying, and completing the Machine Learning subsystem of the Nexus Scalp Engine (NSE). All tasks are derived from forensic code evidence gathered at HEAD `d9a3a829`.

## Planning Rules
1. **Evidence-First:** Every task is grounded in existing repository code, exact line numbers, and verified constraints.
2. **Safety by Default:** No live trading behavior or production model weights may be altered without passing all 12 validation gates.
3. **Explicit Human Boundaries:** Any task requiring an architectural trade-off or breaking contract change requires an explicit operator sign-off before implementation.
4. **No Premature Execution:** Tasks are structured for decoupled, verifiable execution.

## Status Definitions
- **PENDING:** Task specification complete and ready for execution.
- **IN_PROGRESS:** Active implementation underway.
- **BLOCKED:** Waiting on upstream dependency completion.
- **VERIFIED:** Implemented, tested, and validated against acceptance criteria.

## Priority Definitions
- **P0 (Critical Pre-Flight):** Must be resolved before any live capital execution or candidate model generation.
- **P1 (High Operational):** Core learning, data integrity, and promotion reliability tasks.
- **P2 (Medium Capability):** Research pipeline, benchmarking, and feature expansion.
- **P3 (Low / Polish):** Code hygiene, deprecations, and documentation alignment.

---

## Task Summary

- **TOTAL TASKS:** 12
- **P0 (Critical):** 4
- **P1 (High):** 4
- **P2 (Medium):** 3
- **P3 (Low):** 1

- **PENDING:** 12
- **IN_PROGRESS:** 0
- **BLOCKED:** 0
- **VERIFIED:** 0

---

## Master Task Board

| Task ID | Priority | Title | Depends On | Status | Human Decision Required? | Scope |
|---|---|---|---|---|---|---|
| **TASK-001** | **P0** | 50D Normalization End-to-End Parity Verification | None | `PENDING` | NO | Data / Scaler |
| **TASK-002** | **P0** | Market Data Ingest & Dataset Generation Pipeline | None | `PENDING` | NO | Data Pipeline |
| **TASK-003** | **P0** | Strict 3-Class Contract Enforcement & Legacy 4-Logit Deprecation | None | `PENDING` | YES (Class Contract) | Model / Contract |
| **TASK-004** | **P0** | Model Promotion & OOS Economic Expectancy Gate Audit | TASK-001 | `PENDING` | NO | Governance |
| **TASK-005** | **P1** | Purge & Embargo Boundary Audit & Verification Tests | TASK-002 | `PENDING` | NO | Training |
| **TASK-006** | **P1** | Unified Model State Machine Root | None | `PENDING` | YES (State Authority) | Governance |
| **TASK-007** | **P1** | Model Artifacts Inventory & Signed Bundle Distribution | TASK-004 | `PENDING` | NO | Provisioning |
| **TASK-008** | **P1** | Governance Freeze Persistence Across Process Restarts | TASK-006 | `PENDING` | NO | Governance |
| **TASK-009** | **P2** | 2D Snapshot vs 3D Sequence Routing Verification & Benchmark | TASK-001, TASK-003 | `PENDING` | YES (Sequence Deprecation) | Model / Benchmark |
| **TASK-010** | **P2** | 50D vs 70D Architectural Reconciliation & Migration Roadmap | TASK-001, TASK-002 | `PENDING` | YES (70D Champion Adoption) | Architecture |
| **TASK-011** | **P2** | Live Shadow Outcome Real-Time Resolution Wiring | None | `PENDING` | NO | Observability |
| **TASK-012** | **P3** | Online Fine-Tuning Safe Sandbox & Guardrail Hardening | TASK-004, TASK-008 | `PENDING` | YES (Online Learning Enable) | Runtime / Safety |

---

## Dependency Graph

```
[TASK-001: Normalization] ───────────────┬────────────> [TASK-004: Promotion Gate] ──> [TASK-007: Signed Bundles]
                                         │
[TASK-002: Data Ingest] ────> [TASK-005: Purge/Embargo]
                                         │
[TASK-003: 3-Class Contract] ────────────┼────────────> [TASK-009: 2D vs 3D Benchmark]
                                         │
                                         └────────────> [TASK-010: 50D vs 70D Migration]

[TASK-006: Unified State Machine] ───────> [TASK-008: Freeze Persistence] ──> [TASK-012: Online FT Sandbox]

[TASK-011: Shadow Real-Time Resolution] (Independent Observability)
```

---

## Execution Order (Parallel-Safe Tracks)

### Track A (Data & Model Core)
1. **TASK-001:** 50D Normalization End-to-End Parity Verification
2. **TASK-002:** Market Data Ingest & Dataset Generation Pipeline
3. **TASK-003:** Strict 3-Class Contract Enforcement & Legacy 4-Logit Deprecation
4. **TASK-005:** Purge & Embargo Boundary Audit & Verification Tests
5. **TASK-009:** 2D Snapshot vs 3D Sequence Routing Verification & Benchmark
6. **TASK-010:** 50D vs 70D Architectural Reconciliation & Migration Roadmap

### Track B (Governance & Distribution)
1. **TASK-004:** Model Promotion & OOS Economic Expectancy Gate Audit
2. **TASK-006:** Unified Model State Machine Root
3. **TASK-007:** Model Artifacts Inventory & Signed Bundle Distribution
4. **TASK-008:** Governance Freeze Persistence Across Process Restarts
5. **TASK-012:** Online Fine-Tuning Safe Sandbox & Guardrail Hardening

### Track C (Observability & Shadow)
1. **TASK-011:** Live Shadow Outcome Real-Time Resolution Wiring
