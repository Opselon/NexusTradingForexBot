# Canonical Contract Audit Report — 70D / 32 / 3-Class (ScalpNet v3)

> **Scope:** `src/nexus_scalp/model_generation/` (three_model.py, schema_v2.py, sequence.py, architectures.py, temporal_contract.py) vs live inference path `src/nexus_scalp/features/` and `src/nexus_scalp/models/` / serving code. Searches for hardcoded `50`, `70`, `32`, `4 classes`, `scalp_v4`, `FEATURE_DIM`, `SEQ_LEN`, `N_CLASSES`, `ACTIVE_SCHEMA`.
> **Worktree:** `C:/Users/Capsizer/source/repos/NexusTradingForexBot/.worktrees/subagent-sa-2-628685f3` | branch `hermes-subagent/subagent-sa-2-628685f3` | date `2026-09-04T15:04+03:30` (Iran)
> **Canonical declared by task:** 70D, seq 32, 3 classes, ScalpNet v3. Legacy remnants to flag: 50D / 4-class / scalp_v4.

---

## 1. Executive Summary

| Contract axis | Declared canonical (task) | Single Source of Truth (SSoT) file | Canonical value | Live ACTIVE value | Conflict? |
|---|---|---|---|---|---|
| **Feature dimension** | 70D | `model_generation/temporal_contract.py` (`FEATURE_DIM=70`) + `features/schema_contract.py` (`DIMENSION=70`, `SCHEMA_ID=scalp_v3`) | **70** (`scalp_v3`) | **50** (`scalp_v1`, `ACTIVE_SCHEMA_ID` in `features/schema.py:95`) | **INTENTIONAL LAG — documented** (live still 50D; 70D is candidate-only) |
| **Sequence length L** | 32 | `model_generation/temporal_contract.py` (`CANONICAL_SEQ_LEN=32`) aliased by `model_generation/sequence.py` | **32** (alt 16 still valid per artifact) | **32** (`application/live_engine.py:468` → `CANONICAL_SEQ_LEN`) | ✅ Clean — one literal, aliases only |
| **Class count** | 3 | `model_generation/architectures.py` (`CANONICAL_CLASS_COUNT=3` + `LABEL_SCHEMA_3CLASS_V1`) and `model_generation/models.py` (`LABEL_SCHEMA_3CLASS_V1`) | **3** (`NO_TRADE/BUY/SELL`; WAIT is policy-only) | **4 tolerated** (governance/load-gate + serving compat) | ⚠️ Dual — canonical 3, legacy 4 kept explicitly |
| **Schema id** | `scalp_v3` | `features/schema_contract.py` + `features/schema.py` registry | `scalp_v3` (70D) | `scalp_v1` (`ACTIVE_SCHEMA_ID`) | Intentional (see §2) |

**Bottom line:** 70D/32/3 is fully unified as a *training+candidate* contract. The live `ACTIVE_SCHEMA_ID = scalp_v1 / 50D` is a deliberate, loudly documented lag — the forensics checks (INV-70D-*, §7) and `assert_canonical_registry()` enforce that it stays `scalp_v1` until a governance promotion. The only hard conflicts are legacy literals that *should exist* but are well-fenced; a few hygiene spots still hardcode `50` outside the SSoT (flagged in §5).

---

## 2. Dimension — FEATURE_DIM / ACTIVE_SCHEMA — Single Source of Truth vs Live

### 2.1 SSoT graph (70D)

```
features/schema.py
  FEATURE_SCHEMAS registry (all schemas,append-only)
    scalp_v1  50D  is_active=True   ← LIVE contract
    scalp_v3  70D  supersedes scalp_v1
    scalp_v4  70D  supersedes scalp_v1  (family-variant, see §6)
    scalp_v2  60D, scalp_liquidity_v1 60D, scalp_v4_temporal_candidate 92D  (research)

features/schema_contract.py   ← CANONICAL 70D single source of truth (TASK-03)
  SCHEMA_ID="scalp_v3", DIMENSION=70,
  BASE 0..49 | NEWS 50..59 | LIQUIDITY 60..69,
  canonical_feature_names(), family_of(), feature_schema_hash() (SHA-256)

features/features70.py        ← assembly reusing schema_contract
  assemble_70d(base50, news10, liquidity10) → Feature70Snapshot (DIMENSION=70)

model_generation/temporal_contract.py
  FEATURE_DIM=70  (comment: "Canonical 70D feature dimension (scalp_v3)")
  CANONICAL_SCHEMA_ID="scalp_v3"

model_generation/sequence.py  ← NO duplicate literal
  from temporal_contract import FEATURE_DIM, CANONICAL_SEQ_LEN as SEQUENCE_LENGTH
  @dataclass SequenceContract: feature_dim=FEATURE_DIM, sequence_length=SEQUENCE_LENGTH
  doc: "frozen ALIASES re-exported from temporal_contract — one direction only"

model_generation/three_model.py, schema_v2.py, replay.py, training.py, etc.
  all derive dimension from registry / schema_contract / temporal_contract
```

