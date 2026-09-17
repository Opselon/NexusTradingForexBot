# 06 — ML System Task Ledger & Master Roadmap

> **Status:** READ-ONLY Master Engineering Backlog at HEAD `c6e7bffe`.
> Every task is derived from source-backed forensic evidence and canonical contracts.
> No implementation may proceed without satisfying preconditions and human decision gates.

---

## 1. Master Task Inventory

| ID | Priority | Title | Status | Type | Dependencies | Human Decision | Blocks | Main Risk |
|---|---|---|---|---|---|---|---|---|
| **TASK-001** | **P0** | 50D Feature Normalization Parity Verification | `READY` | Verification / Test | None | NO | TASK-004, TASK-010 | Undetected distribution drift in live serving |
| **TASK-002** | **P0** | Deterministic Data Ingest & Dataset Harness | `READY` | Data Tooling | None | NO | TASK-005, TASK-013 | Rate limits, large uncommitted market Parquet files |
| **TASK-003** | **P0** | Strict 3-Class Contract & Legacy 4-Logit Sunset | `HUMAN DECISION REQUIRED` | Architecture / Contract | None | **YES (Sunset)** | TASK-009, TASK-015 | Breaking legacy 4-wide checkpoint compatibility |
| **TASK-004** | **P0** | Model Promotion Pre-Flight & OOS Gate Audit | `BLOCKED` (after TASK-001) | Governance / Security | TASK-001 | NO | TASK-007 | Unvalidated candidate crowned as live Champion |
| **TASK-005** | **P1** | Purge & Embargo Boundary Audit & Monotonicity | `BLOCKED` (after TASK-002) | ML Integrity | TASK-002 | NO | TASK-013 | Information leakage between walk-forward folds |
| **TASK-006** | **P1** | Single Source of Truth State Machine Unification | `HUMAN DECISION REQUIRED` | Architecture / Governance | None | **YES (Root Authority)** | TASK-008 | Competing split-brain state in lifecycle vs governance DBs |
| **TASK-007** | **P1** | Signed Official Bundle Verification & Slot Isolation | `BLOCKED` (after TASK-004) | Distribution / Security | TASK-004 | NO | TASK-015 | Tampered or unsigned model weights executed live |
| **TASK-008** | **P1** | Persistent Governance Freeze Across Process Restarts | `BLOCKED` (after TASK-006) | Operational Safety | TASK-006 | NO | TASK-012 | Emergency freeze cleared on daemon crash/restart |
| **TASK-009** | **P2** | 2D vs 3D Sequence Benchmark & Routing Decision | `HUMAN DECISION REQUIRED` | Architecture / Research | TASK-001, TASK-003 | **YES (Sequence Deprecation)** | None | CPU latency overhead violating tick SLA (< 10ms) |
| **TASK-010** | **P2** | 50D vs 70D Production Feasibility & Roadmap | `HUMAN DECISION REQUIRED` | Architecture / Features | TASK-001, TASK-002 | **YES (70D Live Adoption)** | None | Feed unreliability in News/Liquidity crashing tick pipeline |
| **TASK-011** | **P2** | Live Shadow Outcome Real-Time Resolution Wiring | `READY` | Runtime Observability | None | NO | None | SQLite database lock contention during tick updates |
| **TASK-012** | **P3** | Online Fine-Tuning Safe Sandbox & Circuit Breakers | `HUMAN DECISION REQUIRED` | Runtime Safety | TASK-004, TASK-008 | **YES (Online FT Enable)** | None | Live weight corruption and catastrophic forgetting |
| **TASK-013** | **P2** | CI Model Training Smoke vs Real Validation Gap | `BLOCKED` (after TASK-002, 005) | CI/CD Infrastructure | TASK-002, TASK-005 | NO | None | PR CI timeout or host runner memory exhaustion (OOM) |
| **TASK-014** | **P2** | MT5 Runtime Boundaries & Linux Remote Parity | `READY` | Infrastructure / Portability | None | NO | None | Divergent order payload serialization between platforms |
| **TASK-015** | **P3** | ML Contract & Documentation Drift Prevention Gate | `BLOCKED` (after TASK-003, 007) | Governance / CI/CD | TASK-003, TASK-007 | NO | None | Future documentation diverging from code reality |

---

## 2. Task Details & Current Blocking Conditions

