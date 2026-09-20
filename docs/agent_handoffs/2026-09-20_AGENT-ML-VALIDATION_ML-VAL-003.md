# ML-VAL-003 — Robustness Stress Testing: Friction Clamp, Slippage & Spread Perturbation

**Agent:** AGENT-ML-VALIDATION (NSE Autonomous ML Specialist Swarm, cycle 2026-09-20)
**Task:** `docs/ml-system/tasks/ML-VAL-003.md` (STREAM G — Validation/OOS, P2)
**Status:** DONE — PR opened
**Scope:** verification task; **no `src/` change** (OWNERSHIP_SCOPE `src/nexus_scalp/research/robustness.py` was already production-grade; this task hardened it with tests)

---

## 1. Deliverable

`tests/unit/test_robustness_stress_scenarios.py` — **33 tests, 0 failures** — exercising the
complete `RobustnessEngine.evaluate` (`src/nexus_scalp/research/robustness.py:73-188`) →
`gate_robustness` GATE8 (`src/nexus_scalp/model_lifecycle/gates.py:211-229`) chain.
Registered in `tests/critical_suite.txt`; manifest verified `CRITICAL_SUITE_MANIFEST_OK: 211 paths`.

Combined with the pre-existing friction suites: **58 passed / 0 failed** across
`test_robustness_stress_scenarios.py` + `test_bug299_friction_cap_saturation.py` +
`test_zero_friction_guard_e1.py`.

### Coverage map vs the task's IMPLEMENTATION_PLAN

| Plan step | Tests |
|---|---|
| 2. mock trade series with known R-distribution | `_dataset(win_frac, win_r, risk)` — all tests |
| 3. baseline friction evaluation | `baseline_expectancy_r` asserted in every test |
| 4. severe friction (spread +50, slip +2) | `test_gate8_rejects_fragile_candidate`, `..._task_ceiling_is_the_threshold` |
| 5. GATE8 rejects degradation > ceiling | `test_gate8_rejects_*` (0.48R measured vs 0.25R ceiling) |
| 6. GATE8 passes robust trades | `test_gate8_passes_robust_candidate` |
| BENCHMARK_PLAN (1,000 trades × 4 levels) | `test_benchmark_*` (parametrized + ordered + deterministic) |
| INVESTIGATION_PLAN (spread > TP distance) | `test_friction_r_floor_caps_single_trade_cost_at_half_r` |
| ABORT_CONDITIONS | `test_ceiling_is_never_relaxed_past_task_bound`, `test_gate8_never_swallows_a_fail` |

Stress grid: spread `+1/+2/+5/+10/+20/+50` ticks (the task's "+1 to +5 pips" on
`price_tick=0.01`, extended to the market-open-spike envelope its UNKNOWN asks about),
slippage `+1/+2` ticks, latency `+50/+150 ms`.

## 2. KEY CONTRACT DISCOVERY — the 50% ceiling is the friction model's ceiling

`compute_backtest` (`src/nexus_scalp/research/metrics.py:295`) models per-trade friction as

```python
friction_frac = (friction_ticks_eff * assumptions.price_tick) / risk
friction_r = min(friction_frac, 0.5)  # never more than 0.5R friction
```

so **each trade's degradation is capped at 0.5R**, and therefore **the MAXIMUM measurable
degradation across any series is 0.5R**. Consequences, all pinned by tests:

- The task text's "degradation <= 50%" is the **theoretical ceiling of the friction model**,
  not a threshold that can be meaningfully crossed. The binding constraint is the engine's own
  `MAX_ACCEPTABLE_DEGRADATION_R = 0.25R` (`robustness.py:29`) — it honours the task bound
  rather than relaxing it. **NON_GOALS respected: the 50% ceiling was not relaxed.**
- GATE8's negative-expectancy clause (`worst < 0.0 and max_deg > max_acceptable_deg_r / 2.0`,
  `robustness.py:138`) can only fire for a ceiling in `(0.5, 1.0)`, because for any ceiling ≤ 0.5
  the clause's `max_deg > ceiling/2` is dominated by the primary `max_deg > ceiling` clause.
  It is a genuine second line of defence, not the primary trigger. Pinned in
  `test_negative_expectancy_under_stress_fails_when_degradation_material` and
  `test_abort_condition_negative_stress_expectancy_cannot_pass` (both use `ceiling = 0.8`).
- A spread that exceeds the take-profit distance **saturates** rather than scaling: 50 ticks
  and 100,000 ticks produce byte-identical stressed expectancy. Pinned in
  `test_friction_r_floor_caps_single_trade_cost_at_half_r`.

## 3. ACCEPTED-RISK findings — pinned, not suppressed

Both were investigated against the full gate chain before acceptance. Neither is a production
hole; both are recorded as named tests so a future change to the shape fails loudly.

### RISK-A: an EMPTY dataset evaluates to status=PASS

`RobustnessEngine.evaluate` on `ResearchDataset(samples=[])` returns `status="PASS"` with
`baseline_expectancy_r=0.0`, `max_degradation=0.0`, `reason="Robust to modelled stress"`.
There is **no sample-count floor** in `robustness.py`; `compute_backtest` returns a zero-trade
report that no stress scenario can degrade.

