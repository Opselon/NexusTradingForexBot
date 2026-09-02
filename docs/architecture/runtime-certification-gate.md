# Runtime Certification Gate — canonical pre-push contract

> **CHANGE-ID:** CHG-0051 · **TASK-ID:** TASK-RUNTIME-GATE · **Status:** ACTIVE
> **One command. One answer.** `python scripts/ci/runtime_gate.py`

## 1. Purpose

The repository has many test families (runtime, features, 50D/70D, models,
database, execution, providers, research, CLI, observability, installer,
integration). The problem was never test count — it was the absence of ONE
strong, trustworthy, repeatable command answering:

> "Is the composed runtime safe, coherent, loadable, and operational enough
> for an agent to push its change?"

The Runtime Certification Gate is that command. It is a **curated, layered
certification of the REAL composed runtime** — not "run all pytest". It
exercises the actual boot path (config → adapter → `LiveEngine` service
graph → decision pipeline → API → shutdown) with disposable persistence and
a paper broker.

Doctor (operator diagnostics) and the forensic deploy gate (release health)
remain their own contracts; the gate CALLS them, never replaces them.

## 2. Command

```bash
# Recommended (agents, pre-push) — full certification
python scripts/ci/runtime_gate.py

# Machine-readable (pure JSON on stdout)
python scripts/ci/runtime_gate.py --json

# Cheap tier (no runtime boot) — static/import/config/DB-disposable/contract
python scripts/ci/runtime_gate.py --fast

# Persist evidence artifact (artifacts/forensics/runtime_gate_result.json)
python scripts/ci/runtime_gate.py --evidence
```

