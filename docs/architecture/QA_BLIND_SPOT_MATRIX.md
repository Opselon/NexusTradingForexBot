# QA BLIND-SPOT MATRIX — Nexus Scalp Engine

> CHG-0045 / TASK-QA-DEEP-ASSURANCE (Nexus-Main, 2026-09-02).
> Required first milestone of the OSS-grade adversarial QA brief: prove which
> gaps matter BEFORE building test mass. Derived from `agents/bugs.md`
> (BUG-001..BUG-208) and `agents/change_control.md` (CHG-0001..CHG-0050).

## 1. Method

1. **Defect-history mining** — every BUG/CHG row classified by subsystem,
   severity (P0 money/execution · P1 decision-path/trust · P2 operator
   trust/diagnostics · P3 robustness), and named regression test.
2. **Detection-quality audit** — does the named regression test the ROOT
   CAUSE or only the incident's symptom path?
3. **Re-entry analysis** — can the same class return through another door?
4. **Verdict** — KEEP / IMPROVE / MERGE / DELETE / MISSING per test family.

## 2. Matrix — defect history → detection → gap

| Subsystem | Historical defect | Current regression | Remaining hole | Proposed detection (CHG-0045) | Priority |
|---|---|---|---|---|---|
| features/70D contract | BUG-184 CHECK-FCS-04 accepted bool/str elements (duck-typing) | `test_qa_deep_70d_contract_properties.py` (forensics half GREEN — guard landed 1490635) | `validate_70d_vector` + `InferenceValidator` still coerce bool / crash on str-None at HEAD (parallel absorption restored files) | typed-element guard tests — xfail, owner-routed via BUG-208 scope addendum | P1 |
| signals/policy confidence | BUG-208 ZeroDivisionError: all-WAIT 4-logit vectors crash candidate measure; duplicate-tick gate masks repeats from replay-style tests | `test_qa_deep_bug194_zero_trained_mass.py` (crash probes + masking evidence + post-fix semantics) | policy.py fix (owner-routed); negative-mass poisoning path | crash probes stay RED as live evidence until owner fix lands | P1 |
| execution/position state machine | hysteresis regression family (BUG-081/088/089) | `test_s2_state_machine_golden.py` (example-based) | property guarantees over RANDOM event sequences were MISSING | `test_qa_deep_state_machines.py` SM-1..7 (emergency zero-latency, window non-reset, cross-ticket isolation) | P1 |
| execution/recovery budget | budget clamp/exhaustion semantics (BUG-054/072 family) | `test_s3_recovery_budget_golden.py` | clamp bounds under EXTREME inputs; recompute-not-sticky untested | `test_qa_deep_state_machines.py` RB-1..6 property over random configs | P1 |
| provider gate | BUG-186 429-storm amplification, BUG-187 silent auto-disable | `test_provider_gate_hardening.py` | follower-timeout deferral, circuit-flapping counter truth, exception containment, metrics-vs-reality cross-check | `test_qa_deep_provider_gate_chaos.py` CHAOS-1..7 | P1 |
| database migrations | BUG-094/096/108, AUDIT-0002..0007 chain | `test_database_migrations_phase18.py` (38 acceptance tests) | migrate-idempotence fingerprint, mid-chain failure recovery, tamper block, downgrade block, backup presence | `test_qa_deep_db_migration_adversarial.py` ADV-DB-1..7 | P1 |
| web API surface | BUG-121 code-scanning, BUG-162 fail-open parsing, BUG-189 state contradiction | `tests/integration/test_*_api.py` (happy paths) | malformed/oversized/unknown-field bodies, method abuse, traversal, JSON purity | `test_qa_deep_security_surfaces.py` SEC-1..5 | P2 |
| secrets/credentials | BUG-072/080, BUG-131, BUG-177 redaction corruption, BUG-121 clear-text | `test_logging_redaction.py`, `test_web_security.py` | property-level URL redaction over hostile shapes, CRLF injection, garbage-input crash-freedom | `test_qa_deep_security_surfaces.py` SEC-4..6 (seeded URL generator) | P1 |
| replay/70D temporal | BUG-188 timebase double conversion, BUG-183 purge/embargo, BUG-190 news keys, BUG-192 replay crash | `test_70d_replay_parity_task3.py`, `test_mt5_tick_boundary_bug188.py` | determinism-as-property (repeat bit-identity), timezone equivalence, shuffled-input equivalence, end-of-data boundary | `test_qa_deep_metamorphic_replay.py` M-1..M-6 | **P0** |
| observability aggregator | OBS-001..016 audit, BUG-177, storm contract freeze 2026-09-01 | `test_observability_contract_freeze.py`, `test_operational_log_hygiene.py` | adversarial evidence preservation: signature-flood exact accounting, monotonicity, sample bounds, multi-cycle totals, thread-race zero-loss | `test_qa_deep_observability_evidence.py` OBS-1..7 | P1 |
| execution safety (repo-wide) | INV-002/004, research-stack §63-65 no-order_send | `test_research_execution_stack.py::test_replay_never_calls_mt5_order_send` | structural guard over the QA layer itself + research source scan | `test_qa_deep_execution_safety.py` EXEC-1..4 (`live_trading_actions = 0`) | **P0** |
| confidence semantics | CHG-0042 WAIT dilution; legacy 0.61-floor defect | `test_confidence_semantics_repair.py` (13 example cases) | decision-level threshold freeze under perturbation, WAIT-neutrality, scale invariance | `test_qa_deep_confidence_adversarial.py` (13 attacks) | P1 |
| release/versioning | BUG-152/154/155/144/160 | `test_cli_end_to_end.py`, `tests/release/` | none proven — existing families adequate | **KEEP** (no new mass; duplicate-architecture rule §15) | P2 |
| installer lifecycle | BUG-146/147/149/160/161/185 | `tests/installer/*.py` (28, incl. lifecycle stress) | none proven — recent Task-Installer pass | **KEEP** | P2 |
| CI determinism | BUG-162 xdist flush race, BUG-163 wall-clock lottery, BUG-179 sleep-race pair, BUG-153 rolling-window bomb | fixes landed in-suite | hazard scan performed (37 `time.sleep` sites, 3 network-import test files, 16 random-using files): 25 ranked | **IMPROVE via governance**: clock-injection pattern (FakeClock) is the repo standard; new wall-clock/sleep asserts require a quarantine reason. Evidence in `qa-assurance-contract.md` | P2 |

