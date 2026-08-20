# src/nexus_scalp/model_lifecycle/integrity.py

- **PURPOSE:** Model artifact integrity & compatibility (PHASE 10, spec 28/29).
  Every saved artifact is associated with a hash, size, schema, architecture,
  version, provenance and validation result; a corrupted artifact is NEVER
  silently loaded.
- **ARCHITECTURE LAYER:** Research/ML — compatibility gate, no order authority.
- **RESPONSIBILITY:** Explicit compatibility gate (spec 6/29): feature schema id
  must match; feature dimension must match (50D today; 60D/350D additive — a
  mismatch FAILS loudly, never silently reshapes/truncates); output class count
  must match the model head (4); scaler/preprocessing must be schema-compatible.
- **DEPENDENCIES:** `experience.provenance.fingerprint_artifact` (SHA256 prefix
  hash), `features.schema.FEATURE_SCHEMAS`, torch (lazy, inside
  `_load_state_dict_shapes`), numpy (lazy, inside scaler inspection), models.
- **CONNECTS TO:** champion.py (ChampionModel verifies via inspect_artifact +
  scaler_compatibility), trainer.py (post-training artifact inspection), gates
  (GATE11 consumes integrity info), registry (fingerprint reuse).

- **KEY CONCEPTS:**
  - `EXPECTED_NUM_CLASSES = 4` (line 32): ScalpNet head NO_TRADE/BUY/SELL/WAIT.
  - `compute_artifact_hash` (line 45): SHA256-prefix (full-file hash via
    fingerprint_artifact) or '' when absent.
  - `inspect_artifact` (line 58): never raises for a missing file (missing
    artifact is a supported cold-start state); `integrity_ok` reflects every
    marker. Loads ONLY tensor shapes from the state dict (`_load_state_dict_shapes`,
    line 258, `torch.load(..., weights_only=False)` on CPU — no full model load).
    Input dim comes from `input_projection.weight` or `projection.weight`
    (shape[1]); missing tensor ⇒ integrity fail.
  - CLASS-HEAD PROBE (BUG-110 fix, lines 105-165): class count MUST come from the
    classifier head (final Linear), never from input_projection whose shape[0] is
    the hidden width (128) — that misread produced false "actual_classes=128"
    INTEGRITY_FAILURE on every valid ScalpNet v1 artifact. Head candidates in
    canonical priority: classifier.weight > head.3.weight (TCNAttentionV1 final
    layer) > head.2.weight > head.1.weight > head.0.weight > fc_out.weight.
    `head.0.weight` is the FIRST head layer (hidden→hidden/2) — only treated as a
    class head when it is the ONLY head-scale tensor (defensive fallback,
    lines 130-136). Fallback (lines 137-146): single candidate out-count ≤ 64
    among 2D weights ⇒ treated as classes.
  - Scaler dimension gate (lines 170-184): np.load(scaler) mean/std must match
    the model dimension (when the scaler file exists); scaler_dim is None when
    the file is absent — absent scaler does NOT fail the artifact (cold start),
    only a MISMATCHED one does (`ok` line 185: `scaler_dim is None or
    scaler_dim == dim`).
  - `verify_compatibility` (line 226): raises `SchemaCompatibilityError` on any
    mismatch, including schema-id-declared-dimension vs supplied dimension
    (lines 238-242). NEVER silently reshapes. Returns the info dict on pass.
  - `scaler_compatibility` (line 280): bool, never raises; missing or mismatched
    scaler ⇒ False (used by champion.verify to warn, not to fail — champion
    availability is driven by integrity_ok).

- **HOT PATH / PERFORMANCE:** `_load_state_dict_shapes` loads the full state dict
  into RAM (torch.load) on every verify — this is why champion.py caches verified
  champions behind a fingerprint (BUG-118): the ~2 Hz web/governance poll path
  avoids re-hashing and re-loading while the artifact file is unchanged.

- **EDGE CASES & PITFALLS:**
  - `_load_state_dict_shapes` catches ALL exceptions and returns {} ⇒ a
    legitimately torch-loadable but unusual file (e.g. a full checkpoint dict
    with nested state) shape-flattens nested dicts with dotted keys (line 270-273)
    — `model.classifier.weight` style keys from full checkpoints are NOT matched
    by the head probes, so full-checkpoint artifacts reliably fail integrity
    (only raw state_dicts pass).
  - `weights_only=False` is a deliberate torch-load with pickle — only used for
    shape inspection against artifacts the engine itself wrote.
  - `artifact_size` returns 0 on any exception (line 54) — transient stat errors
    look like an empty file.