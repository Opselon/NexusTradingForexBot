# Feature Importance & Collinearity Audit — `scalp_v1` (50D)

> ML-FEAT-002 (Stream B) — automated feature evaluation. READ-ONLY:
> no feature is deleted, renamed or reordered here. This document is
> the empirical input a *future* schema version cites before pruning.

- **Feature contract:** `scalp_v1` / 50 dimensions
- **Samples analysed:** 5,945
- **Collinearity threshold:** |Spearman rho| > 0.85
- **Scoring model:** deterministic one-vs-rest ridge surrogate (closed-form, alpha=1e-3, train-fitted, chronological 70/30 split, seed-pinned)
- **Determinism:** seed-pinned permutations (identical input -> identical output)

## Method

1. **Spearman rank correlation** across all 50 features — rank transform
   captures monotonic non-linear relationships Pearson misses (the task's
   INVESTIGATION_PLAN).
2. **Mutual information** I(feature; triple-barrier label), equal-width
   binned, numpy-only (the slim verification venv carries no scikit-learn).
3. **Permutation feature importance** on a chronological held-out tail:
   each column is shuffled *in validation only* and the cross-entropy
   increase is the feature's measured contribution.

## Top-10 Alpha Drivers (out-of-sample permutation importance)

| Rank | Feature | PFI (mean Δloss) | PFI std | Mutual information |
|------|---------|------------------|---------|--------------------|
| 1 | `norm_tk_diff` | 0.001573 | 0.000430 | 0.003355 |
| 2 | `dist_to_ema_50` | 0.001548 | 0.000401 | 0.003282 |
| 3 | `dist_to_swing_low_20` | 0.000320 | 0.000156 | 0.002277 |
| 4 | `dist_to_ema_21` | 0.000252 | 0.000291 | 0.002662 |
| 5 | `cross_asset_z_score` | 0.000251 | 0.000238 | 0.002655 |
| 6 | `close_location_value` | 0.000184 | 0.000099 | 0.001426 |
| 7 | `lag_2_log_return` | 0.000138 | 0.000032 | 0.005360 |
| 8 | `norm_rsi` | 0.000094 | 0.000016 | 0.002193 |
| 9 | `dist_to_swing_high_20` | 0.000075 | 0.000009 | 0.002669 |
| 10 | `breakout_sig` | 0.000072 | 0.000013 | 0.000327 |

## Constant Columns

None — every one of the 50 columns varied in this evaluation frame.

## Collinear Pairs (|Spearman rho| > 0.85)

| # | Feature A | Feature B | Spearman rho |
|---|-----------|-----------|------------|
| 1 | `norm_dist_to_tenkan` | `norm_dist_to_kijun` | -1.0000 |
| 2 | `norm_tk_diff` | `norm_dist_to_tenkan` | +1.0000 |
| 3 | `norm_tk_diff` | `norm_dist_to_kijun` | -1.0000 |
| 4 | `stop_hunt_depth` | `feat_ob_liquidity_swept` | +0.9640 |
| 5 | `dist_to_ema_21` | `cross_asset_z_score` | +0.9633 |
| 6 | `dist_to_swing_low_20` | `dist_to_ema_21` | +0.9340 |
| 7 | `dist_to_swing_low_20` | `cross_asset_z_score` | +0.9259 |
| 8 | `dist_to_swing_high_20` | `dist_to_ema_21` | -0.9243 |
| 9 | `dist_to_swing_high_20` | `cross_asset_z_score` | -0.9040 |
| 10 | `kumo_sig` | `dist_to_ema_50` | +0.8923 |
| 11 | `dist_to_ema_21` | `dist_to_ema_50` | +0.8682 |

**Total:** 11 collinear pair(s).

## Hierarchical Collinearity Clusters

| Cluster | Representative | Members | Max |rho| | Redundant |
|---------|----------------|---------|-----------|-----------|
| 1 | `norm_tk_diff` | `norm_tk_diff`, `norm_dist_to_tenkan`, `norm_dist_to_kijun` | 1.0000 | `norm_dist_to_tenkan`, `norm_dist_to_kijun` |
| 2 | `feat_ob_liquidity_swept` | `feat_ob_liquidity_swept`, `stop_hunt_depth` | 0.9640 | `stop_hunt_depth` |
| 3 | `dist_to_ema_50` | `dist_to_ema_50`, `dist_to_swing_low_20`, `dist_to_ema_21`, `cross_asset_z_score`, `dist_to_swing_high_20`, `kumo_sig` | 0.9633 | `dist_to_swing_low_20`, `dist_to_ema_21`, `cross_asset_z_score`, `dist_to_swing_high_20`, `kumo_sig` |

**8 redundant feature(s)** identified across 3 cluster(s).

## Pruning Candidates (evidence, not a change)

> NON_GOAL of ML-FEAT-002: `scalp_v1` stays backwards compatible until a
> new schema version is approved. These are the rows that proposal cites.

| Drop | Keeps | Cluster max |rho| | PFI of dropped |
|------|-------|------------------|----------------|
| `norm_dist_to_tenkan` | `norm_tk_diff` | 1.0000 | -0.000018 |
| `norm_dist_to_kijun` | `norm_tk_diff` | 1.0000 | -0.000116 |
| `stop_hunt_depth` | `feat_ob_liquidity_swept` | 0.9640 | -0.000003 |
| `dist_to_swing_low_20` | `dist_to_ema_50` | 0.9633 | 0.000320 |
| `dist_to_ema_21` | `dist_to_ema_50` | 0.9633 | 0.000252 |
| `cross_asset_z_score` | `dist_to_ema_50` | 0.9633 | 0.000251 |
| `dist_to_swing_high_20` | `dist_to_ema_50` | 0.9633 | 0.000075 |
| `kumo_sig` | `dist_to_ema_50` | 0.9633 | -0.000084 |

## Interpretation

- Highest measured out-of-sample contribution: `norm_tk_diff` (PFI 0.001573).
- Lowest: `consecutive_momentum_count` (PFI -0.000174) — a low PFI means the scoring model did not use the column, which is model-dependent evidence, not proof the feature is worthless.
- 8 of 50 features are statistically redundant neighbours of a stronger column at this threshold.
- Frame provenance: synthetic XAUUSD M1 (6000 bars, seed=42) -> ScalpFeatureEngine (real 50D contract) + TripleBarrierLabeler; 5945 labelled rows. Synthetic bars exercise every feature branch of the production engine but do not carry real market microstructure; re-run with `--dataset <id>` on a broker-history artifact for the live verdict.

## Artifacts

- Dense Spearman / MI / PFI arrays: `artifacts/research/correlation_matrix.npz`
- Machine-readable ranking: `artifacts/research/feature_importance.json`

---
Generated by `scripts/analysis/evaluate_feature_importance.py` (ML-FEAT-002).