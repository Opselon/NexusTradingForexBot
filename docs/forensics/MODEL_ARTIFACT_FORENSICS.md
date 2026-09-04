# Model Artifact Forensics — 2026-09-04T15:30+03:30

**Scope:** every `*.pt*` under `C:/Users/Capsizer/source/repos/NexusTradingForexBot/artifacts` (read-only `torch.load` via `.venv/Scripts/python.exe`, `weights_only=True`) + adjacent `model.meta.json` / `model.json` + `*.npz` scaler.
**Canonical contract:** ScalpNet v3, `(B,32,70)`, 3 classes (`NO_TRADE/BUY_MARKET/SELL_MARKET`), `feature_schema_id=scalp_v3`, `hash=235b8fccc96b7e0e`, `dataset_id=ds_70d_clean_m1_20260904`, `seq_len=32`, `label_schema=triple_barrier_3class_v1`.

## Verdict: P0 INCOHERENCE — metadata claims 3, tensor is 4

`artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt` **is the primary hit**. Every backup variant shows the same tensor. `70d_news`, `50d_main`, `t70d_*_2D`, `liq70_proof` and all legacy `bench_*` / cand LEGACY_SCALPNET artifacts share the same 4-head lineage. Only `wf_candidate` (scalp_v4, 70D, 3-class) and the `TCN_ATTENTION_V1`/`MLP_V2` families are actually 3-class.

## Primary artifact — `artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt`

| field | value |
|---|---|
| size | `1334268` bytes (live); `1335531` for `model.pt.bak_20260904` / `model.pt.bak2_1788498273` (same tensor shape, different weights/sha) |
| sha256 live | `c8c0b5b06d4c094dc04c9e8ff45cbfffc6f3fb396d42e3df46449068b1dbfd2b` |
| sha256 bak_20260904/bak2_1788498273 | `763a25f61fe6b7d35da79fc3d2432b5fce59fdd2fc9237e816de20ec79e88d98` |
| sha256 other baks (`.bak2_1788498799`, `.bak2_1788498909`, `.pre_direct_bak`) | `c8c0b5b06d4c094dc04c9e8ff45cbfffc6f3fb396d42e3df46449068b1dbfd2b` (identical to live) |
| params | `331492` |
| arch hint | `LEGACY_SCALPNET_V1` (`input_projection.weight [128,70]`, `classifier.weight [4,32]`, keys: `attention.*`, `causal_conv*`, `classifier`, `fc1/2`, `input_projection`, `pos_encoder.pe [1,500,128]`) |
| `input_projection.weight` | `[128, 70]` — input_dim correct for 70D |
| **classifier.head** | **`classifier.weight [4, 32]` + `classifier.bias [4]` — 4 classes** |
| scaler | `model.scaler.npz` → `mean [70]`, `std [70]` — correct dimension |

`model.meta.json` (live, also `.pre_direct_bak` identical):

```json
num_features=70, num_classes=3, model_head_classes=3, feature_schema_id=scalp_v3,
feature_schema_dimension=70, feature_schema_hash=235b8fccc96b7e0e, seq_len=32,
temporal_contract.seq_len=32, label_contract.class_count=3, dataset_id=null (in-file),
production_eligible=true, smoke=false, canonical_feature_names 70 entries (60..69 are liquidity 10D)
```

**Incoherence:** `model_head_classes=3` / `label_contract=3` declared, but `classifier.weight` first dim is `4`. Inference would emit a 4-logit vector against a 3-label decoder — `BUG-123` proof hash matches this tensor exactly.

History in this folder shows the claim flipped without fixing the tensor: `.bak2_1788498799` / `*.bak2_1788498909` meta was `scalp_v4`-less legacy (2051 bytes, no `model_head_classes`, no liquidity canonical names), then rewritten to expose `model_head_classes=3` while keeping the same `model.pt` (same sha).

## Sibling deployed artifacts

| artifact | size | sha256 | params | `input_projection` | head | meta claim | verdict |
|---|---|---|---|---|---|---|---|
| `models/scalp/XAUUSD/50d_main/model.pt` | 1325291 | `342901681f89a012…` | 328932 | `[128,50]` | `classifier [4,32]` | `num_classes 3` / `model_head_classes 4` | Meta documents the 4; tensor 4 — consistent with itself, **not** with 70D 3-class contract (legacy 50D ACTIVE, not 70D) |
| `models/scalp/XAUUSD/70d_news/model.pt` | 1335531 | `2b98f333cf1e3f77…` | 331492 | `[128,70]` | `classifier [4,32]` | `num_classes 3` / `model_head_classes 4` | Same self-documented 4; P0 if treated as 70D 3-class candidate |
| `models/scalp/XAUUSD/v1.0.0/model.pt` | 1325291 | `0872ae0b85b3c74b…` | 328932 | `[128,50]` | `[4,32]` | no meta | legacy 50D |
| `models/scalp/EURUSD/v1.0.0/model.pt` | 1325291 | `0872ae0b85b3c74b…` | 328932 | `[128,50]` | `[4,32]` | no meta | legacy 50D (identical to XAUUSD v1.0.0) |