### 2.2 Live ACTIVE contract (the lag)

- `features/schema.py:95` — `ACTIVE_SCHEMA_ID: str = "scalp_v1"` — **the ONLY place the live dimension is declared** (invariant 1 in that module).
- `features/schema.py:144` — `FEATURE_SCHEMAS.active` returns `scalp_v1 / 50D`.
- `features/schema_contract.py:283` — `if ACTIVE_SCHEMA_ID != "scalp_v1": raise` — the 70D contract itself protects the legacy live contract.
- `forensics/checks_features.py:377` — documents ACTIVE as a known LAG.
- `application/live_engine.py:216` — `FEATURE_DIM = active_dimension()` (reads from registry; `class FEATURE_DIM is kept so first-time users still bootstrap 50D`).
- `application/live_engine.py:237-292` — `effective_feature_dim` resolution order: scaler width > model tensor band; never a bare `50`.
- `experience/models.py:41` — `CANONICAL_FEATURE_DIMENSION: int = 50` — the *experience/memory* layer's canonical (separate subsystem; not the model contract — documented as legacy).
- `release/packaging.py`, `release/versioning.py`, `release/metadata.py` — migrations keyed on `ACTIVE_SCHEMA_ID` (so a flip to `scalp_v3` is a config switch, not a code hunt).

**Verdict:** SSoT is clean. Two co-existing truths by design: **candidate 70D (`scalp_v3`)** and **live 50D (`scalp_v1`)**. No competing `50` literal drives training geometry; all training geometry resolves via `FEATURE_SCHEMAS.resolve(schema_id).dimension` or `temporal_contract.FEATURE_DIM`.

### 2.3 Where hardcoded 50/70 literals still exist (grep)

Hardcoded `50` literals that are **acceptable** (bounded, not geometry-defining):
- `model_generation/architectures.py:99` — `input_dim: int = 50` — default param of `TCNAttentionV1`; overridden by factory via `feature_schema.dimension`.
- `models/scalp_net.py:112` — `num_features: int = 50` — legacy serving default (compat; fresh builds pass explicit dim).
- `model_generation/schema_v2.py:179,255,358,442,530,539,656,774,788` — `range(50)`, `>50`, `LIQUIDITY_EXTRA_START=50`, `NEWS_10D_START=50`, `len(feat_cols)==70` — structural constants of the 70D builder (Base|News|Liquidity split); correct to be literals there (they define the layout).
- `adapters/database/audit_repository.py:641,656,758,1193`, `model_lifecycle/store.py:73`, `shadow/store.py:152` — `feature_dimension INTEGER DEFAULT 50` — DB DDL defaults (harmless; rows carry explicit dim).
- `experience/models.py:41` — `CANONICAL_FEATURE_DIMENSION = 50` — memory-layer contract (separate from model contract; flagged in §5 as a divergence to keep visible).

Hardcoded `70` literals that are **acceptable**:
- `model_generation/temporal_contract.py:83` — `FEATURE_DIM=70` (SSoT literal — the one place).
- `features/schema_contract.py:65` — `DIMENSION=70` (contract literal).
- `model_generation/schema_v2.py:788` / `three_model.py:282,360` — validation/assertion strings referencing 70D.

**No competing `FEATURE_DIM` definition found** — grep `FEATURE_DIM|SEQ_LEN|N_CLASSES|ACTIVE_SCHEMA` across `src/` returns exactly the SSoT graph above (0 competing literal definitions; `SEQ_LEN`/`N_CLASSES` names do not exist as symbols — they are `CANONICAL_SEQ_LEN` / `class_count` / `num_classes` — see §3/§4).