Use the repo venv: `.venv/Scripts/python.exe scripts/ci/runtime_gate.py`
(always invoke via the project's venv, never a bare tool name).

Measured budget (2026-09-02, dev workstation):

| Tier  | Measured wall time | Contents |
|-------|--------------------|----------|
| full  | ~30 s (14–16 s warm) | L0..L9 |
| fast  | ~6.5 s | L0..L4 only |

## 3. Certification layers

| Stage | Layer | Verifies (real objects only) | Required |
|---|---|---|---|
| L0 | STATIC | entrypoints exist, `py_compile` of `NexusTradingForexBot.py`/`main.py` | yes |
| L1 | IMPORT | 18 critical modules import (circular/deferred errors surface) | yes |
| L2 | CONFIG | real `AppConfig` defaults + `configs/base.yaml`, telegram secret masking | yes |
| L3 | DATABASE | engine-created schema on a DISPOSABLE SQLite file, required table classes, queued write→flush→read round-trip, migration-engine status; optional tables absent ≠ failure | yes |
| L4 | MODEL/FEATURE | real checkpoint/meta/scaler (70/70), canonical registry + hash, 50D base → `build_70d_vector` (0..49 base, 50..59 news, 60..69 liquidity) → strict `validate_70d_vector` | yes |
| L5 | SERVICE GRAPH | REAL `LiveEngine` construction (paper adapter, injected tmp `AuditRepository`, isolated settings DB); 12 required services present; effective dim/schema from bundle | yes |
| L6 | DECISION CYCLE | seeded synthetic M1 bars → features → 70D assembly → scaler → inference → regime → policy → experience/intelligence gates → freshness gate → risk sizing on a SIMULATED proposal. `NO_TRADE` is a valid completion | yes |
| L7 | API/HEALTH | real `create_app()`; `/health` (READY/DEGRADED) + `/api/status`; secret-leak check | yes |
| L8 | SHUTDOWN | real `_shutdown_async()`: workers stop, audit flush, adapter disconnect, zero pending background tasks | yes |
| L9 | INVARIANTS | runtime proof of `order_send` isolation (execution-seam counter == 0), consumes the certified forensic deploy-gate engine | yes |

Each stage reports `PASS / FAIL / SKIP` with duration, evidence, owner, and
failure class. A cheap-layer failure SKIPs downstream stages (evidence from
a broken foundation is misleading); SKIP is explicit, never silent.

## 4. Exit codes

Additive to `release/exit_codes.py` semantics (0..4 aligned; 5 extended):

| Code | Meaning | Mapped failure classes |
|---|---|---|
| 0 | CERTIFIED | — |
| 1 | RUNTIME FAILURE | `CODE_DEFECT`, `SERVICE_CONSTRUCTION_ERROR`, `RUNTIME_BOOT_ERROR`, `DATABASE_SCHEMA_ERROR`, `API_ERROR`, `SHUTDOWN_ERROR` |
| 2 | CONFIGURATION ERROR | `CONFIG_ERROR` |
| 3 | ENVIRONMENT BLOCKED | `ENVIRONMENT_BLOCKED`, `MISSING_ARTIFACT` |
| 4 | CONTRACT VIOLATION | `MODEL_CONTRACT_ERROR`, `FEATURE_CONTRACT_ERROR`, `INVARIANT_VIOLATION` |
| 5 | INTERNAL GATE ERROR | gate crashed — **never green on a crash** |

## 5. Safety contract

* **No live trading.** Paper adapter only; a gate tripwire wrapper counts
  every execution-seam call (`send_order`, `execute_market_order`,
  `place_pending_order`, `close_position`, …) and the whole certification
  must show **0**. This is runtime PROOF, not a claim.
* **No production mutation.** `artifacts/audit.db`, `artifacts/news.db` and
  the real `app_settings.db` are never touched: disposable tmp DBs are
  injected and `NEXUS_SETTINGS_DB` is repointed BEFORE runtime import.
* **Offline.** No provider calls, no MT5 terminal IPC, no downloads. The
  only traffic is loopback HTTP to the gate's own in-process FastAPI app.
* **Deterministic.** Synthetic bars are fixed math series (no `random`),
  timestamps end in the past (no future data), and repeat runs agree.
* **Pure stdout contract.** `--json` is pure JSON; engine chatter goes to
  stderr (the gate detaches root console handlers before its own output).

## 6. Failure classification & output

Every failure carries: `stage`, `reason`, `failure_class`, `evidence`
(stdout tail included), and `owner` (suggested handoff target). Human
output ends with `RUNTIME CERTIFIED` or `RUNTIME BLOCKED`; every failing
stage prints reason/class/owner — never "something failed".

JSON top-level keys: `gate_version, timestamp, git_commit,
application_version, environment, duration_ms, status, exit_code, stages[],
invariants[], model, feature_schema, database, engine, api, shutdown,
failures[], warnings[]`. Each stage: `name, status, duration_ms, evidence,
owner, failure_class, reason, skipped_reason`.

## 7. Agent pre-push workflow

```
agent modifies code
  → python scripts/ci/runtime_gate.py        # canonical gate (this doc)
  → PASS → targeted tests (critical suite per beforePush)
  → commit → push → GitHub CI (final authority)
```

If the gate fails: **do not push.** Fix the first failing layer (owner is
named in the failure), rerun. The only exception is a failure classified
`ENVIRONMENT_BLOCKED` / `MISSING_ARTIFACT` that is provably environmental
(e.g. artifact not provisioned in CI) — such exceptions are owner-reviewed
and recorded in `agents/bugs.md` or `change_control.md`, never silent.

## 8. CI usage

The same canonical command runs as an explicit step in the `quality` job of
`.github/workflows/ci.yml` (no duplicated logic in YAML — CI invokes the
script). CI remains the final authority; the gate is the local fast mirror
for the composed-runtime question.

## 9. Gate testing (the gate tests itself)

* `tests/unit/test_runtime_gate.py` — 34 tests: exit-code contract, stage
  machinery, JSON schema keys, determinism/no-future-data, tripwire
  behavior, failure-injection classes (missing model, width split, service,
  shutdown, invariants), upstream-skip semantics, human report, subprocess
  e2e.
* `tests/integration/test_runtime_gate_e2e.py` — 9 tests: full-chain
  certification, stage order, 70D model/scaler/schema evidence, zero-seam
  decision-cycle proof, health/shutdown evidence, DB isolation evidence,
  stdout purity.

## 10. Failure-injection matrix (mission 28 coverage)

| Scenario | Injected via | Expected stage | Class / exit |
|---|---|---|---|
| A. missing model | artifact absent | L4 | `MISSING_ARTIFACT` / 3 |
| B. wrong model width | 50-wide checkpoint | L4 | `MODEL_CONTRACT_ERROR` / 4 |
| C. wrong schema | meta ≠ canonical id | L4 | `MODEL_CONTRACT_ERROR` / 4 |
| D. broken scaler | zero-std / dim split | L4 | `MODEL_CONTRACT_ERROR` / 4 |
| E. missing required table | schema probe | L3 | `DATABASE_SCHEMA_ERROR` / 1 |
| F. malformed config | masking/parse fail | L2 | `CONFIG_ERROR` / 2 |
| G. dependency graph failure | service absent | L5 | `SERVICE_CONSTRUCTION_ERROR` / 1 |
| H. feature init failure | base/liquidity block | L4/L6 | `FEATURE_CONTRACT_ERROR` / 4 |
| I. inference failure | degenerate probs | L6 | `MODEL_CONTRACT_ERROR` / 4 |
| J. policy construction failure | service absent | L5 | `SERVICE_CONSTRUCTION_ERROR` / 1 |
| K. API startup failure | wrong status/verdict | L7 | `API_ERROR` / 1 |
| L. shutdown failure | pending tasks | L8 | `SHUTDOWN_ERROR` / 1 |
| M. order_send reachable | tripwire counter > 0 | L6/L9 | `INVARIANT_VIOLATION` / 4 |
| N. stale model | meta/schema/hash drift | L4 | `MODEL_CONTRACT_ERROR` / 4 |
| O. 70D enabled + incompatible model | dim split | L4/L5 | `MODEL_CONTRACT_ERROR` / 4 |

## 11. Ownership boundary

The gate owns runtime verification/certification orchestration only. It
CALLS public certified surfaces (`schema_contract`, `features70`,
`liquidity_runtime`, `inference_validator`, `HealthEngine`,
`ForensicHealthEngine`, `AuditRepository`, migration engine) and does NOT
redesign installer, provider gate, order manager, policy, regime, replay,
shadow, migrations, model training, observability SSOT, or CLI product
architecture. Defects found outside the gate are proven and handed off with
the failing stage's owner field.
