# src/nexus_scalp/shadow/challenger.py

- PURPOSE: ChallengerRuntime — the SHADOW-ONLY execution layer (PHASE 11
  spec 2/18/19): loads a validated Challenger artifact + scaler with full
  integrity checks (hash/schema/dimension/class count), runs inference on
  the SAME vector the Champion saw, produces a hypothetical proposal
  ONLY. It holds no adapter/order manager/risk engine and has ZERO order
  authority; NEVER modifies Champion state.
- ARCHITECTURE LAYER: Domain (model runtime, observation-only).
- RESPONSIBILITY: ChallengerRuntime (_load, infer, summary),
  load_challenger factory, _action_from_probs, resolve_schema.
- DEPENDENCIES: features.schema (FEATURE_SCHEMAS),
  model_lifecycle.integrity (inspect_artifact, scaler_compatibility,
  SchemaCompatibilityError), models.scalp_net (ScalpNet),
  shadow.models; numpy/torch lazy imports inside methods.
- CONNECTS TO: governance.shadow_runtime (wraps it), shadow.engine
  (attach_challenger), load wiring.
- KEY CONCEPTS:
  - _load sequence: inspect_artifact integrity (hash present + ok) →
    feature_dimension == live_dimension (schema mismatch →
    SHADOW_LOAD_FAILED / SCHEMA_MISMATCH) → num_classes == expected →
    scaler_compatibility(scaler, live_dimension) → torch.load
    (weights_only=False) → ScalpNet(live_dimension, num_classes)
    load_state_dict → eval() → scaler mean/std loaded as float32. ANY
    failure raises ChallengerLoadError; an invalid artifact is
    SHADOW_LOAD_FAILED, never silently reshaped (spec 26: a scalp_v2/60D
    challenger given a scalp_v1/50D live vector is rejected
    INVALID_COMPARISON, never padded/truncated).
  - infer(x50): strict input-length check (live_dimension) →
    standardize with (x - mean)/(std + 1e-8) → torch tensor →
    nan_to_num(nan=0, posinf=1, neginf=-1) → torch.inference_mode →
    ScalpNet(return_logits=True) → softmax → 4-prob list; action from
    argmax mapping {0:NO_TRADE, 1:BUY_MARKET, 2:SELL_MARKET, 3:WAIT};
    confidence = max prob. PURE COMPUTATION — a hypothetical proposal.
  - ref: ShadowModelRef built AFTER successful load (is_champion=False).
- HOT PATH / PERFORMANCE: inference ~ms on CPU under inference_mode
  (no autograd overhead); standardization is vectorized numpy; the
  nan_to_num guards NaN feature injection.
- EDGE CASES & PITFALLS: torch.load uses weights_only=False (arbitrary
  pickle — only safe because the artifact hash was verified BEFORE
  loading, and that ordering is load-bearing); class-count mismatch and
  scaler incompatibility raise distinct ChallengerLoadError messages
  (diagnosable); `_action_from_probs` with an unexpected probs length
  maps via dict.get → NO_TRADE fallback (a 5-class output would silently
  default to NO_TRADE — but length is enforced at infer's softmax of the
  model's own output, so this is defensive only); infer re-imports
  numpy/torch on every call (module import cache makes it cheap).