---

## 3. Sequence Length — SEQ_LEN / CANONICAL_SEQ_LEN

### 3.1 SSoT

| File | Symbol | Value | Role |
|---|---|---|---|
| `model_generation/temporal_contract.py:88-90` | `CANONICAL_SEQ_LEN_DEFAULT_16=16`, `CANONICAL_SEQ_LEN_DEFAULT_32=32`, `CANONICAL_SEQ_LEN=CANONICAL_SEQ_LEN_DEFAULT_32` | **32** | **Authoritative** (TASK ARCH-SEQ-UNIFY) |
| `model_generation/sequence.py:103` | `SEQUENCE_LENGTH = CANONICAL_SEQ_LEN` (alias) | 32 | Re-export, no literal |
| `model_generation/sequence.py:97-101` | `MAX_GAP_US`, `SCHEMA_ID`, `FEATURE_DIM` | from `temporal_contract` | Aliases |
| `model_generation/sequence.py:159` | `SEQUENCE_CONTRACT = SequenceContract()` | L=32 C=70 gap=10min | Singleton |
| `training/walk_forward_trainer.py:1515-1525` | `from temporal_contract import CANONICAL_SEQ_LEN` | 32 | Training + meta fallback |
| `application/live_engine.py:468-525` | `self._live_sequence_seq_len = 32`, `CANONICAL_SEQ_LEN`, `meta_declared_seq_len()` | 32 / artifact `seq_len` | Live: artifact `seq_len` wins, else 32 |

Additional contract fields: `CANONICAL_MAX_GAP_US=10*60*1_000_000`, `CANONICAL_PURGE_BARS=15`, `CANONICAL_EMBARGO_BARS=15`, `CANONICAL_HTF_TIMEFRAMES_MIN=(60,240,1440)` — all in `temporal_contract.py`.

### 3.2 Consumers

- `model_generation/sequence_training.py:60-72` — `SequenceBuilder(seq_len=None → SEQUENCE_CONTRACT.sequence_length)`.
- `model_generation/benchmark.py:99,326` — ablation matrix uses `seq_len=16` explicitly (allowed: alternate valid value per artifact).
- `cli/doctor.py:1501` — `SequenceBuilder(seq_len=16)` smoke probe (alternate).
- `model_generation/architectures.py:106` — `max_seq_len=64` (positional-embedding bound, not L).
- `model_generation/temporal_contract.py:126-151` — `get_canonical_sequence_builder(seq_len=CANONICAL_SEQ_LEN)`, `validate_sequence_tensor_shape(x, (B,L,70))`, `meta_declared_seq_len(meta)`.

### 3.3 Conflicts

- **None** on the canonical value. `32` appears as one canonical literal (`CANONICAL_SEQ_LEN_DEFAULT_32=32`) plus docs/tests (F2 harness ccb7765c). No second competing `32` definition.
- **Two valid L values (16, 32)** is intentional per-artifact (`temporal_contract.py:15-19`): artifact `meta.json: seq_len` + `temporal_contract` block wins; inference validates with `expected_seq_len`.
- Import-cycle guard: `temporal_contract` is authoritative; `sequence` comment block explicitly forbids adding a second literal.

---

## 4. Class Count — N_CLASSES / num_classes / class_count

### 4.1 SSoT

| File | Symbol | Value | Role |
|---|---|---|---|
| `model_generation/models.py:47-64` | `LABEL_SCHEMA_3CLASS_V1: {class_count:3, class_names:[NO_TRADE,BUY,SELL], numeric_mapping:{0,1,2}}` | **3** | **Authoritative label contract** |
| `model_generation/architectures.py:48-49` | `CANONICAL_CLASS_COUNT=3`, `CANONICAL_CLASSES=[NO_TRADE,BUY,SELL]` | 3 | Neural head SSoT (MLFIX-T4) |
| `model_generation/model_factory.py:29-31` | `LEGACY_HEAD_CLASSES=4`, `CONTRACT_3CLASS=3`, `CANONICAL_CLASS_COUNT=3` | 3 / 4-legacy | Factory contract |
| `model_lifecycle/integrity.py:37-38` | `EXPECTED_NUM_CLASSES=3`, `LEGACY_EXPECTED_NUM_CLASSES=4` | 3 / 4-legacy | Gate contract |
| `training/walk_forward_trainer.py:144-152` | `NUM_CLASSES=3`, `CANONICAL_NUM_CLASSES=3`, `LEGACY_HEAD_CLASSES=4`, `TRAINED_CLASS_COUNT=3` | 3 / 4-legacy | Trainer contract |
| `domain/enums.py:35-40` | `ActionType.WAIT/NO_TRADE/BUY_MARKET/SELL_MARKET` | 4 enum members | Policy layer (WAIT derived) |
| `labeling/triple_barrier.py:8,86,145` | `0=NO_TRADE,1=BUY,2=SELL; WAIT derived` | 3 labels | Labeling |

