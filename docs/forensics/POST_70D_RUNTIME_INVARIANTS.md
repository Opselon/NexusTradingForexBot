# POST-70D RUNTIME INVARIANTS — Nexus Scalp Engine (NSE)

> Maintained per the MASTER MULTI-AGENT CONTRACT §8 (`agents/multi-agent-git-contract.md`).
> TASK-11 (AGENT-11, 2026-08-19): permanent invariant registry for the continuous
> forensic monitoring layer. These invariants are NON-NEGOTIABLE runtime guarantees.
> Every agent must consider them when modifying shared runtime code; intended
> changes require review + a DEC-XXXX decision record.
>
> Status legend: 🟢 VERIFIED · 🟡 PARTIALLY VERIFIED · 🔴 VIOLATED · ⚪ NOT_APPLICABLE/UNKNOWN
> Each invariant lists its enforcing check (CHECK-*) and its protecting tests (TEST-MONITOR-*).

## ID SPACES (INV-70D-001..003 — feature family layout)

### INV-70D-001 — Feature indices 0..49 are the Base family
The first 50 dimensions of every feature vector schema are the canonical
`scalp_v1` Base contract (price action, wick anatomy, swing structure,
sessions, lags, ICT/SMC, Ichimoku, dynamic S/R, multi-timeframe, OB
validation). No code change may reorder, remove or rebase indices 0..49.
- Enforcing check: CHECK-FCS-01 (FeatureContractCheck family layout)
- Tests: TEST-MONITOR-01, TEST-MONITOR-02
- Current evidence: `scalp_v1` (dim 50, ACTIVE), `scalp_v2` (dim 60),
  `scalp_liquidity_v1` (dim 60) all inherit Base at 0..49
  (src/nexus_scalp/features/schema.py). 🟢

### INV-70D-002 — Feature indices 50..59 are the News family
When a schema extends Base with News dimensions, they occupy indices 50..59.
Any code change that moves a different feature into index 60 (or renames the
family) is FEATURE_SCHEMA_DRIFT and must be detected BEFORE model inference.
- Enforcing check: CHECK-FCS-02 (FeatureContractCheck news family)
- Tests: TEST-MONITOR-02

### INV-70D-003 — Feature indices 60..69 are the Liquidity family
When a schema extends Base+News with Liquidity dimensions, they occupy
indices 60..69. The first Liquidity index is 60 (e.g. `bsl_distance_atr` —
per 70D contract snapshot docs/LIQUIDITY_60D_50D_CONTRACT_SNAPSHOT.json).
A feature swap at index 60 is FEATURE_SCHEMA_DRIFT, reported before any
model inference.
- Enforcing check: CHECK-FCS-03 (FeatureContractCheck liquidity family)
- Tests: TEST-MONITOR-01, TEST-MONITOR-02
- Current evidence: `scalp_v4` (dimension 70, candidate) registers the
  70D integration contract BASE 0..49 | FAMILY 50..59 | LIQUIDITY 60..69
  (TASK-02-70D-INTEGRATION, 2026-08-19) with `bsl_distance_atr` at index 60.
  ACTIVE live contract remains `scalp_v1` (50D). No 70D artifact or frozen
  liquidity reference exists yet — the monitor reports UNKNOWN for
  70D-liquidity drift checks until references are frozen. 🟡

## VECTOR CONTRACT (INV-70D-004..006)

### INV-70D-004 — 70D vector length = 70
The 70D feature vector is exactly 70 floats (50 Base + 10 News + 10
Liquidity). No truncated/padded vectors are ever accepted by model
inference, training or experience storage.
- Enforcing check: CHECK-FCS-04; schema.validate_vector (fail-loud)
- Tests: TEST-MONITOR-01

### INV-70D-005 — all feature values finite
Every element of every produced 70D vector is finite (never NaN/Inf).
- Enforcing check: CHECK-FCS-05
- Tests: TEST-MONITOR-01

### INV-70D-006 — all feature values within [-3, +3]
Every element of every produced 70D vector is clipped to [-3, +3] (the
canonical normalization contract of the feature engine).
- Enforcing check: CHECK-FCS-06
- Tests: TEST-MONITOR-01

## ARTIFACT CONTRACT (INV-70D-007..010)

