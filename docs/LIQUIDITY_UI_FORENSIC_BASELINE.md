# LIQUIDITY UI Forensic Baseline — Contradiction Reproduction & First Divergence

> Task: Fix Liquidity Intelligence UI contract & runtime state.
> Agent: Hermes-LiquidityUI-Forensic (AGENT-14 series) · 2026-08-19
> HEAD at baseline: `617c23a` (AGENT-03 STEP-01/02 70D parity harness)

## 1. Scope

Every Liquidity value, index, status, timestamp, schema, availability flag and
model-compatibility state shown in the Liquidity Intelligence UI must match the
canonical runtime state. This document records the exact values at every layer
**before** any fix, and proves where the first divergence appears.

## 2. Proven reproduction

`scratch/probe_liquidity_ui_state_contract.py` reproduces the production
sequence `enabled=True -> compute snapshot -> toggle OFF` (exactly what a live
session leaves behind) and captures the payloads the UI renders
(`scratch/probe_liquidity_ui_state_contract.out.txt`). The probe output below
is REAL runtime payload, not mocked:

```
[1] RUNTIME STATE (governor internals)
    enabled            = False
    snapshot present   = True
    _last_success_at   = 42973.687  (time.monotonic!)
    status()           = DISABLED
    causal_state()     = VALID

[2] GET /api/liquidity/state  (report())
    enabled                = False
    available              = True
    status                 = 'DISABLED'
    causal_state           = 'VALID'
    source                 = 'LIVE_MARKET_STATE'
    algorithm_version      = 'scalp_liquidity_v1.0.0'
    last_update            = '1970-01-01T11:56:13.687000+00:00'
    schema                 = {'id': 'scalp_v1', 'dimension': 50, ...}
    model_compatibility    = {'result': 'BLOCK',
                              'reason': 'LIQUIDITY_ENABLED_BUT_MODEL_INCOMPATIBLE',
                              'model_schema_id': 'scalp_v1', 'model_dimension': 50,
                              'runtime_schema_id': 'scalp_v4', 'runtime_dimension': 70}
    feature_count          = 10

[3] GET /api/liquidity/features (snapshot_payload())
    schema_id = scalp_v1  dimension = 50
    bsl_distance_atr             idx= 40
    ssl_distance_atr             idx= 41
    eqh_strength                 idx= 42
    eql_strength                 idx= 43
    htf_liquidity_score          idx= 44
    internal_liquidity_distance  idx= 45
    external_liquidity_distance  idx= 46
    liquidity_confluence         idx= 47
    liquidity_sweep_state        idx= 48
    post_sweep_displacement      idx= 49

[4] AUTHORITATIVE REGISTRY (schema_contract.py scalp_v3 70D)
    bsl_distance_atr             idx= 60   family=liquidity
    ssl_distance_atr             idx= 61   ...
    post_sweep_displacement      idx= 69
```

## 3. Layer-by-layer trace (FIELD x LAYER matrix)

| FIELD | BACKEND RUNTIME | API /api/liquidity/state | /api/liquidity/features | SSE/live | UI (app.js) | VERDICT |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| enabled | False (governor) | False | n/a | False | DISABLED | consistent |
| status | DISABLED | DISABLED | DISABLED (per-value) | DISABLED | DISABLED | consistent |
| schema | scalp_v1 (active, disabled) | scalp_v1/50D | scalp_v1/50D | same | scalp_v1 | **consistent but misleading** |
| dimension | 50 (disabled) | 50 | 50 | 50 | 50D | consistent w/ active schema |
| feature indices | n/a (derived) | 40..49 (snapshot_payload) | 40..49 | n/a | 40..49 (derived in JS) | **FIRST DIVERGENCE: should be 60..69 canonical** |
| source | LIVE_MARKET_STATE (last calc) | LIVE_MARKET_STATE | LIVE_MARKET_STATE | same | LIVE_MARKET_STATE | consistent |
| causal | VALID (fresh snapshot) | VALID | n/a | VALID | VALID | consistent, but semantic misuse |
| availability | snap exists → True | True | True | True | Available | **contradicts DISABLED+source** |
| last_update | 1970-01-01T11:56 (monotonic!) | same | real decision_at | same | 1970-01-01 11:56 | **FIRST DIVERGENCE: monotonic→fromtimestamp** |
| latency_ms | 108.024 | 108.024 | n/a | same | 108.02 ms | consistent |
| model_compatibility | BLOCK(LIQUIDITY_ENABLED_BUT_MODEL_INCOMPATIBLE) | same | n/a | same | BLOCK(...) | **contradicts enabled=False** |
| algorithm_version | scalp_liquidity_v1.0.0 | same | n/a | same | same | **unverified provenance** |

## 4. Proven root causes (first incorrect layer)

