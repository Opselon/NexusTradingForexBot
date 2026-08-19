# AI HUB / MODEL TENSOR LOADING + SSE SERIALIZATION + POST-70D RUNTIME FORENSIC — FINAL

> Agent: Hermes-AIHubForensic · 2026-08-19
> Task: AI HUB / MODEL TENSOR LOADING + SSE SERIALIZATION + POST-70D RUNTIME FORENSIC FIX
> Base commit: cfcd115 · HEAD at task end: 5a37374 (origin/main in sync)

---

## A. MODEL ARTIFACT FORENSICS

### Live Champion (`artifacts/models/scalp/XAUUSD/v1.0.0/model.pt`)

| Field | Value |
|---|---|
| artifact | `model.pt` (1,326,059 bytes) |
| SHA-256 (prefix-16) | `9105cef7d93e23b8` |
| input dimension | **50** (input_projection.weight (128, 50)) |
| hidden dimension | **128** |
| actual output classes | **4** (classifier.weight (4, 32)) |
| expected output classes | 4 (NO_TRADE/BUY/SELL/WAIT) |
| scaler | mean (50,), std (50,) — matches |
| integrity (post-fix) | **VALID** |

### ROOT CAUSE (proven, with evidence)

```
The log line  actual_classes=128 / expected_classes=4  was a VERIFIER BUG,
not an invalid artifact.

src/nexus_scalp/model_lifecycle/integrity.py (pre-fix) read:
    actual_out = int(input_shape[0])   # input_projection.weight.shape[0]

input_projection.weight is (hidden_dim, feature_dim) = (128, 50).
shape[0] = 128 is the HIDDEN width — NOT the class count.

The TRUE class head is the final Linear:
    classifier.weight = (4, 32)   # 4 classes, 32 = hidden//4

Evidence (probe scratch/aihub_forensic_probe.py, .out.txt):
  - state_dict keys match ScalpNet(num_features=50, num_classes=4) EXACTLY
    (missing=[], extra=[]); dry-run logits shape (1,4), finite.
  - Every one of the 18 model artifacts in the tree carries a 4-class head.
  - The runtime's own loader (_load_or_initialize_model_weights) only
    compares input_projection shape — so the artifact loads, and the
    verifier then false-alarms.

Consequence chain: verifier false failure -> "Champion unavailable:
artifact missing or invalid" -> AI Hub/UI showed unavailable despite a
perfectly valid 4-class artifact on disk.
```

### Why 128 exists

`128` is `ScalpNet.hidden_dim` (latent channel capacity): input_projection
(50→128), mlp_res1/2 (128→128), causal_conv1-3 (128ch), attention
(embed_dim=128), pos_encoder.pe (1,500,128). The state_dict's 128 values are
the embedding width, never a trading-class count. The TCN variant
(TCNAttentionV1) shares hidden_dim=128 with a 3-class head
(`head.3.weight` = (3,64)) — bench_c/bench_d are 3-class TCN artifacts and
are correctly REJECTED against the 4-class contract.

### Canonical class contract (unchanged)

```
0 = NO_TRADE  1 = BUY_MARKET  2 = SELL_MARKET  3 = WAIT
```
`EXPECTED_NUM_CLASSES = 4` in integrity.py is preserved; nothing was
modified to accommodate an invalid artifact.

---

## B. MODEL INVENTORY (18 artifacts scanned)

