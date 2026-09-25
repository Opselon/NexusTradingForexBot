# src/cli/train_model.py

- **PURPOSE:** The legacy standalone training CLI (`python -m cli.train_model`)
  — trains a ScalpNet from raw tick/bar data with the 50D contract, kept
  as the manual training path (superseded by the model factory for new
  work, still referenced by docs/tests).
- **ARCHITECTURE LAYER:** CLI (training script).
- **RESPONSIBILITY:** load data → build 50D frames → triple-barrier label
  → walk-forward train → save artifact.
- **DEPENDENCIES:** walk_forward_trainer, labeling, features, torch.
- **CONNECTS TO:** tests (test_train_model_cli), operators.
- **KEY CONCEPTS:** contract-aligned 50D (NUM_FEATURES=50); artifact
  writing follows the candidate-path discipline (never silently
  overwrites the champion — the BUG-104 rule applies to every trainer).
- **EDGE CASES & PITFALLS:** heavy torch import; offline-only (never on
  the tick path); schema changes (60/70D) go through the model factory,
  not this script.