### INV-70D-007 — schema hash matches manifest
The schema identity of every artifact (model, dataset, scaler) must match
the registered schema manifest (id + dimension + order). A hash mismatch is
MODEL_CONTRACT_INVALID and refuses inference where required.
- Enforcing check: CHECK-MDL-01 (ModelCheck artifact-schema chain)
- Tests: TEST-MONITOR-07, TEST-MONITOR-13

### INV-70D-008 — scaler hash matches model
The scaler artifact paired with a model must be the scaler that model was
trained with (full-file hash), otherwise MODEL_CONTRACT_INVALID and
inference is refused.
- Enforcing check: CHECK-MDL-02
- Tests: TEST-MONITOR-07

### INV-70D-009 — training/live feature parity
The feature computation path used at training time and the path used live
must produce identical vectors for identical inputs (same schema id, same
hash, same semantics). PARITY_BROKEN blocks release.
- Enforcing check: CHECK-RTP-01 (RuntimeParityCheck train/live)
- Tests: TEST-MONITOR-06 (dataset parity), TEST-MONITOR-05 (causal canary)

### INV-70D-010 — replay/live parity
Replay of deterministic historical samples must reproduce the live-produced
70D vectors byte-for-byte (same feature pipeline, no randomness).
- Enforcing check: CHECK-RTP-02
- Tests: TEST-MONITOR-06

## CAUSALITY (INV-70D-011)

### INV-70D-011 — no future leakage
No feature, news context or liquidity state at time t may use information
from after t (INV-008 extension to Liquidity). Causal test fixtures inject
future data and verify the historical vector is unchanged; triple-barrier
labeling keeps embargo/purge discipline.
- Enforcing check: CHECK-RTP-03 (causality canary)
- Tests: TEST-MONITOR-05, TEST-MONITOR-06

## RUNTIME SAFETY (INV-70D-012..016)

### INV-70D-012 — shadow cannot modify execution
The shadow/comparison path imports no order manager, risk engine or MT5
adapter. Challenger failure is FAILURE_ISOLATED; the Champion prediction
path continues unaffected. (Extends INV-014 to 70D models.)
- Enforcing check: CHECK-SHD-01 (ShadowHealthCheck)
- Tests: TEST-MONITOR-16, TEST-LG-10/11/12 (inherited)

### INV-70D-013 — legacy 60D model never receives 70D
A 60D (scalp_v2 / scalp_liquidity_v1) artifact loaded into any runtime must
receive exactly 60 features — a 70D vector is refused.
- Enforcing check: CHECK-MDL-03 (dimension cross-match)
- Tests: TEST-MONITOR-07

### INV-70D-014 — 70D model never receives 60D
A 70D artifact must receive exactly 70 features — a 60D vector is refused
at the load gate and at inference.
- Enforcing check: CHECK-MDL-03
- Tests: TEST-MONITOR-07

### INV-70D-015 — production accounting cannot be polluted by research/shadow
Research and shadow computations write to their own tables; they never
INSERT/UPDATE/DELETE accounting, ledger, broker or outcome rows. The
monitor verifies write-scope separation read-only.
- Enforcing check: CHECK-GOV-04 (GovernanceCheck), CHECK-INT-03 (DB integrity)
- Tests: TEST-MONITOR-17, TEST-MONITOR-18

### INV-70D-016 — one economic trade cannot create duplicate canonical outcomes
For each economic execution identity, canonical outcome uniqueness is
asserted: `owner_of_execution` must resolve to exactly one canonical
outcome (BUG-097 guard: record_trade_outcome rejects a second outcome
sharing execution_id under a different idempotency key).
- Enforcing check: CHECK-ACC-02 (duplicate economic outcome)
- Tests: TEST-MONITOR-10, TEST-ANOM-01..05 (inherited)

## EVOLUTION (INV-70D-017..020)

### INV-70D-017 — migration version must match current runtime
Every database domain's applied schema version must equal the version the
current runtime expects (audit=4, news=2, candle_intel=2 baseline as of
2026-08-19). Pending safe migrations at startup are applied by the gate;
a BLOCKED/FAILED/CORRUPTED migration state refuses READY.
- Enforcing check: CHECK-MIG-01 (MigrationCheck)
- Tests: TEST-MONITOR-08, TEST-DBM-01..40 (inherited)

### INV-70D-018 — Web bundle version must be compatible with backend
The shipped Web bundle (Web/app.js, index.html) must be the compatible
generation for the backend API contract. A stale bundle is
WEB_BUNDLE_DRIFT and must be exposed with the exact stale file/version.
- Enforcing check: CHECK-UI-02 (Web bundle drift)
- Tests: TEST-MONITOR-20, TEST-FA-* (inherited frontend asset checks)

