# tests/unit/test_walk_forward_trainer.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- Single-test contract suite for WalkForwardTrainer SMC fine-tuning: ingests features, extracts the 4 SMC columns, applies specialized loss multipliers, fine-tunes without shape errors, saves the checkpoint ATOMICALLY.
- Guards: returns a REAL ScalpNet (`isinstance(tuned_model, ScalpNet)`); weights UPDATED after fine-tuning (`weights_updated is True`); checkpoint exists on disk (model.pt) AND scaler saved alongside (`model_path.with_suffix(".scaler.npz").exists()`); loaded state contains `classifier.weight`.
- Setup: `num_folds=3, epochs_per_fold=1, min_rows_per_train_split=10, min_rows_per_test_split=5`, 100 rows × 50 features × 4 classes — bounded runtime.
- 1 def / 100 lines — minimal smoke-to-contract test (module covered in depth by test_train_model_cli.py).