Scalers match dims (50 / 70 / 70 respectively).

## `artifacts/model_generation/models/*` — full inventory

All inspected with `torch.load` (short sha = first 16 hex). `arch_hint` derived from key set.

| model dir | sha | params | arch | in_dim | head | meta (`model.json` / `model.meta.json`) | dataset | note |
|---|---|---|---|---|---|---|---|
| `bench_a_v1` | `a04b26b2…` | 328932 | LEGACY_SCALPNET | 50 | `classifier [4,32]` | `LEGACY_SCALPNET_V1, scalp_v1, fd 50, input_dim 50, ds_fe27908a6a66ee8f` | cand | historical bench — 4 |
| `bench_b_v1` | `dc98bb59…` | 330468 | LEGACY_SCALPNET | 62 | `[4,32]` | `scalp_v1, fd 50, input_dim 62, ds_fe279…` | cand | 4 — news-augmented dim |
| `bench_c_v1` | `0776dfa1…` | 330212 | LEGACY_SCALPNET | 60 | `[4,32]` | `scalp_v2, fd 60, input_dim 60, ds_9704ecc…` | cand | 4 |
| `bench_d_v1` | `367c2ec0…` | 331748 | LEGACY_SCALPNET | 72 | `[4,32]` | `scalp_v2, fd 60, input_dim 72, ds_9704ecc…` | cand | 4 — 72D news |
| `bench_e_v1` | `9e657ada…` | 238339 | TCN_ATTENTION_V1 | 50 | `head.3 [3,64]` | `TCN_ATTENTION_V1, scalp_v1, fd 50, input_dim 50` | — | **3 — correct** |
| `bench_f_v1` | `dbc85166…` | 239875 | TCN | 62 | `[3,64]` | `scalp_v1, fd 50, input_dim 62` | — | **3** |
| `bench_g_v1` | `35d62c2f…` | 239619 | TCN | 60 | `[3,64]` | `scalp_v2, fd 60, input_dim 60` | — | **3** |
| `bench_h_v1` | `cdbdc212…` | 241155 | TCN | 72 | `[3,64]` | `scalp_v2, fd 60, input_dim 72` | — | **3** |
| `cand_05d5e65879bc5748` | `a04b26b2…` | 328932 | LEGACY | 50 | `[4,32]` | `LEGACY, scalp_v1 50` | ds_fe27… | dup of bench_a — 4 |
| `cand_241f9cf5ac52575f` | `dd7a97a1…` | 323300 | LEGACY | 6 | `[4,32]` | `LEGACY, scalp_v1 6, ds_test` | smoke — 4 |
| `cand_298a28df308874db` | `ad96a1f6…` | 331748 | LEGACY | 72 | `[4,32]` | `LEGACY, scalp_v2 72` | ds_test | 4 |
| `cand_6f4090b06d2975d3` | `507ee080…` | 323300 | LEGACY | 6 | `[4,32]` | `LEGACY, scalp_v1 6` | smoke — 4 |
| `cand_8ed8b7985a7d0231` | `e6ce609b…` | 39939 | MLP_V2 | 50 | `net.9 [3,128]` | `MLP_V2, scalp_v1 50` | ds_fe27… | **3** |
| `cand_aaae456347026f59` | `8b71508a…` | 39939 | MLP_V2 | 50 | `net.9 [3,128]` | `MLP_V2, scalp_v1 50` | — | **3** |
| `cand_d77b130ea1e1473b` | `e6ce609b…` | 39939 | MLP_V2 | 50 | `net.9 [3,128]` | same | — | **3** |
| `candidate_exp_seqb2_ae3f1d_203828` | `4baad7b7…` | 238339 | TCN | 50 | `[3,64]` | `TCN, scalp_v1 50, seq 16` | — | **3** |
| `candidate_exp_seqbudget_9d4da9_203705` | `14fb1067…` | 238339 | TCN | 50 | `[3,64]` | `TCN, scalp_v1 50` | — | **3** |
| `liq70_proof` | `2569c67e…` | 331492 | LEGACY | 70 | `[4,32]` | `LEGACY_SCALPNET_V1, scalp_v3, fd 70, input_dim 70, ds_proof` | proof | **P0: claims 3, tensor 4** |
| `t70d_2d_baseline_same_windows` | `d6d70942…` | 331492 | LEGACY | 70 | `[4,32]` | `SCALPNET_V3_2D, scalp_v3, fd 70, num_classes 3, seq_len 1, ds t70d_f1_full_m1` | — | **P0: meta 3, tensor 4** |
| `t70d_full_retrain` | `c8c0b5b0…` | 331492 | LEGACY | 70 | `[4,32]` | `SCALPNET_V3_2D, scalp_v3, fd 70, num_classes 3, seq_len 1` | same | **P0 — identical tensor/scaler to 70d_liquidity live** |
| `t70d_seq_v1` | `9d629690…` | 236803 | TCN | 70 | `[3,64]` | `TCN_ATTENTION_V1, scalp_v3, fd 70, seq_len 32` | t70d | **3 — correct 70D 3-class** |
| `t70d_seq_v2_tuned` | `7ff2c4da…` | 236803 | TCN | 70 | `[3,64]` | same | t70d | **3 — correct** |
| `wf_candidate` | `ec84ed21…` | 331459 | LEGACY | 70 | **`classifier [3,32]`** | `scalp_v4, 70, num_classes 3, model_head_classes 3, seq_len 32, production_eligible false, label_origin UNKNOWN` | `null` | **Only LEGACY 70D with 3-head; 33 fewer params than 4-head (331459 vs 331492) — `3*32+3=99` vs `4*32+4=132`. Schema is `scalp_v4` (not canonical `scalp_v3`), no `feature_schema_hash`, not production-eligible** |