### INV-70D-019 — runtime schema must equal API schema
The schema the runtime actually uses must equal the schema advertised by
the API layer. Runtime/API mismatch is CRITICAL.
- Enforcing check: CHECK-UI-03 (UI/API consistency)
- Tests: TEST-MONITOR-19

### INV-70D-020 — UI displayed values must originate from canonical runtime state
The dashboard must display the backend canonical state graph (api/live/state,
api/status health block). UI values with no canonical source (fake defaults,
old bundle values) are UI/API mismatch.
- Enforcing check: CHECK-UI-01 (canonical state provenance)
- Tests: TEST-MONITOR-19, TEST-MONITOR-20

## ULTIMATE INVARIANT (id=m47xj8) — NO SILENT CORRUPTION

```text
NO SILENT CORRUPTION        NO SILENT SCHEMA DRIFT
NO SILENT FALLBACK           NO SILENT MODEL MISMATCH
NO SILENT DATABASE DAMAGE    NO SILENT UI/API DIVERGENCE
```
Enforcing checks: every CHECK-* below; UNKNOWN is reported whenever the
checker cannot determine health (never converted to PASS, never converted
to zero/fake values).
Tests: TEST-MONITOR-24 (silent fallback), TEST-MONITOR-25 (200-but-wrong),
TEST-MONITOR-36 (no automatic self-modification).

## CHECK REGISTRY (implemented by the ForensicHealthEngine)

| Check ID | Owner check | Status | Evidence source | Tests |
| :--- | :--- | :--- | :--- | :--- |
| CHECK-FCS-01..06 | FeatureContractCheck | ACTIVE | features/schema.py + live engine vector | 01,02 |
| CHECK-MDL-01..03 | ModelCheck | ACTIVE | model_lifecycle/champion + load gate + artifacts | 07 |
| CHECK-RTP-01..03 | RuntimeParityCheck | ACTIVE | deterministic canary fixtures | 05,06 |
| CHECK-DTA-01..03 | DatasetCheck | ACTIVE | dataset manifests + distributions | 06,13 |
| CHECK-ACC-01..03 | AccountingCheck | ACTIVE | audit.db ledger/broker/outcomes | 09,10,11,12 |
| CHECK-INT-01..03 | DatabaseIntegrityCheck | ACTIVE | PRAGMA/integrity/manifest drift | 08 |
| CHECK-LIQ-01..03 | LiquidityHealthCheck | ACTIVE* | liquidity feature distributions | 03,04,15 |
| CHECK-NWS-01..04 | NewsHealthCheck | ACTIVE | news.db sources/worker/articles | 14,25 |
| CHECK-SHD-01..02 | ShadowHealthCheck | ACTIVE | governance store + shadow store | 16 |
| CHECK-GOV-01..04 | GovernanceCheck | ACTIVE | governance state + strategy registry | 17,18 |
| CHECK-MIG-01 | MigrationCheck | ACTIVE | database engine status | 08 |
| CHECK-RLS-01..02 | ReleaseCheck | ACTIVE | release bundle + build-info | 20,32 |
| CHECK-UI-01..03 | UIAPIConsistencyCheck | ACTIVE | api/live/state + bundle | 19,20 |
| CHECK-TEL-01 | TelemetryCheck | ACTIVE | telegram notifier health_state | 21 |
| CHECK-TRC-01..02 | TraceCompletenessCheck | ACTIVE | worker state tables + correlation ids | 22,23 |
| CHECK-PER-01 | PerformanceCheck | ACTIVE | timings + baselines | 31 |
| CHECK-GRW-01..02 | GrowthCheck | ACTIVE | db size + queue lengths | 29,30 |

\* CHECK-LIQ fully ACTIVE for candidate liquidity features (60D); 70D
liquidity checks report UNKNOWN while the 70D series is blocked.

## Registry notes
- `agents/bugs.md` BUG-NNN entries provide forensic evidence for each invariant.
- New invariants: append INV-70D-NNN referencing the enforcing check and tests.
- This registry supersedes nothing; existing INV-001..017 remain in force and
  are extended by the 70D set above (INV-70D-011 extends INV-008; INV-70D-012
  extends INV-014; INV-70D-017 extends INV-013).