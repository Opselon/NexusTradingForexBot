# tests/unit/test_scalp_features_forensic_bug082.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- Forensic 50D contract tests (BUG-082 audit): executable-contract truth for `scalp_features.FEATURE_NAMES` — EXTENDS test_scalp_features.py with the forensic verification matrix.
- Guards: 50D contract dimension AND ORDER exact (`test_50d_contract_dimension_and_order`); index-1 canonical lower-wick ratio (the bug's index); INDEPENDENT recomputation of all 50 features (`test_independent_recomputation_all_50` — recomputed from raw bars, not from the runtime's own arrays); determinism over 100 runs.
- Causality: deep-history mutation at T-1 changes nothing at T (`test_causality_t_minus_1_deep_history_mutation`); T+1 tail-window contract — future tail bars must not leak into current features.
- Norm guards: RSI divisor contract (`test_norm_rsi_divisor_contract`); `test_edge_cases_no_nan_no_inf` — degenerate bars (doji, flat, NaN-ish input) never produce NaN/Inf.
- Independent double-check: `independent_50d` recomputes each feature from first principles; `np.allclose(runtime[i], expected[i], rel_tol=1e-9, abs_tol=1e-9)`.
- 23 defs / 657 lines; fixture bars (trend/flat/doji/wick/volume-spike/reversal/high-vol) built synthetically.