### 4.2 Live vs training

- **Training:** all fresh builds emit 3 logits (`TCNAttentionV1` head is always `num_classes=3`; `WalkForwardTrainer.model = ScalpNet(CANONICAL_NUM_CLASSES=3)`). `training.py:251` uses `minlength=3`; `sequence_training.py:190` uses `experiment.class_count or 3`.
- **Serving:** `models/scalp_net.py:113` — `num_classes: int = 4` — **legacy serving default** (comment: `MLFIX-T4: NO_TRADE/BUY/SELL + WAIT policy bridge`). `application/live_engine.py:1082,2751,6016` — `ScalpNet(num_classes=4)` / `num_classes=4` in inference path — legacy path for existing artifacts.
- **Governance:** `governance/load_gate.py:247` — `if class_count not in (3,4)` + `model_lifecycle/model_class_contract.py` — 4→3 WAIT masking helpers (`_mask_wait_logit`, `map_4_to_3`) — explicit bridge.
- **Manifest:** `model_generation/models.py:285,405` — `ModelManifest.class_count: int = 3`, `ExperimentConfig.class_count: int = 3`; `training/walk_forward_trainer.py:1533` — `num_classes/model_head_classes = CANONICAL_NUM_CLASSES=3` in meta.

### 4.3 Conflicts

- **No competing training definition** — every trainer/factory/lifecycle file reads 3 from the SSoT (or `experiment.class_count` which defaults to 3). Grep `class_count.*4` / `NUM_CLASSES.*4` hits only the legacy bridge constants above.
- **Live 4-class heads are legacy artifacts**, not a competing contract. They are tolerated by gates and rejected by fresh-build gates unless `allow_legacy_4` is passed (`model_lifecycle/integrity.py:324-334`).
- **Recommendation (no code change in this audit):** keep `ScalpNet(num_classes=4)` default annotated as legacy; new code must never call `ScalpNet()` without explicit `num_classes=3` — the audit confirms current fresh paths already do.

---

## 5. Feature Ordering / Label Maps / Scaler Formats — Duplicates vs Single Source

### 5.1 Feature ordering — ✅ Single source

| Family | SSoT | Indices | Notes |
|---|---|---|---|
| Base 50D | `features/scalp_features.py:163` `FEATURE_NAMES` (50) + `NUM_FEATURES` | 0..49 | Protected contract; `validate_70d_vector` / `assert_canonical_registry` guard drift |
| News 10D | `features/schema_contract.py:77-88` `NEWS_10D_NAMES` = fields 0..8 + index 10 (`news_state`) of `news_context_v1` | 50..59 | NOT blind first-10; `source_consensus` (idx 9) stays outside 70D by contract |
| Liquidity 10D | `features/schema_contract.py:92-103` `LIQUIDITY_10D_NAMES` = `liquidity_engine.LIQUIDITY_FEATURE_NAMES` | 60..69 | Identical to `LiquidityFeatures.as_vector()` |

**No duplicate ordering** — every consumer derives from `schema_contract.canonical_feature_names()`:
- `features/features70.py` — `assemble_70d` validates 50/10/10 split.
- `model_generation/schema_v2.py:539-659,774` — `NEWS_10D_START=50`, `compute_70d_frame` validates `dimension==70`.
- `model_generation/runtime.py`, `application/live_engine.py`, `features/runtime70.py`, `features/liquidity_runtime.py` — all import `canonical_feature_names` / `feature_schema_hash`.
- Forensics `references.py:171` `LIQUIDITY_70D_FEATURE_NAMES` re-derives 60..69 from the same tuple (checked in `checks_features`).

