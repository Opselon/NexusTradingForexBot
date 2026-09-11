# 2026-09-11 — Agent-7 Runtime Resilience Wave 2b (FI-9 + classifications)

## FI-9: degraded-inference no-op guard at the hedging layer

FI-2 (bb08e91a) fail-closed the policy layer: `probs=None` -> NO_TRADE
(`PROBS_UNAVAILABLE_DEGRADED`) instead of an AttributeError crash loop.
One unguarded probs consumer remained on the post-policy path:
`LiveEngine._evaluate_hedging_policy` crashed on `probs.squeeze()` whenever a
tick ran degraded (BUG-253 70D stale-liquidity gate or an in-trade inference
failure). Prior tests masked the hole: the FI-2b and Agent-6 stubs replaced
`om._evaluate_hedging_policy` with a MagicMock, so the REAL body was never
exercised with `probs=None`.

Fix (PR #130): the hedging evaluation NO-OPs on degraded inference
(None / non-tensor / empty probs). Position protection continues via
`manage_active_positions`; no crash; no fabricated score. Pinned by FI-9a
(real body, None/empty) and FI-9b (full degraded-tick chain) in
`tests/unit/test_runtime_failure_injection.py` (21 tests).

## Design classification — PREDICTIVE_LIMIT / TICK_SWEEP model-health

ACCEPTED DESIGN. Both structural paths are model-confidence-independent BY
DESIGN (BUG-226 explainability contract stamps
`confidence_gate_applied=false` + `structural_gate` evidence; TICK_SWEEP
enforces its own `sweep_conf_thresh` floor). Every structural proposal still
passes RiskEngine.evaluate_proposal + dispatch guards (kill switch, persisted
halt, SAFE_MODE, maintenance window, idempotency, exposure, volume clamp).
No MODEL_HEALTH contract exists in agents/contracts.md; INV-003 keeps the
risk engine authoritative. Adding a gate would change strategy behavior
without a contractual basis.

## Contract classification — BUG-192 (validate_70d_vector element types)

CONTRACT DECISION REQUIRED (feature-contract owner). Behavior matrix:
bool ACCEPTED (True -> 1.0), str/None raise raw TypeError/ValueError (not
SchemaContractError), NaN/Inf/width -> structured SchemaContractError.
Required xfail pins exist (test_qa_deep_70d_contract_properties.py,
"BUG-184 extension / BUG-208 addendum"). Live exploitability NOT proven:
FeatureVector fields are float-typed and forensics CHECK-FCS-04 already
returns CRITICAL for non-numeric elements. The TASK-QA-DEEP-ASSURANCE row
claims "BUG-192 fix + regression net" while the validator is unchanged and
features/schema_contract.py carries a read-only convention note in another
taskboard row — the owner must adjudicate.

## 70D provenance (972 scalp_v3/50D rows)

Separate ML-provenance lane (Agent-8). No code coupling found with BUG-192
or the runtime resilience chain; rows must not be purged or fabricated.