**Why accepted:** `ModelLifecycleOrchestrator._evaluate_gates`
(`src/nexus_scalp/model_lifecycle/orchestrator.py:352-380`) runs GATE1 `gate_dataset_integrity`
and GATE7 `gate_oos` (whose economic floor is `MIN_ECONOMIC_OOS_EXPECTANCY_R = 0.02`,
`src/nexus_scalp/research/oos.py:41`) **before** GATE8, so an empty candidate is rejected long
before the robustness gate. GATE8 measures degradation only — adding a sample-count floor to it
would duplicate GATE1 and violate the single-owner rule.

Pinned by `test_accepted_risk_empty_dataset_passes_gate8_but_is_blocked_earlier`, which also
asserts the gate-chain reason GATE7 rejects the negative-expectancy shape.

### RISK-B: a NEGATIVE-BASELINE model can PASS GATE8

A model whose *baseline* expectancy is already negative (e.g. 50% winners at +0.9/−0.9 →
−0.04R) passes GATE8, because its degradation under stress is small — it was already losing.

**Why accepted:** GATE8 is a **relative** gate by design (spec 16 / 35): it measures collapse
under stress, not absolute profitability. Absolute profitability is owned by GATE7
(`gate_oos`, `min_oos_expectancy_r` floor). Wiring an absolute floor into GATE8 would duplicate
GATE7 and break the gate division of labour. Pinned by
`test_accepted_risk_negative_baseline_passes_gate8_profitability_is_gate7`, which ALSO asserts
that GATE7 rejects the same model — so the acceptance is conditional on GATE7 doing its job;
if GATE7 ever stops, this test goes red and the finding needs re-review.

## 4. GATE8 verdict map (proven, not assumed)

| Shape | Engine status | gate_robustness |
|---|---|---|
| robust (small stress, wide stop) | PASS | PASS |
| fragile (0.48R degradation) | FAIL | FAIL |
| negative stress expectancy + material drop | FAIL | FAIL |
| cap-pinned bundle (BUG-299 shape) | FAIL (ROBUSTNESS_NOT_SIMULABLE) | FAIL |
| latency-only scenario set | FAIL (ROBUSTNESS_NOT_SIMULABLE) | FAIL |
| empty dataset | PASS (accepted-risk RISK-A) | PASS |
| negative baseline | PASS (accepted-risk RISK-B) | PASS |

`test_gate8_never_swallows_a_fail` walks the three reachable FAIL/PASS shapes and asserts the
gate verdict equals the engine status with the `max_degradation` detail propagated — no FAIL
shape converts to a PASS. `test_gate8_dict_payload_rejected_same_as_object` covers the
serialized dict path that orchestrators may use.

## 5. Verification evidence (all commands actually run)

```
PYTHONPATH=src:. .venv-linux/bin/python -m pytest tests/unit/test_robustness_stress_scenarios.py
    33 passed in 0.72s
PYTHONPATH=src:. .venv-linux/bin/python -m pytest \
    tests/unit/test_robustness_stress_scenarios.py \
    tests/unit/test_bug299_friction_cap_saturation.py \
    tests/unit/test_zero_friction_guard_e1.py
    58 passed in 1.09s
PYTHONPATH=src:. .venv-linux/bin/python -m ruff check  <file>   -> All checks passed!
PYTHONPATH=src:. .venv-linux/bin/python -m ruff format --check <file> -> 1 file already formatted
PYTHONPATH=src:. .venv-linux/bin/python -m mypy <file>          -> Success: no issues found in 1 source file
PYTHONPATH=src:. .venv-linux/bin/python scripts/ci/verify_critical_suite_manifest.py
    -> CRITICAL_SUITE_MANIFEST_OK: 211 paths all exist
```

All tests are stdlib+repo only (no torch import), safe for the slim venv and the CI gate.

## 6. Downstream unblock

ML-BT-001 (`docs/ml-system/tasks/ML-BT-001.md`, P1, Trading Quality Metric Suite) listed
`ML-VAL-003` as a dependency in `06_TASK_LEDGER.md:72`; with ML-VAL-001 and ML-VAL-003 both
DONE it is now **unblocked** and is the strongest next-run candidate (its ledger dep on
ML-VAL-003 was the last unsatisfied one).

## 7. Files changed

- `tests/unit/test_robustness_stress_scenarios.py` (new, 33 tests)
- `tests/critical_suite.txt` (new entry next to the BUG-299 suite it builds on)
- `docs/ml-system/tasks/ML-VAL-003.md` (STATUS → DONE + completion evidence)
- `docs/ml-system/TASK_BOARD.md` (ML-VAL-003 → DONE)
- `docs/ml-system/06_TASK_LEDGER.md` (ML-VAL-003 → DONE)
- `agents/taskboard.md` (closeout row)
- `docs/agent_handoffs/2026-09-20_AGENT-ML-VALIDATION_ML-VAL-003.md` (this report)