### 5.2 Label maps — ✅ Single source, explicitly bridged

- `model_generation/models.py:47-53` — `LABEL_SCHEMA_3CLASS_V1.numeric_mapping = {NO_TRADE:0, BUY:1, SELL:2}` — the only label map.
- `labeling/triple_barrier.py:145,231` — encodes with same 0/1/2 codes.
- `model_generation/architectures.py:48-49` — `CANONICAL_CLASSES = [NO_TRADE,BUY,SELL]` (same).
- Legacy 4-class (`WAIT=3`) lives only in `domain/enums.py` + `model_lifecycle/model_class_contract.py` as a **policy bridge** (`WAIT is derived in policy, never a training label` — `architectures.py:9`). `model_class_contract.py:114-171` provides `_mask_wait_logit` / `_map_probs` that zero/mask logit 3.

### 5.3 Scaler format — ✅ Single format, pre-train purity enforced

- **Format everywhere:** `np.savez(path, mean=..., std=...)` / `np.load(path)["mean"/"std"]` — one format across `artifact_store.py`, `model_lifecycle/*`, `training/walk_forward_trainer.py`, `application/live_engine.py:2785-2826`, `model_generation/training.py:202`, `model_lab/trainer.py:265`.
- **Dimension binding:** `mean.shape[0] == std.shape[0] == feature_dimension` asserted at save and at every load (`live_engine._load_scaler_artifacts:2805`, `governance/verify.py:170`, `model_lifecycle/integrity.py:350`).
- **Schema hash binding:** `model_generation/models.py:324-385` `ModelManifest.scaler_hash + feature_schema_hash`, `artifact_store.py:219` `sha256_file(scaler_path)`, `governance/verify.py:181-186` `feature_schema_hash_matches` gate, `features/inference_validator.py:231-248` `ScalerContract(dimension, hash)`.
- **Deduplication:** `application/live_engine.ScalerBundle` (`mean/std`, `is_ready`, `dimension()`, `transform(x) → clip [-5,+5]`) is the live wrapper; no second wrapper exists.

---

## 6. scalp_v4 / 70D family — What it is, what it isn't

| Item | File | Finding |
|---|---|---|
| `scalp_v4` registry entry | `features/schema.py:209-228` | **70D, candidate-only**, `BASE 0..49 | FAMILY 50..59 | LIQUIDITY 60..69` (TASK-02 integration contract). Identified as `70D_FAMILY` in `features/liquidity_runtime.py:241-242` alongside `scalp_v3`. |
| Liquidity runtime family map | `features/liquidity_runtime.py:228-242` `_SCHEMA_IDS_COMPATIBLE_WITH_70D={"scalp_v3","scalp_v4"}` | `scalp_v4` passes SCHEMA_DIMENSION_MATCH with a 70D runtime. |
| Release artifact versions | `release/model_artifacts.py:100-103` `scalp_v4: 1.0.0` | Brief-37 says `scalp_v1..scalp_v4 all included` (brief migration note). |
| Superseded guard | `release/model_artifacts.py:274` `if schema_id in ("scalp_v2","scalp_v4","scalp_liquidity_v1")` | `scalp_v4` treated as superseded in some release paths (legacy of family experiment). |
| Shadow 70D | `shadow/shadow70/models.py:6,31` | Shadow moved runtime to `scalp_v4` for news-family canonicalization; `SHADOW70_DIMENSION=70`, `SHADOW70_SCHEMA_ID=scalp_v4` (candidate shadow, never champion). |
| Canonical 70D | `features/schema_contract.py:63` `SCHEMA_ID="scalp_v3"` | **The canonical 70D is `scalp_v3`, not `scalp_v4`.** `scalp_v4` is a family alias with identical dimension, kept for compatibility. |
| `scalp_v4_temporal_candidate` | `features/schema.py:250` `dimension=92` | Research 70D+22 temporal dims (never ACTIVE — brief 45). |

**Deduplication assessment:** `scalp_v3` and `scalp_v4` share dimension 70 and the same base/liquidity split; they differ only in the documented semantics of indices 50..59 (`scalp_v3`: news_context_v1 first-10; `scalp_v4`: TASK-5/TASK-1 family — see registry descriptions). The 70D SSoT (`schema_contract`) pins `scalp_v3`; `scalp_v4` is retained as a compatibility family id and is **not** a competing canonical.

