# MODEL CERTIFICATION GATE BATTERY

**Status:** Canonical / Active  
**Enforcement:** `src/nexus_scalp/model_generation/certification.py` (`ModelCertificationGate`, `certify_model`)  
**Scope:** Authoritative multi-gate evaluation required for model candidate certification.

---

## 1. Principle

The existence of a weights file (`model.pt`) does not constitute a certified model.
A model candidate becomes officially certified **only** when every mandatory gate in the canonical battery passes without exception:

$$\text{MODEL\_CERTIFIED} \iff \bigwedge_{i=1}^{10} \text{Gate}_i == \text{PASS}$$

If any gate fails, the model status is recorded as `REJECTED`, and the model is barred from deployment to live inference.

---

## 2. The 10 Mandatory Certification Gates

### Gate 1: `DATASET` (Data Quality Gate)
- Evaluates `dataset_quality.json`.
- Fails if raw data quality status is not `PASS` or `trainable` is False.
- Fails if raw bar corruption exceeded allowable limits.

### Gate 2: `FEATURE` (Feature Quality Gate)
- Evaluates `feature_quality.json`.
- Fails if feature quality status is not `PASS`.
- Fails if any constant features or all-NaN columns are detected.

### Gate 3: `LABEL` (Label Quality Gate)
- Evaluates `label_quality.json`.
- Fails if target classes collapsed (e.g. 100% `NO_TRADE` or 0% `BUY`/`SELL`).
- Fails if regime collapse or severe cross-fold class spread occurred.

### Gate 4: `LEAKAGE` (Leakage Prevention Gate)
- Verifies that `oos_isolated` is explicitly asserted.
- Confirms that normalization was trained exclusively on in-sample data.

### Gate 5: `TRAIN` (Training Convergence Gate)
- Verifies that real PyTorch training occurred (`metrics_measured: True`).
- Fails if training was simulated or skipped (`epochs_completed == 0` or missing loss values).
- Fails if training loss diverged to `NaN` or `Inf`.

### Gate 6: `VALIDATION` (In-Sample Generalization Gate)
- Fails if validation loss is unmeasured or infinite.
- Evaluates validation accuracy against minimum opportunity quality floor.

### Gate 7: `WALK_FORWARD` (Temporal Walk-Forward Gate)
- Confirms walk-forward cross-validation passed across all folds.
- Verifies purge and embargo decontamination was applied between sequential folds.

### Gate 8: `OOS` (Out-of-Sample Holdout Gate)
- Fails if out-of-sample holdout was un-evaluated.
- Fails if OOS loss degrades catastrophically relative to in-sample validation.

### Gate 9: `COMPATIBILITY` (Runtime Contract Gate)
- Confirms dimension is canonical (50 or 70).
- Confirms sequence length matches inference contract (1 or 32).
- Confirms label schema matches triple barrier contract.

### Gate 10: `ARTIFACT_INTEGRITY` (Bundle Trust Chain Gate)
- Verifies existence of `model.pt`, `scaler.npz`, `meta.json`.
- Verifies SHA-256 hash of `model.pt` matches `weights_sha256`.
- Verifies SHA-256 hash of `scaler.npz` matches `scaler_sha256`.
- Verifies canonical JSON digest of `meta.json` matches `manifest_sha256`.

---

## 3. Machine-Readable Certificate Schema

Upon evaluation, a `ModelCertificate` is serialized as `<model_id>.certificate.json`:

```json
{
  "model_id": "scalp_v1_xauusd_20261001",
  "model_status": "CERTIFIED",
  "certified": true,
  "certified_at": "2026-10-01T15:00:00Z",
  "certification_version": "cert_v1",
  "schema_id": "scalp_v1",
  "dimension": 50,
  "sequence_length": 32,
  "label_schema_id": "triple_barrier_v3",
  "weights_sha256": "4b68e91...",
  "scaler_sha256": "9a12c4f...",
  "manifest_sha256": "7d33b1e...",
  "failed_stages": [],
  "passed_stages": [
    "DATASET",
    "FEATURE",
    "LABEL",
    "LEAKAGE",
    "TRAIN",
    "VALIDATION",
    "WALK_FORWARD",
    "OOS",
    "COMPATIBILITY",
    "ARTIFACT_INTEGRITY"
  ],
  "rejection_reason": null,
  "gates": [
    {
      "gate": "DATASET",
      "passed": true,
      "message": "Dataset certified with status PASS"
    }
  ]
}
```