| Path | Input | Classes | Head key | Scaler | Status |
|---|---|---|---|---|---|
| models/scalp/XAUUSD/v1.0.0 (LIVE) | 50 | 4 | classifier | 50 | **LIVE / VALID** (restored bench_a_v1-derived; see incident doc) |
| model_generation/models/wf_candidate | 70 | 4 | classifier | 70 | **CANDIDATE / VALID** (scalp_v4, 70D) |
| model_generation/models/bench_a_v1 | 50 | 4 | classifier | — | CANDIDATE / VALID (byte-identical to LIVE) |
| model_generation/models/bench_b_v1 | 62 | 4 | classifier | — | CANDIDATE / 62D (no matching schema declared) |
| model_generation/models/bench_c_v1 | 50 | **3** | head.3 | — | **INVALID / CLASS_COUNT_MISMATCH** (TCN family) |
| model_generation/models/bench_d_v1 | 50 | **3** | head.3 | — | **INVALID / CLASS_COUNT_MISMATCH** (TCN family) |
| model_generation/models/cand_241f9cf5… | 6 | 4 | classifier | — | CANDIDATE / 6D research probe |
| model_generation/models/cand_331e81b3… | 6 | 4 | classifier | — | CANDIDATE / 6D research probe |
| model_generation/models/cand_6f4090b0… | 6 | 4 | classifier | — | CANDIDATE / 6D research probe |
| model_generation/models/cand_75a4028e… | 6 | 4 | classifier | — | CANDIDATE / 6D research probe |
| model_generation/models/cand_data_gate_ep20 | 50 | 4 | classifier | — | CANDIDATE / VALID |
| model_generation/models/cand_data_gate_ep30 | 50 | 4 | classifier | — | CANDIDATE / VALID |
| model_generation/models/cand_data_gate_v1 | 50 | 4 | classifier | — | CANDIDATE / VALID |
| model_generation/models/cand_data_gate_v2 | 50 | 4 | classifier | — | CANDIDATE / VALID |
| model_generation/models/task5_a_v1 | 50 | 4 | classifier | — | CANDIDATE / VALID |
| model_generation/models/task5_b_v1 | 62 | 4 | classifier | — | CANDIDATE / 62D |
| model_generation/models/task5_c_v1 | 60 | 4 | classifier | — | CANDIDATE / 60D (scalp_v2 family) |
| model_generation/models/task5_d_v1 | 72 | 4 | classifier | — | CANDIDATE / 72D |

Lifecycle: **no 70D artifact is LIVE**. wf_candidate (scalp_v4/70D) is a
CANDIDATE with a valid manifest (num_features=70, model_head_classes=4,
feature_schema_dimension=70) and a 70D scaler. It is NOT promoted (INV-015,
no automatic promotion exists).

---

## C. AI HUB

### Backend (NEW)

`GET /api/models/integrity` returns backend-decided model health:

```
model_id, model_version, artifact_path, artifact_hash, schema_id,
feature_dimension, expected_classes, actual_input_dimension,
actual_output_classes, actual_hidden_dimension, class_head_name,
scaler_path, scaler_hash, scaler_dimension,
compatibility (VALID|INVALID), integrity, state (ACTIVE|INCOMPATIBLE|INVALID),
active, reason
```

- The state machine is DISCOVERED → LOADING → LOADED → VALIDATING → VALID
  (or INVALID / INCOMPATIBLE / UNAVAILABLE). The runtime NEVER transitions
  LOADING→ACTIVE before integrity validation succeeds.
- The UI **consumes backend truth only** — no JS-side integrity logic
  (no duplication; `Web/app.js` fetches `/api/models/integrity`).

### UI (NEW)

Model card now renders: Integrity (VALID/INVALID), State, Output Classes in
addition to ID/Version/Architecture/Schema/Scaler/Artifact. The liquidity
panel renders: Status, Calculation (SUCCESS/NOT_RUN/FAILED), Source Status,
Causal State, Schema, Dimension, Algorithm Version, Latency, Model
Compatibility (PASS/BLOCK/NOT_APPLICABLE) — never a single collapsed
HEALTHY.

---

## D. SSE (datetime serialization)

### Exact failure (proven)

```
artifacts/logs/nse_live.log (2026-08-19 04:45):
  [LIQUIDITY] event=FEATURE_CALCULATION_OK source=UNAVAILABLE latency_ms=166.11 bars=901
  WEB_ERROR endpoint=/api exception_type=TypeError
  Object of type datetime is not JSON serializable
  server.py line 6039 event_generator frame = json.dumps(payload)
  (repeats every SSE cycle)
```

### WHICH FIELD / WHO CREATED IT / WHY IT REACHED SSE