---

## 7. Hardcoded 50 / 70 / 32 / 4 — Findings Summary

| Literal | Where it is SSoT | Where it appears as legacy/compat (acceptable) | Where it appears as a *competing* definition (conflict) |
|---|---|---|---|
| `50` | `features/scalp_features.py:163` `FEATURE_NAMES` (50) | `models/scalp_net.py:112` default, `model_generation/*: BASE 50D split`, DB `DEFAULT 50` | **None** — no second `FEATURE_DIM=50` definition |
| `70` | `temporal_contract:FEATURE_DIM=70` + `schema_contract:DIMENSION=70` | `schema_v2.py: dimension==70 checks` | **None** — no second `FEATURE_DIM=70` definition |
| `32` | `temporal_contract:CANONICAL_SEQ_LEN=32` | `benchmark: L=16 ablations`, `walk_forward_trainer: CANONICAL_SEQ_LEN=32` | **None** — `sequence.py` is an alias, not a redefinition |
| `4` (classes) | `domain/enums:WAIT` + `LEGACY_HEAD_CLASSES=4` constants | `models/scalp_net: num_classes=4` default, `live_engine: num_classes=4` for legacy artifacts, `governance: allow (3,4)` | **None as training contract** — canonical is 3 everywhere; 4 is fenced as legacy |

---

## 8. Duplicate vs Deduplicated — Scoreboard

| Concern | Status | Evidence |
|---|---|---|
| Duplicate `FEATURE_DIM` literal | ✅ **Deduplicated** | One literal `70` in `temporal_contract.py:83`; `sequence.py` aliases it (`No duplicate definitions` comment). |
| Duplicate `SEQ_LEN` literal | ✅ **Deduplicated** | One canonical `CANONICAL_SEQ_LEN=32`; `sequence.py`, `walk_forward_trainer`, `live_engine` all import/alias. |
| Duplicate `N_CLASSES` / `class_count` | ✅ **Deduplicated** | One canonical `class_count=3` (`LABEL_SCHEMA_3CLASS_V1`); legacy `4` lives in named `LEGACY_*` constants only. |
| Duplicate feature ordering | ✅ **Deduplicated** | `schema_contract.canonical_feature_names()` is the only ordered tuple; all producers/consumers import it. |
| Duplicate label map | ✅ **Deduplicated** | `LABEL_SCHEMA_3CLASS_V1.numeric_mapping` is the only map; legacy WAIT is policy-derived, not a label. |
| Duplicate scaler format | ✅ **Deduplicated** | One format `npz(mean,std)` + `scaler_hash`/`feature_schema_hash` binding. |
| Competing `ACTIVE_SCHEMA` | ⚠️ **Intentional lag** | `scalp_v1/50D` live vs `scalp_v3/70D` candidate — documented, hash-gated. |
| `scalp_v4` as second canonical | ⚠️ **Family alias, not canonical** | `scalp_v4` is 70D-compatible family, never marketed as canonical; SSoT is `scalp_v3`. |
| Stale `50` in experience/memory contracts | ⚠️ **Visible but benign** | `experience/models:CANONICAL_FEATURE_DIMENSION=50` — separate subsystem; keep visible per §9. |

---

## 9. Remediation Recommendations (no code changed in this audit)

1. **Keep `ACTIVE_SCHEMA_ID = scalp_v1` until a versioned promotion** — the existing `assert_canonical_registry()` and `INV-70D-013/014` checks already enforce this. Promotion should flip the registry `is_active` and the packaging `active_schema()` return, not a scattered `50→70` edit.
2. **Do not collapse `scalp_v3` / `scalp_v4`** — keep `scalp_v3` as the contracted canonical name; treat `scalp_v4` as a deprecated family alias (already handled by `liquidity_runtime._SCHEMA_IDS_COMPATIBLE_WITH_70D`).
3. **Retire `models/scalp_net.py:112` `num_features=50` default by lint rule** — require explicit `num_features=FEATURE_DIM` or `from temporal_contract import FEATURE_DIM` in new call sites (current fresh paths already do).
4. **Surface `experience/models:CANONICAL_FEATURE_DIMENSION=50`** in the forensics report as a subsystem-scoped constant, not a model-contract constant (it already is labeled as such; consider renaming to `CANONICAL_EXPERIENCE_DIMENSION` on next major bump to reduce confusion).
5. **Scaler hash verification:** `inference_validator._scaler_hash_equals()` is a deliberate no-op (dimension check is the per-tick guard; hash check is at load time). Keep as-is; do not add file I/O to the hot path.