### TASK-001: 50D Feature Normalization End-to-End Parity Verification
- **Outcome:** Deterministic unit test proving zero numerical drift between training scaler and live `InferenceService` scaler.
- **Source of Truth:** `src/nexus_scalp/training/walk_forward_trainer.py:1816-1830` & `src/nexus_scalp/application/live/inference.py:52-110`.
- **Current Condition:** `READY` for immediate execution. Zero blocking dependencies.

### TASK-002: Deterministic Market Data Ingest & Dataset Pipeline Harness
- **Outcome:** CLI script fetching historical M1 bars and generating labeled (N, 50) training datasets passing `GATE1_DATASET`.
- **Source of Truth:** `src/nexus_scalp/model_generation/dataset_factory.py` & `src/nexus_scalp/model_lifecycle/gates.py:40`.
- **Current Condition:** `READY` for immediate execution. Zero blocking dependencies.

### TASK-003: Strict 3-Class Contract Enforcement & Legacy 4-Logit WAIT Sunset
- **Outcome:** Formalized 3-class model contract; sunset plan for legacy 4-logit WAIT masking.
- **Source of Truth:** `src/nexus_scalp/model_lifecycle/model_class_contract.py:50-127`.
- **Current Condition:** `BLOCKED ON HUMAN DECISION`. Operator must approve whether to immediately drop or maintain legacy compatibility shims.

### TASK-004: Model Promotion Pipeline Pre-Flight & OOS Economic Gate Verification
- **Outcome:** Hardened `POST /api/models/promotion/execute` asserting OOS expectancy $\ge 0.02\text{R}$ and atomic rollback journal.
- **Source of Truth:** `src/nexus_scalp/web/model_governance_routes.py:1270` & `src/nexus_scalp/research/oos.py:35`.
- **Current Condition:** `BLOCKED BY TASK-001`. Requires verified normalization parity before testing candidate promotion.

### TASK-005: Purge and Embargo Temporal Boundary Audit & Monotonicity Verification
- **Outcome:** Proven zero index overlap and 15-bar purge/embargo boundaries across all 34 walk-forward folds.
- **Source of Truth:** `src/nexus_scalp/training/walk_forward_trainer.py:1853-1937`.
- **Current Condition:** `BLOCKED BY TASK-002`. Requires dataset generation harness to construct and verify fold splits.

### TASK-006: Single Source of Truth Model State Machine Unification
- **Outcome:** Unified state adapter declaring `PromotionState` as authoritative root over `ModelStatus`.
- **Source of Truth:** `src/nexus_scalp/governance/models.py:170` & `src/nexus_scalp/model_lifecycle/models.py:32`.
- **Current Condition:** `BLOCKED ON HUMAN DECISION`. Operator must approve canonical mapping between lifecycle and governance states.

### TASK-007: Signed Official Model Bundle Verification & Staging Slot Isolation
- **Outcome:** Cryptographically verified distribution drill ensuring tampered weights fail closed before slot symlinking.
- **Source of Truth:** `src/nexus_scalp/model_provisioning/official_contract.py` & `official_install.py`.
- **Current Condition:** `BLOCKED BY TASK-004`. Requires hardened promotion and artifact emission gates.

### TASK-008: Persistent Governance Emergency Freeze Across Process Restarts
- **Outcome:** Emergency promotion freeze persisted in SQLite `governance_state` table; restored on engine startup.
- **Source of Truth:** `src/nexus_scalp/governance/engine.py:108` & `store.py`.
- **Current Condition:** `BLOCKED BY TASK-006`. Depends on single authoritative governance store.

### TASK-009: 2D Snapshot vs 3D Sequence Routing Verification & Architectural Decision
- **Outcome:** Controlled empirical benchmark comparing fold Sharpe, expectancy, and CPU latency between 2D and 3D ScalpNet.
- **Source of Truth:** `src/nexus_scalp/models/scalp_net.py:194-209` & `src/nexus_scalp/application/live_sequence.py:98`.
- **Current Condition:** `BLOCKED ON HUMAN DECISION`. Requires operator choice on 3D sequence deprecation vs retention.

### TASK-010: 50D vs 70D Production Feasibility Audit & Migration Roadmap
- **Outcome:** Characterized real-time feed stability for News/Liquidity features and formal migration criteria.
- **Source of Truth:** `src/nexus_scalp/features/schema.py:12` & `src/nexus_scalp/features/runtime70.py`.
- **Current Condition:** `BLOCKED ON HUMAN DECISION`. Operator must approve migration checklist and timeline.

