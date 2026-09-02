# QA ASSURANCE CONTRACT — Deep Assurance Layer (CHG-0045)

> Owner: Nexus-Main (TASK-QA-DEEP-ASSURANCE). This document is the contract
> for the OSS-grade adversarial QA layer: lanes, markers, budgets, the JSON
> result schema, and the governance rules for flaky/open-defect tests.

## 1. Position in the gate hierarchy

| Lane | Command | When | Budget |
|---|---|---|---|
| Pre-push critical gate (existing, NOT replaced) | `beforePush.sh` / `check_local.py` | every push | ~2.5 min |
| **Deep assurance — fast** | `python scripts/qa/deep_assurance.py --fast` | PR / push lane in CI | < 60 s |
| **Deep assurance — full** | `python scripts/qa/deep_assurance.py` | main / scheduled / dispatch | < 5 min |
| Mutation proof | `python scripts/qa/run_mutations.py` | scheduled / dispatch (weekly) | < 15 min |

The deep-assurance layer NEVER replaces the runtime gate; it adds
independent adversarial angles (brief §30/§26).

## 2. Battery inventory (tests/unit/test_qa_deep_*.py)

| Battery | Tests | Class | Determinism |
|---|---|---|---|
| `test_qa_deep_70d_contract_properties.py` | 14+6xfail | property (70D contract, BUG-184/192 classes) | seeded `random.Random(20260902)` |
| `test_qa_deep_bug194_zero_trained_mass.py` | 5 | adversarial crash probes (BUG-208) | explicit inputs |
| `test_qa_deep_confidence_adversarial.py` | 18 | metamorphic + threshold freeze (CHG-0042) | explicit inputs |
| `test_qa_deep_state_machines.py` | 15 | state-machine property (SM-1..7, RB-1..6) | seeded random sequences |
| `test_qa_deep_provider_gate_chaos.py` | 7 | chaos/failure-injection (CHAOS-1..7) | FakeClock + stubs |
| `test_qa_deep_db_migration_adversarial.py` | 7 | DB adversarial (ADV-DB-1..7) | disposable tmp_path DBs |
| `test_qa_deep_security_surfaces.py` | 10 | security surfaces (SEC-1..6) | seeded URL generator |
| `test_qa_deep_metamorphic_replay.py` | 7 | metamorphic/temporal replay (M-1..6) | seeded synthetic bars |
| `test_qa_deep_observability_evidence.py` | 8 | evidence preservation (OBS-1..7) | seeded + bounded threads |
| `test_qa_deep_execution_safety.py` | 4 | execution safety (EXEC-1..4) | structural |

Helper: `tests/helpers/fault_injection.py` (`FaultPoint` — deterministic,
count-based boundary fault injector).

## 3. Rules

1. **Offline by default.** No network, no MT5, no package downloads. Any
   future external test MUST be marked and default-skipped as
   `ENVIRONMENT_BLOCKED` in JSON (never counted as PASS).
2. **`live_trading_actions = 0`** is structurally enforced
   (`test_exec_live_trading_actions_zero_kpi`).
3. **Determinism.** All randomness from `random.Random(fixed seed)`. The
   full layer is verified green on two consecutive runs.
4. **No production edits.** Production-code changes discovered by this
   layer are ledger-routed (BUG-208 → policy owner; BUG-184 extension →
   feature-contract owner; SEC-1c → web owners) with xfail/RED tests as
   pinned semantics — never silently skipped.
5. **Open-defect tests** use `pytest.mark.xfail(strict=False, reason=...)`
   naming the BUG id; they MUST flip green when the owner lands the fix
   and become strict or plain tests at that point.
6. **Flaky governance.** A flaky battery test is reproduced, classified,
   and either FIXED or quarantined with an explicit reason in this file.
   Blanket retries are forbidden.
7. **Mutation proof** runs against temp-tree copies; the working tree is
   never mutated. `SURVIVED` = test blind spot and must be filed as a
   test-quality defect in `agents/bugs.md`.
8. **Windows-first.** Everything is pure Python + pytest; no bash/grep
   assumptions; PS 5.1/7 compatible invocation.

## 4. Budgets (measured on the dev machine, 2026-09-02)

| Class | Measured | Budget |
|---|---|---|
| QA-DEEP full layer | 20–28 s | < 60 s |
| Individual battery | 0.3–8 s | < 30 s |
| Mutation proof (9 mutations, isolated interpreters) | ~150 s | < 15 min |

A battery exceeding its budget twice consecutively must be investigated
(perf regression in the target contract, not just "slow tests").

## 5. JSON result schema (`deep_assurance.py --json`)

```json
{
  "suite_version": "1.0.0",
  "git_commit": "<sha>",
  "environment": {"python": "3.11.x", "platform": "Windows-..."},
  "seed": 20260902,
  "duration_ms": 0,
  "status": "PASS | FAIL | ENVIRONMENT_BLOCKED",
  "tests_run": 0,
  "tests_passed": 0,
  "tests_failed": 0,
  "tests_skipped": 0,
  "tests_xfailed": 0,
  "defects": [
    {"subsystem": "...", "severity": "P1", "classification": "OPEN|FIXED",
     "evidence": "test id", "reproducer": "test path", "owner": "..."}
  ],
  "flaky": [],
  "performance": {"slowest_battery_ms": 0},
  "security": {"surfaces_checked": 6, "findings": []},
  "mutation": {"file": "scripts/qa/run_mutations.py", "last_run": "n/a"},
  "coverage": {"note": "line-coverage intentionally omitted; detection-power driven"},
  "recommendations": []
}
```

## 6. CI integration

`.github/workflows/qa-deep-assurance.yml`:

- **fast lane**: `--fast` on pull_request + push to main (timeout 10 min).
- **deep lane**: scheduled weekly + workflow_dispatch (`--json` artifact
  uploaded; failures block the lane, never silently green).
- The workflow does NOT touch `ci.yml` / `beforePush` (owner boundaries).

## 7. New-dependency policy (brief §31)

No new dependencies were adopted. Property generation uses stdlib
`random.Random` with fixed seeds; fault injection is a 60-line helper;
mutation testing is a bespoke bounded runner. Hypothesis/Locust/Playwright
are consciously NOT adopted (no proven defect class beyond current layers).