## No `scalp_v4` remnants beyond `wf_candidate`

`scalp_v4` appears only as `wf_candidate/model.meta.json: feature_schema_id=scalp_v4`. No other `scalp_v4` `model.pt` remnant exists in `artifacts/`. All other 70D meta use `scalp_v3` (or `scalp_v1/v2` for legacies). `t70d_*` and `liq70_proof` are `scalp_v3` 4-head; `wf_candidate` is the sole `scalp_v4` 3-head.

## Canonical dataset — `ds_70d_clean_m1_20260904`

- `dataset_manifest.json`: `feature_schema_id scalp_v3, label_schema triple_barrier_3class_v1, feature_schema_hash 235b8fccc96b7e0e, shape [26916,32,70], valid windows 22436, contract class_count 3, purge 15 / embargo 15, seed 42, source XAUUSD_M1.csv 100k rows`
- `verification.json`: `all_gates_pass=true, verify_70d ok, gap_safe ok, label_integrity ok, schema_hash ok` — **canonical 70D 3-class is actually built and gated**, but the only LEGACY 70D that matches it head-wise is `wf_candidate` (wrong schema id + not production-eligible); the deployed `70d_liquidity` does not.

## Summary table vs 70D 3-class contract

| artifact | contract check (`70D, 3 classes, ds_70d_clean…`) | result |
|---|---|---|
| `70d_liquidity` live + 4 baks | `70D ok, seq adhoc vs 32, dataset mismatch, **4-head vs 3**` | **P0 FAIL** |
| `70d_liquidity` bak_20260904 / bak2_1788498273 | same tensor family | **P0 FAIL** |
| `70d_news` | `70D ok, 4-head vs 3` | **P0 FAIL** |
| `t70d_*_2D`, `liq70_proof` | `70D ok, 4-head vs 3, dataset t70d_f1_full_m1` | **P0 FAIL** |
| `wf_candidate` | `70D ok, 3-head ok, scalp_v4 vs scalp_v3, no hash, production_eligible false` | **PASS on geometry, FAIL on governance/schema** |
| `t70d_seq_v1/v2` | `70D 3-class TCN, different arch` | **PASS (TCN family, not ScalpNet v3)** |
| `50d_main` / v1.0.0 | `50D legacy, 4-head` | not 70D — out of scope |

## Recommendation

- Do not promote `70d_liquidity` to production: retrain a `scalp_v3`, 70D, `seq_len=32`, 3-class `SCALPNET_V3_2D` (or equivalent LEGACY head `[3,32]`) on `ds_70d_clean_m1_20260904` and wire `model_head_classes=3` from the tensor at train time (the current meta is hand-edited). `wf_candidate` proves the 3-head LEGACY path compiles but it is `scalp_v4` + `UNKNOWN` lineage — retag to `scalp_v3` + `235b8fccc96b7e0e` and re-verify.
- `t70d_seq_v1/v2` are the only head-correct 70D sequence candidates; if ScalpNet v3 is required, do not substitute TCN.

## Provenance

- Python: `.venv/Scripts/python.exe` Python 3.11.16, `torch 2.13.0+cpu`
- Inspect: `torch.load(..., map_location='cpu', weights_only=True)` → `state_dict` unwrap → tensor scan → `classifier.weight` / `head.3.weight` / `net.9.weight` as head, `input_projection.weight` / `projection.weight` / `net.0.weight` as input_dim
- Hashes: `sha256sum` equivalent (hashlib sha256 streaming 1 MiB)
- Raw dump: `artifacts/forensics/model_artifact_forensics_20260904.json` (machine-readable) accompanies this markdown.