### TASK-011: Live Shadow Outcome Real-Time Resolution & Holding Metrics Wiring
- **Outcome:** Completed shadow decisions updated with realized R-multiples and holding bars in real-time upon candle close.
- **Source of Truth:** `src/nexus_scalp/application/live/shadow_recorder.py:39` & `src/nexus_scalp/shadow/outcomes.py:92`.
- **Current Condition:** `READY` for immediate execution. Observability-only hook with zero live order authority.

### TASK-012: Online Fine-Tuning Safe Sandbox, Quarantine Buffer & Circuit Breakers
- **Outcome:** Quarantined evaluation buffer and loss degradation circuit breakers protecting neural network weights.
- **Source of Truth:** `src/nexus_scalp/application/live/bar_handler.py:311` & `walk_forward_trainer.py:1041`.
- **Current Condition:** `BLOCKED ON HUMAN DECISION`. Operator must authorize whether online learning is permitted in demo mode.

### TASK-013: CI Model Training Smoke vs Real Validation Gap & Benchmark Harness
- **Outcome:** Automated nightly benchmark workflow running a 5-fold walk-forward candidate training run on GitHub Actions.
- **Source of Truth:** `tests/critical_suite.txt` & `.github/workflows/ci.yml`.
- **Current Condition:** `BLOCKED BY TASK-002, TASK-005`. Requires data ingest harness and verified fold splits.

### TASK-014: MT5 Runtime Boundaries & Remote Gateway Linux Parity Verification
- **Outcome:** Integration tests proving bitwise identical order payload generation across Linux Remote Gateway and Paper adapters.
- **Source of Truth:** `src/nexus_scalp/adapters/mt5/mt5_adapter.py:73` & `remote_gateway_adapter.py`.
- **Current Condition:** `READY` for immediate execution. Portability verification test suite.

### TASK-015: ML Contract & Canonical Documentation Drift Prevention CI Gate
- **Outcome:** Automated CI check in `.github/workflows/ci.yml` failing if source code constants drift from canonical docs.
- **Source of Truth:** `docs/ml-system/*.md` & `src/nexus_scalp/features/schema.py`.
- **Current Condition:** `BLOCKED BY TASK-003, TASK-007`. Requires finalized class contract and artifact distribution contract.

---

## 3. Dependency Graph

```
                                  [TASK-001: Normalization Parity] ───┬───> [TASK-004: Promotion Gate] ───> [TASK-007: Signed Bundles] ───┐
                                              (READY)                 │                 (BLOCKED)                        (BLOCKED)             │
                                                                      │                                                                        │
                                  [TASK-002: Data Ingest Harness] ────┼───> [TASK-005: Purge/Embargo] ───┬─────────────────────────────────────┤
                                              (READY)                 │                 (BLOCKED)        │                                     │
                                                                      │                                  ↓                                     ↓
[TASK-003: 3-Class Sunset] ───────────────────────────────────────────┼───────────────────────────> [TASK-013: CI Training Gap]    [TASK-015: Contract Drift Gate]
 (HUMAN DECISION REQUIRED)                                            │                                      (BLOCKED)                        (BLOCKED)
                                                                      │
                                                                      ├───> [TASK-009: 2D vs 3D Benchmark]
                                                                      │          (HUMAN DECISION REQUIRED)
                                                                      │
                                                                      └───> [TASK-010: 50D vs 70D Feasibility]
                                                                                 (HUMAN DECISION REQUIRED)

[TASK-006: Unified State Machine] ────────────────────────────────────────> [TASK-008: Freeze Persistence] ───> [TASK-012: Online FT Sandbox]
    (HUMAN DECISION REQUIRED)                                                       (BLOCKED)                     (HUMAN DECISION REQUIRED)

[TASK-011: Shadow Real-Time Resolution] (READY — Independent Observability)
[TASK-014: MT5 Linux Parity Verification] (READY — Independent Platform Portability)
```

---

## 4. Execution Phases & Sequencing

### Phase A — Contract & Parity Verification (Immediate Start)
*Zero dependencies; immediately executable in parallel.*
- **TASK-001:** 50D Feature Normalization Parity Verification (`READY`)
- **TASK-002:** Deterministic Market Data Ingest & Dataset Pipeline Harness (`READY`)
- **TASK-011:** Live Shadow Outcome Real-Time Resolution Wiring (`READY`)
- **TASK-014:** MT5 Runtime Boundaries & Remote Gateway Linux Parity (`READY`)