## 3. Test-family verdicts

| Family | Verdict | Rationale |
|---|---|---|
| unit/critical suite (69 files, ~779 tests) | **KEEP** | highest defect-detection per runtime; ram-aware xdist |
| integration (21 files) | **KEEP** | real API/engine wiring; port-squatting hazard documented |
| QA-DEEP adversarial layer (10 files, 101 tests) | **KEEP** | defect-class targeted; ~22 s; double-run determinism proof |
| property testing (70D, state machines) | **KEEP** | stdlib seeded generators — no hypothesis dependency needed |
| metamorphic replay | **KEEP** | P0 determinism net; cheap |
| mutation proof (`scripts/qa/run_mutations.py`) | **KEEP** | temp-tree isolated; working tree never mutated |
| example-based policy tests (`test_policy.py`) | **MERGE-candidate** | overlaps CHG-0042 battery; keep until owner refactor |
| one-shot probes in `scratch/` (~23 files) | **DELETE-candidate** | zero consumers; archived copies exist; owner decision required |
| bounded load/performance | **MISSING (deferred)** | latency benchmark scripts cover p50 probes; Locust NOT adopted (no proven service-load requirement) |
| browser E2E (Playwright) | **MISSING-optional** | adopt only if a UI regression class escapes JS tests + API layer |

## 4. NEW defects found by this pass (evidence-first)

| ID | Class | Severity | Evidence | Owner routing |
|---|---|---|---|---|
| BUG-208 | ZeroDivisionError in `SignalPolicy` candidate measure (all-WAIT vectors); duplicate-tick gate masks it from replay-style tests | **P1** | live logs 2026-09-01 09:04:31/09:04:33/09:44:05 + crash probes `test_qa_deep_bug194_zero_trained_mass.py` | policy/confidence owner (CHG-0042 author) |
| SEC-1c | JSON body under wrong Content-Type returns HTTP 500 with empty body | P3 | `test_sec_wrong_content_type_is_never_a_traceback` probe | web owners (matrix-routed) |
| BUG-184 extension | type guard landed for CHECK-FCS-04 (1490635) but NOT for `validate_70d_vector` / `InferenceValidator` at HEAD (absorbed away mid-flight) | P2 | import probe: `validate_70d_vector([True, 0.0...])` ACCEPTED; xfail-marked tests | feature-contract owner (BUG-208 scope addendum) |

## 5. Priority of next detection investments

1. **Owner fixes for BUG-208 + BUG-184 extension** (flip the RED probes/xfails).
2. Determinism governance rulebook enforcement (no new wall-clock/sleep asserts without classification).
3. Bounded load scenarios ONLY when a service-level requirement is proven (per brief §19).
