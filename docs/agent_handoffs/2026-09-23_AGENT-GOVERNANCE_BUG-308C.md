# 2026-09-23 — AGENT-GOVERNANCE — BUG-308C

## GATE11 artifact-integrity shape contract + orchestrator fail-closed hardening

**Status:** FIXED-VERIFIED, PR opened (`agent/governance/bug-308c`)
**Branch:** `agent/governance/bug-308c` (off `origin/main` @ 4958311d)
**Role:** AGENT-GOVERNANCE (Stream J — Model Governance & Lifecycle)

---

## Origin

Follow-through of the key finding from run 33 (ML-CI-001, PR #363, merged
03d524d9): *"`gate_artifact_integrity`'s dict path reads `num_classes`, which
the trainer's bundle `manifest.json` never emits → raw manifest returns
`classes=None` and fails on a valid artifact."*

With the ML task board exhausted (28/34 DONE; the 6 remaining are human-gated or
blocked pending operator decisions), this cycle closed that latent
production-gate defect instead of idling. The premise turned out to be
**incomplete in an interesting way** — the real shape is worse than reported.

## The real shape (discovered by driving the actual bundle publication)

`WalkForwardTrainer.train_and_validate` was run on a synthetic 50D frame and the
emitted artifacts were handed **straight** to GATE11 (no `inspect_artifact`
shim). Three artifact shapes exist, and GATE11 handled exactly one of them:

| Shape | Dimension key | Class key | `integrity_ok` | Hash handles |
|---|---|---|---|---|
| `ModelArtifactInfo` (promotion pipeline; `orchestrator.py:369`) | `feature_dimension` | `num_classes` | **yes** | `artifact_hash` |
| bundle **`manifest.json`** (`emission_gate.py:254-278`) | **`input_dim`** | **`class_count`** | **absent** | 3 |
| trainer **`model.meta.json`** (`walk_forward_trainer.py:2510-2600`) | **`feature_schema_dimension`** | `model_head_classes` + `num_classes` | **absent** | **none** |

Pre-fix, GATE11's dict branch read only `feature_dimension` / `num_classes` —
spelling (1) of the object path only. So:

* a raw published `manifest.json` read `dim=None`/`classes=None` → **FAIL on a
  valid, promotion-eligible artifact**;
* the trainer's `model.meta.json` read the same → FAIL;
* the defect was **latent** because the orchestrator passes a
  `ModelArtifactInfo`, whose attribute names are the only ones the gate read.
  Every future dict caller (governance UI, verification report, bundle
  re-check) would have hit the false fail. ML-CI-001's harness was the first
  such caller and routed around it via `inspect_artifact()`.

## Defects fixed

**BUG-308C-1 (P1): three-way key-alias gap.** GATE11 now resolves every known
spelling via ordered handle tables — `feature_dimension` → `input_dim` →
`feature_schema_dimension` → `num_features` for the width, and (head-first, per
the MODEL_CLASS_CONTRACT SSoT at `walk_forward_trainer.py:2516-2522`)
`model_head_classes` → `class_count` → `num_classes` → nested
`label_contract.class_count` for the class count. Head-first precedence matters:
when a legacy re-stamped artifact carries divergent handles, the class head is
the ground truth.

**BUG-308C-2 (P1): no `integrity_ok` in either artifact dict.** The original
gate read `info.get("integrity_ok", False)`, so both real dicts failed on a
missing field that no producer emits. Now an explicit verdict from the caller is
**authoritative** (never overridden — a caller's FAIL is never turned into a
derived pass), and a dict that carries no verdict gets an honest **derived**
verdict instead of a silent `None`: the markers must resolve and the bundle must
declare a hash handle. When the caller supplies `_bundle_dir`, the declared hash
chain (`model_sha256` / `metadata_sha256` / `scaler_sha256`) is re-verified
against the bytes — a tampered hash or a moved bundle fails closed.

**BUG-308C-3 (P2): orchestrator gate loop dropped the exception class.**
`orchestrator.py:424` caught a raising gate and recorded `reason=str(e)`,
discarding the exception class — the diagnostic that says *which* gate blew up.
Reason now carries `f"{type(e).__name__}: {e}"`. `None` (zero-artifact run) is
handled explicitly rather than falling through the object branch, and foreign
input shapes classify fail-closed without raising.

**Diagnostics.** The FAIL reason now distinguishes three cases so an operator
can act: (a) `dim=None classes=None` — the document declares no recognizable
markers; (b) `verdict=False` — an honest failure or a hash-chain mismatch;
(c) *"no integrity_ok verdict and no hash handle — this document declares no
identity of its own; pass the bundle manifest.json or a ModelArtifactInfo"* —
the `model.meta.json` case. The meta is bound **by** the bundle manifest; it
carries markers but no hash, so it is correctly rejected as an identity
document, and now the reason says so instead of the opaque `dim=None`.

### Layering note (why the widening is safe)

GATE11 is a **presence + honest-verdict gate**, not a value-comparison gate:
`passed = integrity_ok and dim is not None and classes is not None`. The actual
`DIMENSION_MISMATCH` / `CLASS_COUNT_MISMATCH` comparison happens in
`integrity.inspect_artifact` (`integrity.py:191-217`), which sets
`integrity_ok=False`, and GATE11 rejects on that verdict. Widening the accepted
spellings therefore cannot weaken a comparison GATE11 never performs — pinned
explicitly by `test_bug308c_producer_key_contract_is_live` +
`test_bug308c_all_class_spellings_resolve_head_first`.

## Files changed

