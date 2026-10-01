# MODEL ARTIFACT BUNDLE CONTRACT

**Status:** Canonical / Active  
**Enforcement:** `src/nexus_scalp/model_generation/certification.py` (`verify_artifact_bundle`)  
**Scope:** Specification of certified model bundles, sidecars, checksums, and provenance.

---

## 1. Bundle Specification

A candidate model is published as an atomic directory containing 7 interrelated artifacts:

```
artifacts/model_generation/checkpoints/
└── <model_id>/ (or flat prefix <model_id>.*)
    ├── <model_id>.pt                    # Serialized PyTorch state_dict
    ├── <model_id>.scaler.npz            # Fitted scaler sidecar (mean, std)
    ├── <model_id>.meta.json             # Manifest with provenance and checksums
    ├── <model_id>.certificate.json      # Signed 10-gate certification verdict
    ├── <model_id>.dataset_quality.json  # Raw data quality report
    ├── <model_id>.feature_quality.json  # Feature quality audit report
    └── <model_id>.label_quality.json    # Triple Barrier label audit report
```

---

## 2. Manifest Schema (`<model_id>.meta.json`)

The manifest contains the complete provenance and integrity chain:

```json
{
  "model_id": "scalp_v3_xauusd_20261001_143000",
  "schema_id": "scalp_v3",
  "dimension": 70,
  "sequence_length": 32,
  "output_classes": 3,
  "weights_sha256": "4b68e91...",
  "scaler_sha256": "9a12c4f...",
  "manifest_sha256": "7d33b1e...",
  "label_schema_id": "triple_barrier_v3",
  "normalization": "zscore_clip5",
  "oos_isolated": true,
  "git_commit": "614f4ef...",
  "created_at": "2026-10-01T14:30:00Z",
  "model_status": "CERTIFIED"
}
```

### Deterministic JSON Hashing Contract
To prevent self-referential hash recursion and platform-dependent key ordering discrepancies:
1. `manifest_sha256` is stripped from the dictionary before computing the digest.
2. Canonical JSON serialization is enforced: `json.dumps(obj, sort_keys=True, separators=(",", ":"))`.
3. Both the trainer (`StudioTrainer`) and the verification gate (`verify_artifact_bundle`) utilize this identical hashing algorithm.

---

## 3. Scaler Sidecar (`<model_id>.scaler.npz`)

The scaler sidecar encapsulates the frozen Z-score transformation:
- `mean`: 1D array of shape `(dimension,)` representing training sample means.
- `std`: 1D array of shape `(dimension,)` representing training sample standard deviations (bounded away from zero: $\sigma \ge 10^{-6}$).
- `dimension`: Integer matching 50 or 70.
- `schema_id`: String matching the declared feature schema.

---

## 4. No Silent Fallbacks Policy

1. **Missing Weights:** If `<model_id>.pt` is missing, loading is refused with `CompatibilityError`.
2. **Missing Scaler:** If `<model_id>.scaler.npz` is missing, loading is refused. The engine never silently falls back to identity scaling or unit variance.
3. **Missing Certificate:** If `<model_id>.certificate.json` is missing or verdict is `REJECTED`, loading is refused.
4. **Dimension Mismatch:** If an artifact is 50D and the requested contract is 70D, loading is refused with an exact diagnostic. The engine never zero-pads or truncates tensors silently.
