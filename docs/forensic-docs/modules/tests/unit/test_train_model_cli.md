# tests/unit/test_train_model_cli.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- Legacy CLI training script (50D contract alignment): `src/cli/train_model.py` must generate and select the FULL 50-dimensional feature matrix matching `WalkForwardTrainer.NUM_FEATURES` and ScalpNet — no 18D truncation, no dimension mismatch.
- Guards: feature engineering produces snapshots (`len(df_features) > 0`); EXACTLY 50 feature columns (`len(feature_cols) == 50`); every canonical `feat_<idx>` present (truncation detection: `feat_18` missing assertion for the historical bug); OHLC + spread + atr_m1 columns present.
- CLI compat: train-model CLI feature columns compatible with WalkForwardTrainer; on mismatch the error MESSAGE includes both widths (`assert str(WalkForwardTrainer.NUM_FEATURES) in message` and `"18" in message`) — asserts BEHAVIOUR, not just raise.
- 3 defs / 104 lines; synthetic tick data → real M1 bar aggregation.
- NOTE: regression for the 18D-truncation bug — feature names were silently cut; this pins width + names.