| File | Change |
|---|---|
| `src/nexus_scalp/model_lifecycle/gates.py` | ordered handle tables (`_DIM_KEYS`/`_CLASS_KEYS`/`_HASH_KEYS`), `_first_key`/`_first_attr`/`_hash_chain_ok`, explicit-Verdict-authoritative logic, `None` + foreign-shape fail-closed, three-way diagnostic reason |
| `src/nexus_scalp/model_lifecycle/orchestrator.py` | gate-loop exception reason now carries the exception class |
| `tests/unit/test_bug308c_gate11_artifact_integrity.py` | NEW — 25-test regression battery |
| `tests/critical_suite.txt` | registered the battery (233 → 234 paths) |
| `agents/locks.yaml` | BUG-308C locks registered |
| `docs/agent_handoffs/2026-09-23_AGENT-GOVERNANCE_BUG-308C.md` | this report |

## Verification evidence (fresh, at branch head)

```
pytest tests/unit/test_bug308c_gate11_artifact_integrity.py  -> 25 passed in 1.39s (serial, CPU)
pytest tests/integration/test_walk_forward_pipeline_real.py  -> 11 passed (real WFT path; GATE11 on the real published bundle)
pytest tests/unit/test_promotion_rejects_degenerate_model.py -> 3 passed, 1 skipped (champion artifact absent — pre-existing skip)
pytest tests/unit/test_promotion_economic_integrity.py       -> 8 passed
pytest tests/unit/test_model_lifecycle_phase10.py
       tests/unit/test_model_governance_phase16.py
       tests/unit/test_champion_writer_governance.py
       tests/unit/test_agent3_champion_registry_sync.py
       tests/unit/test_gate_convergence_evidence.py          -> 120 passed in 74.34s
ruff check  (0.16.8 CI pin): All checks passed!
ruff format --check        : 3 files already formatted
mypy gates.py orchestrator.py: Success: no issues found in 2 source files
verify_critical_suite_manifest.py : CRITICAL_SUITE_MANIFEST_OK: 234 paths all exist
check_dependency_drift.py         : OK - 99 pins, requirements.txt consistent
```

**Real-artifact probe** (the one that found the truth — drove
`WalkForwardTrainer.train_and_validate` on a synthetic 50D frame and handed the
emitted files straight to GATE11):

```
manifest.json (real)             passed=True   dim=70 cls=3   reason=ok
manifest.json + bundle dir       passed=True   dim=70 cls=3   reason=ok
model.meta.json (real)           passed=False  dim=50 cls=3   reason=no integrity_ok verdict and no hash handle...
model.meta.json + bundle dir     passed=False  dim=50 cls=3   reason=no integrity_ok verdict and no hash handle...
tampered model_sha256            passed=False                 reason=artifact integrity failed (verdict=False)
```

The pre-fix gate returned `passed=False | dim=None classes=None` for the first
two rows — a valid published bundle. Post-fix the manifest passes, the meta is
rejected with an actionable reason, and a tampered hash is detected.

Environment: hermes venv python3 (3.11.16, torch 2.13.0+cpu, numpy, polars);
serial runs (`-p no:cacheprovider`) on the 3.9 GB / 2-core host per the
xdist-under-memory-pressure gotcha. The repo's `.venv-linux` interpreter has no
pydantic (slim venv); the hermes interpreter is the one with torch+pydantic.

## Battery coverage (25 tests)

1. Published bundle manifest passes (the headline regression)
2. `_bundle_dir` hash-chain verification: match passes, tamper fails
3. Bundle dir missing the declared files fails closed
4. Trainer meta without identity FAILs with an actionable reason
5. Explicit `integrity_ok: True` on the meta is honoured
6. Explicit `integrity_ok: False` is authoritative (never overridden by a derived pass)
7. Canonical spellings still first-class
8. All four dimension spellings resolve
9. All class spellings resolve, head-first on divergence
10. Nested `label_contract.class_count` resolves
11. No recognizable markers → diagnostic `dim=None`/`classes=None`
12. Markers but no verdict and no hash → FAIL, not pass
13. `ModelArtifactInfo` canonical attributes pass
14. Object with only manifest spellings resolves
15. `integrity_ok=False` fails despite valid dims
16. `None` → structured `GATE11_ARTIFACT` FAIL, no exception
17. Empty dict fails closed
18. Parametrized foreign shapes (`0`, `""`, `[]`, `object()`) never raise
19. Producer key contract is live (source-of-truth grep pin on both producers)
20. Published `manifest.json` disk round-trip still passes
21. Orchestrator loop: a raising gate does not abort the remaining gates; the
    FAIL reason carries the exception class

## Non-goals / not touched

- No change to `RemoteMT5GatewayAdapter` or its client contract.
- No change to `.github/workflows/*` (the ML-CI-001 nightly harness is
  untouched and its 11 tests still pass).
- No change to frozen domain models (`ModelArtifactInfo` used as-is).
- No change to the live tick path or `_process_tick_pipeline`.
- Promotion semantics (`gate_production_eligible`, the 12-gate set, gate order)
  are unchanged — this is a key-resolution + diagnostics + fail-closed fix only.
- No change to the emission gate or the producers; the manifest's `input_dim`
  is `CANONICAL_FEATURE_DIM` (a schema-contract constant, not a per-artifact
  measurement — verified at `emission_gate.py:266` and cross-checked by
  `verify_bundle_against_manifest`), so it was left as emitted.
- ML task board state unchanged: the 6 remaining tasks (ML-ARCH-001, ML-GOV-002,
  ML-GOV-003, ML-FEAT-003, ML-EXP-002, ML-OBS-002) still await operator
  decisions; no autonomous progress is possible on them.