### RC-1 — 1970 timestamp (BUG candidate A)
`LiquidityGovernor._last_success_at` / `_last_error_at` are assigned
`time.monotonic()` (seconds since an arbitrary boot point) in
`compute_from_engine()` and on error. `report()` then renders them with
`datetime.fromtimestamp(self._last_success_at, tz=UTC)` — interpreting
monotonic seconds as Unix epoch seconds → `1970-01-01T<uptime>`.
**First incorrect layer: `liquidity_runtime.py` (timestamp capture + render).**
`age_sec` uses monotonic correctly (a delta); only the wall-clock render is wrong.

### RC-2 — feature indices 40..49 instead of 60..69 (BUG candidate B)
`snapshot_payload()` derives each index as
`act["dimension"] - len(snap.names) + idx` where `act = _active_schema_block()`.
When disabled, `_active_schema_block()` returns the ACTIVE repo schema
(`scalp_v1`, 50D) → 50-10+idx = **40..49**. When enabled it returns
`scalp_liquidity_v1` (60D) → **50..59**. The UI additionally re-derives the
same wrong numbers in JS (`baseDim - 10 + i`). The authoritative 70D registry
(`schema_contract.py`) places liquidity at **60..69**. The runtime's "active
schema" concept (50D/60D) and the canonical 70D liquidity placement are two
different notions that the payload collapses into one `schema`/`dimension`
pair — hence the "50D yet 60..69" confusion.
**First incorrect layer: `liquidity_runtime.py` (derived index math).**

### RC-3 — DISABLED yet 10 "features" + available=True (BUG candidate C)
`report()`:
- `available = snap is not None and status != UNAVAILABLE` → with a retained
  snapshot and status DISABLED it returns `True`.
- `features` = the last snapshot's 10 values whenever a snapshot exists,
  regardless of `enabled`.
The UI is a faithful renderer: it shows "Status: DISABLED" and 10 values with
no STALE/NOT ACTIVE marker, plus "Availability: Available".
**First incorrect layer: `liquidity_runtime.py` report()/snapshot_payload()**
**(no enabled gate, no stale/historical labeling).**

### RC-4 — model_compatibility BLOCK while disabled (BUG candidate D)
`model_compatibility()` ALWAYS evaluates the current model (50D scalp_v1)
against the reserved 70D `scalp_v4` schema, even when liquidity is disabled.
Result: BLOCK with `LIQUIDITY_ENABLED_BUT_MODEL_INCOMPATIBLE` — a reason that
claims liquidity is ENABLED while the status says DISABLED.
**First incorrect layer: `liquidity_runtime.py` model_compatibility()**
**(no enabled gate; wrong "enabled-but-incompatible" semantics).**

### RC-5 — source/causal/availability semantic conflation (contract)
`source` reflects the LAST successful calculation's producer
(LIVE_MARKET_STATE after a live run) — it is NOT "is there a live source right
now". `causal_state` reflects snapshot age only. `available` reflects
"snapshot exists && not UNAVAILABLE". The three are orthogonal, but the
payload exposes them with no explicit `feature_availability` /
`source_status` / `calculation_status` decomposition, so a reader (and the UI)
can assemble the contradictory row "Source UNAVAILABLE + Availability
Available" or "DISABLED + VALID + Available".
**First incorrect layer: payload contract in `liquidity_runtime.py`.**

### RC-6 — algorithm version provenance (contract)
`LIQUIDITY_ALGORITHM_VERSION = "scalp_liquidity_v1.0.0"` is a hardcoded
governor constant. The production producer
(`liquidity_engine.compute_liquidity_features`) carries NO version field; the
optimized candidate (`liquidity_engine_opt`, `liquidity-v1.1`) is not wired
into the governor. The UI therefore cannot distinguish "configured version"
from "active calculation version".
**First incorrect layer: `liquidity_runtime.py` (no provenance).**

## 5. What is NOT broken (verified)

- UI JS index math is wrong but is a *faithful* re-derivation of the backend's
  wrong schema dimension; the backend is the first wrong layer.
- SSE serialization of pool datetimes (BUG-110 fix in working tree) — already
  ISO-8601 strings via the canonical encoder; probe confirmed json.dumps
  succeeds on `report()`.
- The 10 feature VALUES themselves are real computed values (0.028, 1.384,
  ...) — no fake numbers anywhere in the pipeline.

## 6. Target contract (post-fix)

```
runtime_enabled        = false                    (toggle state)
calculation_status     = NOT_RUNNING | SUCCESS | FAILED | DEGRADED
source_status          = NONE | LIVE_MARKET_STATE | REPLAY | UNAVAILABLE
feature_availability   = AVAILABLE | STALE_CACHE | UNAVAILABLE | NOT_ACTIVE
causal_state           = VALID | STALE | INVALID | NOT_APPLICABLE
model_compatibility    = PASS | BLOCK | UNKNOWN | NOT_APPLICABLE (disabled)
schema                 = canonical 70D scalp_v3 layout when the 70D series is
                         the active integration contract; liquidity ALWAYS at
                         60..69 via the authoritative registry
last_update            = real wall-clock ISO-8601 (or explicit None)
feature index          = from schema_contract registry, never derived
```