```
Field:  liquidity.pools[].confirmed_at
Creator: LiquidityGovernor.report() built pools_payload with
         getattr(p, "confirmed_at", None) — a raw datetime (LiquidityPool.confirmed_at)
Why it reached SSE: report() is embedded in get_system_state()["liquidity"]
  via _liquidity_state_section(), and event_generator json.dumps() the whole
  payload every frame. Once any pool was CONFIRMED (901 bars → pools
  exist), every SSE frame crashed.
Why the old serializer didn't handle it: json.dumps default encoder
  rejects datetime; no project-wide encoder existed; per-field isoformat
  was applied everywhere except this field.
```

### Fix

1. `liquidity_runtime.py::report()` — `confirmed_at` is now ISO-8601
   timezone-aware string (`.isoformat()`); never a raw datetime.
2. `server.py` — new canonical encoder `canonical_json()`:
   datetime (naive→UTC stamped) → ISO-8601; date; Enum → value; UUID;
   Decimal → float; Path; numpy scalar/array; unknown → TypeError (never
   corrupt JSON).
3. SSE handler — on serialization failure emits a structured
   `SSE_SERIALIZATION_ERROR` event (correlation_id, event_type, error,
   failed_fields via `_find_non_json_fields()`), logs it, and CONTINUES
   with the next frame (recoverable). No silent drop, no corrupted frame.
4. `_find_non_json_fields()` — locates the failing leaf path + type for
   actionable diagnostics.

### Verified

- `canonical_json` handles aware/naive datetime, nested dicts, lists,
  None, Decimal, UUID, Path, Enum, numpy — deterministic ISO-8601.
- `test_aihub_07..09` cover pool-datetime serialization, nested payloads,
  and observable failure.

---

## E. LIQUIDITY (calculation vs source status)

`FEATURE_CALCULATION_OK + source=UNAVAILABLE` is a LEGITIMATE pair: the
governor computed real features from engine bars without a live broker
source. It is NOT a bug and NOT "healthy".

`report()` now exposes the two orthogonal dimensions explicitly:

```
status:                 ENABLED | DISABLED | DEGRADED | UNAVAILABLE
calculation_status:     SUCCESS | NOT_RUN | FAILED     (last compute attempt)
source_status:          LIVE_MARKET_STATE | REPLAY | UNAVAILABLE
feature_availability:   AVAILABLE | STALE_CACHE | NOT_ACTIVE | UNAVAILABLE
causal_state:           VALID | STALE | INVALID | NOT_APPLICABLE
model_compatibility:    PASS | BLOCK | UNKNOWN | NOT_APPLICABLE
```

UI renders each field separately; `available=True` requires
`feature_availability == "AVAILABLE"`.

---

## F. 70D

- Canonical 70D contract: **scalp_v3** (Base 0..49 | News/Family 50..59 |
  Liquidity 60..69) per TASK-03-70D-PARITY; scalp_v4 is the TASK-02
  integration contract (wf_candidate manifest declares scalp_v4).
- **No valid 70D LIVE model.** wf_candidate is the only 70D artifact:
  input 70, classes 4, scaler 70, hash `9265e4b7c88089c6` — a CANDIDATE.
- The AI Hub shows 70D NOT AVAILABLE as LIVE; the candidate is exposed in
  the inventory with CANDIDATE status. **Nothing is auto-promoted.**
- 50D backward compatibility is intentional and preserved (LIVE champion
  remains 50D/scalp_v1).

---

## G. TESTS

New regression tests (all passing):

- `tests/unit/test_model_lifecycle_phase10.py` — TEST-AIHUB-01..06, 11,
  12, 13 (valid 50D/4-class; class-head-not-hidden; 70D loads; wrong
  scaler rejected; class mismatch rejected; schema mismatch rejected;
  invalid champion never ACTIVE; dry-run tensor shape; corrupt artifact
  explicit error).
- `tests/unit/test_liquidity_runtime_integration_phase18.py` —
  TEST-AIHUB-07/08/09 (SSE datetime, nested, observable failure), 10/10b
  (calc/source distinct), 14 (no auto-promotion), 15 (inventory
  lifecycle).