### Phase B — Human Architectural & Product Decisions
*Requires explicit operator authorization before implementation.*
- **TASK-003:** Strict 3-Class Contract Enforcement & Legacy 4-Logit Sunset (`HUMAN DECISION REQUIRED`)
- **TASK-006:** Single Source of Truth Model State Machine Unification (`HUMAN DECISION REQUIRED`)
- **TASK-009:** 2D Snapshot vs 3D Sequence Benchmark & Routing Decision (`HUMAN DECISION REQUIRED`)
- **TASK-010:** 50D vs 70D Production Feasibility Audit & Migration Roadmap (`HUMAN DECISION REQUIRED`)
- **TASK-012:** Online Fine-Tuning Safe Sandbox & Circuit Breakers (`HUMAN DECISION REQUIRED`)

### Phase C — Training, Anti-Leakage & Governance Hardening
*Unblocked after Phase A completion and relevant Phase B decisions.*
- **TASK-004:** Model Promotion Pipeline Pre-Flight & OOS Gate Audit (Unblocked by TASK-001)
- **TASK-005:** Purge and Embargo Temporal Boundary Audit & Monotonicity (Unblocked by TASK-002)
- **TASK-008:** Persistent Governance Emergency Freeze Across Restarts (Unblocked by TASK-006)

### Phase D — Distribution, Benchmarking & CI Safety Gates
*Final integration and continuous validation gates.*
- **TASK-007:** Signed Official Model Bundle Verification & Staging Slot Isolation (Unblocked by TASK-004)
- **TASK-013:** CI Model Training Smoke vs Real Validation Gap (Unblocked by TASK-002, TASK-005)
- **TASK-015:** ML Contract & Canonical Documentation Drift Prevention Gate (Unblocked by TASK-003, TASK-007)

---

## 5. Mapping: Historical Tasks to Hardened Backlog

| Original Audit Task | Hardened Task ID | Status | Primary Change / Refinement |
|---|---|---|---|
| TASK-001 (50D Normalization) | **TASK-001** | `READY` | Restructured with explicit parity epsilon (atol=1e-7), failure conditions, and zero-variance tests. |
| TASK-002 (Dataset Pipeline) | **TASK-002** | `READY` | Added CLI ingestion harness for MT5/CSV and GATE1_DATASET validation. |
| TASK-003 (3-Class Contract) | **TASK-003** | `HUMAN DECISION` | Isolated human decision for legacy 4-logit WAIT sunsetting. |
| TASK-004 (Promotion Audit) | **TASK-004** | `BLOCKED (T-001)` | Gated on TASK-001; hardened OOS 0.02R floor and atomic rollback journal checks. |
| TASK-005 (Purge/Embargo) | **TASK-005** | `BLOCKED (T-002)` | Gated on TASK-002; explicit 15-bar purge/embargo index disjointness assertions across 34 folds. |
| TASK-006 (State Machine) | **TASK-006** | `HUMAN DECISION` | Isolated operator decision designating authoritative state root. |
| TASK-007 (Artifacts / Bundles) | **TASK-007** | `BLOCKED (T-004)` | Gated on TASK-004; verified Ed25519 signing and tamper-proofing drill. |
| TASK-008 (Governance Freeze) | **TASK-008** | `BLOCKED (T-006)` | Gated on TASK-006; added SQLite table schema migration for crash persistence. |
| TASK-009 (2D vs 3D Routing) | **TASK-009** | `HUMAN DECISION` | Empirical benchmark protocol with strict CPU latency and fold Sharpe metrics. |
| TASK-010 (50D vs 70D Roadmap) | **TASK-010** | `HUMAN DECISION` | Characterization of News/Liquidity feeds before operator decision. |
| TASK-011 (Shadow Outcomes) | **TASK-011** | `READY` | Hooked bar close event to shadow outcome resolution; zero execution authority. |
| TASK-012 (Online FT Sandbox) | **TASK-012** | `HUMAN DECISION` | Isolated human decision for online learning in demo mode; shadow quarantine buffer. |
| *New: CI Training Gap* | **TASK-013** | `BLOCKED (T-002, 005)` | Dedicated scheduled/nightly workflow running 5-fold candidate training. |
| *New: MT5 Linux Portability* | **TASK-014** | `READY` | Independent verification of RemoteMT5GatewayAdapter vs Paper adapter parity on Linux. |
| *New: Contract Drift Gate* | **TASK-015** | `BLOCKED (T-003, 007)` | CI script asserting that canonical docs/ml-system constants never drift from code. |