---

## 10. Audit Proofs (reproducible greps)

```bash
# Worktree root: C:/Users/Capsizer/source/repos/NexusTradingForexBot/.worktrees/subagent-sa-2-628685f3

# SSoT axes — no competing literal
grep -rn "FEATURE_DIM\|SEQ_LEN\|N_CLASSES\|ACTIVE_SCHEMA" src/ --include="*.py"
# → FEATURE_DIM only in temporal_contract (def) + sequence (alias import)
# → ACTIVE_SCHEMA_ID only in features/schema.py (def) + consumers
# → N_CLASSES / SEQ_LEN names do not exist (canonical names are CANONICAL_SEQ_LEN / class_count / num_classes)

# ScalpNet class contract
grep -rn "scalp_v4\|FEATURES_70\|FEATURES_50" src/ --include="*.py" -i

# Dimension literals scoped to schema/contract
grep -rn -E "\b50\b|\b70\b" src/nexus_scalp/model_generation/ --include="*.py" | grep -i -E "dim|feature|seq|class|schema"

# Class count contract
grep -rn "CANONICAL_CLASS_COUNT\|CANONICAL_NUM_CLASSES\|EXPECTED_NUM_CLASSES\|LEGACY.*CLASSES" src/ --include="*.py"

# Feature ordering / label maps / scaler
grep -rn "FEATURE_NAMES\|feature_names\|scaler\|scaler_hash\|feature_schema_hash" src/ --include="*.py"
grep -rn "NO_TRADE\|BUY_MARKET\|SELL_MARKET\|class_names\|numeric_mapping" src/ --include="*.py"
```

---

## 11. Files Audited (primary)

- `src/nexus_scalp/model_generation/three_model.py`
- `src/nexus_scalp/model_generation/schema_v2.py` (+ `schema_v2_incremental.py`)
- `src/nexus_scalp/model_generation/sequence.py`
- `src/nexus_scalp/model_generation/architectures.py`
- `src/nexus_scalp/model_generation/temporal_contract.py`
- `src/nexus_scalp/model_generation/models.py`
- `src/nexus_scalp/model_generation/model_factory.py`, `replay.py`, `runtime.py`, `training.py`, `sequence_training.py`
- `src/nexus_scalp/features/schema.py`, `schema_contract.py`, `schema_augment.py`, `liquidity_engine.py`, `liquidity_runtime.py`, `features70.py`, `runtime70.py`, `scalper_features.py`, `inference_validator.py`, `temporal.py`
- `src/nexus_scalp/models/scalp_net.py`
- `src/nexus_scalp/application/live_engine.py`
- `src/nexus_scalp/training/walk_forward_trainer.py`
- `src/nexus_scalp/model_lifecycle/{integrity,model_class_contract}.py`, `governance/{load_gate,verify}.py`, `shadow/shadow70/*`

---

## 12. Verdict

**CONTRACT: PASS with documented legacy.**
- Canonical 70D/32/3 is deduplicated to a single source of truth per axis (`temporal_contract` for L+D, `schema_contract` for ordering+hash, `LABEL_SCHEMA_3CLASS_V1` for labels, `npz(mean,std)` for scaler).
- Live serving intentionally remains `scalp_v1 / 50D / 4-class-tolerant` until a governance promotion — correctly fenced and loudly checked.
- No competing `50`/`70`/`32`/`4` literal drives training geometry outside the SSoT. Residual literals are either structural (layout splits), DDL defaults, or explicitly named `LEGACY_*` constants.
- `scalp_v4` is a 70D family alias, not a second canonical — no merge needed beyond keeping `scalp_v3` as the documented canonical name.

---

*Generated by hermes-subagent/subagent-sa-2-628685f3. See `forensics/checks_features.py` (INV-70D-*) for the automated regression of these invariants.*