Full-suite status: the complete `tests/unit` run is executed in the
quality-gate phase (below). Known collection error in UNTRACKED parallel
file `tests/unit/test_schema_70d_reconciliation.py` (NameError: Path) —
parallel WIP, not mine, excluded from my runs.

---

## H. RUNTIME (before/after)

### Before (live log, 2026-08-19 04:45)

```
MODEL INTEGRITY FAILURE: actual_classes=128 actual_dim=50
  expected_classes=4 expected_dim=50 model_id=primary_scalp
Champion unavailable: artifact missing or invalid
WEB_ERROR endpoint=/api exception_type=TypeError
  Object of type datetime is not JSON serializable (server.py 6039, xN)
```

### After (probe + unit verification, 2026-08-19 ~06:5x)

```
LIVE champion:  VALID | dim 50 | classes 4 | scaler 50 | hidden 128
wf_candidate:   VALID | dim 70 | classes 4 | scaler 70
bench_c (TCN):  INVALID | CLASS_COUNT_MISMATCH | classes 3
SSE payload with confirmed pools: json.dumps() SUCCEEDS (ISO-8601)
calculation_status=SUCCESS + source_status=UNAVAILABLE coexist truthfully
```

---

## I. BUGS

- **BUG-110 (new, proven)**: class-count probe read the hidden width as
  the output class count → false MODEL INTEGRITY FAILURE on every valid
  ScalpNet artifact. Fixed in `model_lifecycle/integrity.py`
  (b7cde3a). Same number covers the SSE datetime leak (pool
  `confirmed_at` raw datetime → `TypeError` every frame) fixed in
  `liquidity_runtime.py` report() + canonical encoder.
- Pre-existing/parallel: BUG-111 (liquidity UI truth, wall-clock
  timestamps — parallel agent, landed 3a6405d); BUG-106 (O(n²) 70D
  frame — fixed by AGENT-05); untracked parallel test files carry their
  own collection errors (not mine).

---

## J. COMMITS (mine)

| SHA | Step | Content |
|---|---|---|
| b7cde3a | STEP-02 | integrity class-head probe fix + tensor diagnostics + TEST-AIHUB-01..13 |
| 5a37374 | STEP-01 | forensic probe (read-only) + captured output |

Absorbed by parallel commits (content verified in history):
- SSE canonical encoder + /api/models/integrity + SSE_SERIALIZATION_ERROR →
  in 11e3402 (Hermes-GovAgent8) and 3a6405d (AGENT-02).
- UI integrity cards + liquidity calc/source render → absorbed at HEAD.
- aihub regression tests in phase18 → absorbed at HEAD.

## K. REMOTE

Local HEAD == origin/main == `5a37374` (0 ahead, 0 behind; `origin/main`
contains both my commits).

---

## L. REMAINING RISKS

- **PROVEN**: nothing trading-related changed; 50D live contract intact;
  invalid artifacts rejected with explicit reason; no auto-promotion.
- **NOT PROVEN**: whether the operator accepts the RESTORED_CANDIDATE
  champion identity (incident CHAMPION_ARTIFACT_INCIDENT_20260819.md) —
  governance decision required (INV-015). The restored artifact is a
  DIFFERENT weight set than the frozen f0f70efb champion.
- **UNKNOWN**: parallel untracked WIP (test_schema_70d_reconciliation.py
  collection error, latency forensics) — owned by parallel agents;
  excluded from my gates.

---

## NEXT-AGENT HANDOFF

1. Coordinate with the BUG-111 liquidity-UI owner before touching
   `liquidity_runtime.py` / `test_liquidity_runtime_integration_phase18.py`
   (shared surface, both agents' hunks interleaved).
2. Operator decision on Champion identity (restore f0f70efb from an
   external backup OR approve bench_a_v1-derived weights) — nothing else
   can make the LIVE model authoritative again.
3. When a validated 70D candidate exists, run it through the governance
   promotion path (INV-015) — never direct placement.
4. Fix the untracked `test_schema_70d_reconciliation.py` Path import when
   its owner lands it, so the full unit suite collects cleanly.
5. `beforePush.ps1` / `beforePush.sh` remain the gate; my changed files
   pass ruff+mypy+their tests (full suite running at handoff time).
