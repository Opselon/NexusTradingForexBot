# E2E Smoke Test — Canonical Agent / Operator Guide

> **Owner:** Nexus-Main · **Status:** ACTIVE · **Version:** 2.0 (layered production smoke)
> **Entrypoint:** `nse smoke` (Typer) · `python -m nexus_scalp.cli.main smoke`
> **Runner:** `src/nexus_scalp/smoke/runner.py` · **Matrix:** `src/nexus_scalp/smoke/coverage_matrix.py`
> **Result contract:** `src/nexus_scalp/smoke/result_contract.py`
> **CI gate:** `.github/workflows/ci.yml` (quality job, fast tier)

---

## 1. Purpose

The smoke system must stop being a collection of basic health checks and become
a **high-signal operational gate** capable of detecting broken wiring, contract
mismatches, stale artifacts, config drift, schema violations, model failures,
risk/execution regressions, persistence failures, replay/live divergence,
governance violations, unsafe LIVE activation, dead workers, broken web/API,
stale deps, partial startup and degraded-but-reported-as-healthy states.

The smoke answers one question:

> **Can this repository be safely started, exercised through its critical
> paths, observed, stopped, and trusted to preserve its safety contracts?**

Optimize for **confidence, failure detection, evidence quality,
diagnosability, repeatability and operational usefulness** — not test count.

---

## 2. Architecture — Layered Smoke

Do NOT create one giant test. The smoke is a **layered certification**.

| Layer | What it verifies | Real objects | Required |
|-------|---|---|---|
| **L0 STATIC** | entrypoints exist, `py_compile`, critical imports, `AppConfig` (defaults + `configs/base.yaml` PAPER default, secret masking), directory layout, version identity | `NexusTradingForexBot.py`, `main.py`, `src/`, `configs/base.yaml`, `AppConfig`, `get_version_info` | yes |
| **L1 CONTRACT** | 50D/70D/schema/hash/bounds/scaler/manifest, 10 `RejectionCode`s reachable, windowed M5-tail producer, 70D assembly `Base 0..49 \| News 50..59 \| Liquidity 60..69`, safety defaults | `schema_contract.SCHEMA_ID=scalp_v3 / DIMENSION=70 / feature_schema_hash()`, `FEATURE_SCHEMAS` (scalp_v1=50 / scalp_v3=70), `InferenceValidator`, `ScalpFeatureEngine`, `assemble_70d`, `validate_70d_vector` | yes |
| **L2 INTEGRATION** | real seam `MarketData → 50D → 70D → ScalpNet → Policy → Risk → Accounting` (no mocks, disposable `audit.db`) | `ScalpFeatureEngine`, `ScalpNet`, `SignalPolicy`, `RiskEngine`, `AuditRepository` | yes |
| **L3 RUNTIME** | REAL `LiveEngine` service graph (12 services), synthetic decision cycle (PAPER, zero execution-seam proof), `/health` + `/api/status`, graceful shutdown, forensic deploy-gate | `LiveEngine` (paper adapter, disposable DBs, isolated settings DB), `create_app()` | yes |
| **L4 SAFETY** | 12 negative injections (each must **fail safely** with a specific code) | see §7 | yes |
| **LIFECYCLE** | `STOPPED → STARTING → READY → RUNNING → DEGRADED → RECOVERY → SHUTDOWN → STOPPED` + restart (second engine after first shutdown) | `LiveEngine` twice | informational |
| **DATAFLOW** | `tick → features → inference → policy → risk → accounting` chain complete with correlation IDs (`run_id` + `EXEC-*`) | `assemble_70d` + disposable audit write | yes |
| **HOTPATH** | `INV-001`: no sync DB on tick path (`log_signal` put <5 ms) + `_process_tick_pipeline` has no `sqlite`/`AuditRepository` | `AuditRepository` queue | informational |
| **AUTHORITY** | research/shadow/training have **zero** order authority (AST scan: no `order_manager` import), seam count == 0 | AST scan + tripwire | yes |
| **IDENTITY** | `artifact → scaler → bundle → serving` identity chain (tensor width == scaler dim == `effective_feature_dim`) | `model.pt` / `model.scaler.npz` / `LiveEngine.effective_feature_dim` | informational |
| **DETERMINISM** | same bars → same 50D (bit-identical on repeat) | `ScalpFeatureEngine` | informational |
| **BUDGET** | 8 performance budgets (startup/readiness/first-tick/e2e/shutdown/worker/API/persistence) — **WARN only**, never hide a failure | timings | informational |
| **TAXONOMY** | status vocabulary `PASS/FAIL/SKIP/WARN/BLOCKED/NOT_APPLICABLE/ENVIRONMENT_FAILURE/UNAVAILABLE` + no-false-green gate | runner | yes |

All layers run on every `nse smoke`. Cheap-layer failures **SKIP** downstream
layers so broken-foundation evidence is never misleading. Safety (L4) always
runs even when L0/L1 fail — it proves fail-safe behaviour.

---

## 3. Commands

```bash
# Recommended (agent pre-push) — full certification (~17 s, real LiveEngine)
nse smoke                # same as --full
nse smoke --full         # all layers incl. runtime E2E

# Cheap tier — static/import/config/contract + integration + safety (~2 s)
nse smoke --fast

# Runtime-heavy tier — like --full with explicit lifecycle emphasis (~10 s)
nse smoke --runtime

# Safety only — L0+L1 + all 12 negative injections (~2 s, no runtime boot)
nse smoke --safety

# Persist machine-readable evidence (in addition to --json stdout)
nse smoke --evidence                          # → artifacts/forensics/smoke_result.json
nse smoke --report path/to/report.json        # custom path
nse smoke --json                              # pure JSON on stdout (machine-readable)
nse smoke --fast --json                       # fast + machine-readable

# Render a previously persisted report without re-execution
nse smoke report                              # reads artifacts/forensics/smoke_result.json
nse smoke report --input path/to/report.json --json
```

All tiers also via the canonical invocation:

```bash
python -m nexus_scalp.cli.main smoke --fast
python -m nexus_scalp.cli.main smoke --safety --json
```

---

## 4. Exit Codes

| Code | Meaning | When |
|------|---------|------|
| `0` | **PASS** | all required checks passed (SKIPs on env-absent private model are honest, not failures) |
| `1` | **Smoke failure** (`FAIL` / `BLOCKED`) | any critical check failed or blocked |
| `2` | **Invalid invocation** | bad flag combo (`--fast --full` together) or `--report` write failure |
| `3` | **Environment failure** | honest `BLOCKED` (non-critical env SKIPs, not a code defect) |
| `4` | **Safety gate blocked** | an L4 safety check failed (a negative injection was NOT rejected) |
| `5` | **Infrastructure unavailable** | not used by smoke itself (reserved for future CI infra lane) |

The runner's `overall_status` is `PASS / FAIL / BLOCKED`.
`release_gate` is `true` only when the smoke is shippable.

---

## 5. When Agents MUST Run Smoke

Mandatory before declaring complete any change touching:

- runtime / `LiveEngine` / adapters / model loading / feature schema
- inference / policy / risk / execution / accounting / persistence
- governance / shadow / research / replay / observability / web / API
- configuration / startup / shutdown / release packaging / worker lifecycle
- safety boundaries

Also: any change that could plausibly affect the critical path.

### Fast smoke only — when allowed

Pure doc/comment/whitespace changes with **no** source semantics touched.
Every runtime/system-contract change requires **full smoke** or at minimum
`--fast` + targeted critical-suite tests.

---

## 6. Agent Pre-Push Contract

```
CHANGE
  → Targeted tests (critical suite slice per area)
  → Fast Smoke   (nse smoke --fast)           — every push
  → Full Smoke   (nse smoke --full)           — if runtime/system contract affected
  → Review failures/warnings in the report
  → Verify working tree (git status, no stray artefacts)
  → Push (CI is final authority)
```

For runtime-sensitive changes:

```
Targeted Tests → Full E2E Smoke → Runtime Full Check → Inspect Report → Only then Push
```

Agents must **not** declare success solely from unit tests.

---

## 7. Safety / Negative Testing (mandatory)

The smoke proves the system **fails safely**, not only that it succeeds.
Each injection below must be **detected** with its specific code,
**blocked**, leave the system in a **safe state**, and emit **evidence**.

| ID | Injected fault | Expected code | Layer |
|----|----------------|---------------|-------|
| SAFETY-01 | wrong model dimension (49D) | `MODEL_INPUT_DIMENSION_MISMATCH` | L4 |
| SAFETY-02 | wrong scaler dimension (50 vs 70) | `SCALER_MISMATCH` | L4 |
| SAFETY-03 | wrong schema hash | `SCHEMA_HASH_MISMATCH` | L4 |
| SAFETY-06 | invalid feature `NaN` | `NONFINITE_FEATURE` | L4 |
| SAFETY-07 | invalid feature `Inf` | `NONFINITE_FEATURE` | L4 |
| SAFETY-08 | out-of-range feature (beyond `[-3,3]`) | `OUT_OF_RANGE_FEATURE` | L4 |
| SAFETY-09 | unavailable liquidity block | `LIQUIDITY_UNAVAILABLE` | L4 |
| SAFETY-10 | unavailable news block | `NEWS_UNAVAILABLE` | L4 |
| SAFETY-13 | invalid risk proposal (`NO_TRADE` sized) | `RISK_REJECTION` | L4 |
| SAFETY-14 | excessive exposure (999 lots must clamp to `HARD_MAX_LOTS`) | `HARD_MAX_LOTS_ENFORCED` | L4 |
| SAFETY-19 | LIVE without confirmation | `LIVE_BLOCKED` | L4 |
| SAFETY-20 | research attempting execution | `ORDER_AUTHORITY_VIOLATION` | L4 |

Every failure carries `failure_code`, `expected`, `observed`, `safe_action`,
`suggested_investigation` and `evidence` — it must allow an operator/agent
to start debugging immediately, not just print `FAILED`.

---

## 8. Machine-Readable Result Contract

`nse smoke --json` emits the canonical JSON (also persisted with
`--evidence` to `artifacts/forensics/smoke_result.json` which is
**gitignored** — reports never land in Git).

Top-level keys:

```json
{
  "run_id": "smoke-20260905T15:35:12-abcdef12",
  "git_commit": "cd5544ec",
  "version": "9.0.10",
  "timestamp": "2026-09-05T15:35:12.123+00:00",
  "environment": { "python": "3.11.16", "platform": "win32", "executable": "...", "TELEGRAM_BOT_TOKEN_present": false },
  "runtime_mode": "paper",
  "tier": "fast | full | runtime | safety",
  "overall_status": "PASS | FAIL | BLOCKED",
  "release_gate": true,
  "duration_ms": 17096.1,
  "checks": [ { "id": "...", "layer": "L0|L1|L2|L3|L4|lifecycle|...", "name": "...", "status": "PASS|FAIL|SKIP|WARN|...", "duration_ms": 0.0, "failure_code": "...", "reason": "...", "expected": "...", "observed": "...", "evidence": {}, "safe_action": "...", "suggested_investigation": "..." } ],
  "critical_failures": [],
  "warnings": [],
  "degraded_components": [],
  "contract_results": [],
  "runtime_results": [],
  "safety_results": [],
  "performance_results": [],
  "artifacts": [],
  "evidence": { "coverage": { "coverage": [...], "negative_cases": [...] }, "timings": {}, "run_id": "...", "tier": "..." },
  "worker_health": {},
  "model_identity": { "artifact": "...", "exists": true },
  "schema_identity": { "schema_id": "scalp_v3", "dimension": 70, "hash": "235b8fccc96b7e0e" },
  "adapter_identity": { "mode": "PAPER", "seam_calls": 0 },
  "summary": {}
}
```

Never hard-code the shape — adapt to the repo's existing contract
(`src/nexus_scalp/smoke/result_contract.py` is the single source of truth).

---

## 9. Human Summary

Every smoke execution also renders a concise operator-friendly summary on
stderr/stdout (when not `--json`):

```
========================================================================
NEXUS E2E SMOKE TEST  —  FAST
========================================================================
Overall: PASS
Release Gate: PASS
Run: smoke-20260905T...  @  2026-09-05T...
Git: cd5544ec   Version: 9.0.10   Mode: paper
...

Startup / Critical Path / Safety / Observability / Lifecycle / Performance
...

Warnings: 0   Failures: 0
========================================================================
```

and for failures an **actionable** block:

```
FAIL
Code: MODEL_INPUT_DIMENSION_MISMATCH
Component: Model Load Gate
Expected: 70
Observed: 50
Safe Action: Inference blocked
Evidence: { ... }
Suggested Investigation: Check artifact path / scaler pairing
```

---

## 10. Evidence Collection

Every smoke run preserves:

- `run_id` (smoke correlation ID), `git_commit`, `version`, `timestamp`
- `tier`, `runtime_mode`, `environment` (python/platform, secret **presence** only)
- `duration_ms`, per-check durations
- `coverage` registry snapshot (all 200+ entries + 21 negative cases)
- `timings` (startup/readiness/first-tick/e2e/shutdown budgets)
- `model_identity` (artifact path, exists, dims), `schema_identity` (id/dim/hash)
- `adapter_identity` (PAPER proof, seam count), `worker_health`
- per-check `failure_code`/`expected`/`observed`/`evidence`

Never stored: `bot_token`, `api_key`, `password`, `token`, `secret`
— any key matching those names is replaced by `***REDACTED***`.

---

## 11. "Runtime Full Check" — Exact Command

```bash
nse smoke --full --evidence
# or: python -m nexus_scalp.cli.main smoke --full --evidence
# + optional machine-readable mirror:
nse smoke --full --json > smoke.json
```

What it verifies (one run):

```
startup → doctor → config → migrations → adapter → model → schema
→ web / workers → controlled tick/dataflow → inference → policy
→ risk → paper execution → accounting → observability → lifecycle
→ graceful shutdown
```

> **LIVE trading is never enabled as part of automated smoke testing.**
> The runner is PAPER-only, uses disposable DBs and an isolated settings DB,
> and proves at runtime that `order_send` was never called (seam counter == 0).

---

## 12. Failure Policy

### Hard Block (block push/release)

- model mismatch / schema mismatch / scaler mismatch
- unsafe LIVE activation / execution-authority violation
- accounting corruption / safety gate failure
- broken startup / broken runtime pipeline (L0–L3 FAIL on a critical check)

`overall_status = FAIL`, `release_gate = false`.

### Warning (do not ship but not a hard block)

- non-critical optional provider unavailable (when wired)
- research-only subsystem unavailable when not in use
- performance budget exceeded (WARN, never Fail)

Warnings are **never** disguised as PASS — they remain `WARN` in the
report and appear in `warnings[]`.

### Environment Failure (not a product failure)

- MT5 unavailable where runtime smoke would otherwise need it
- missing private champion artifact (see `MODEL-05` / `IDENT-01` `SKIP`)
- unavailable external provider

Materialised as `SKIP` with `MISSING_ARTIFACT`, not `FAIL`.
The report distinguishes this from a code defect — but the `BLOCKED`
status is still never reported as `PASS`.

---

## 13. No False Green — Hard Requirement

The smoke **must NOT** report `PASS` when:

- a critical check was `SKIP`ped unintentionally
- a required subsystem was unavailable without being explicitly `SKIP`ped
- a worker silently died (detected via shutdown pending-tasks check)
- a degraded subsystem was reported as healthy
- model or schema identity is UNKNOWN
- evidence is missing
- a safety check was not executed
- runtime did not actually exercise the critical path
- an exception was swallowed (checker exceptions surface as `FAIL`)
- a retry concealed the original failure (no retries hide evidence)

The gate prefers `FAIL / BLOCKED / DEGRADED` over an unjustified `PASS`.

---

## 14. CI Integration

| Job | Lane | Command | When | Budget |
|-----|------|---------|------|--------|
| `quality` | **fast smoke** | `nse smoke --fast --json` → `ci-results/layered-smoke/smoke.json` | every push/PR | ~2 s |
| `runtime_gate` | **canonical L0–L9 cert** | `python scripts/ci/runtime_gate.py --json` → `ci-results/runtime_gate.json` | every push/PR | ~30 s |
| `heavy-ci` | (future) `smoke --full` | gated to `ci-tests` / `workflow_dispatch` | not in default gate | ~17 s |

The quality job also gates `ruff`, `format`, `mypy`, `pytest` (critical suite)
and the one-file `smoke_chain` (`tests/e2e/test_smoke_chain.py`). The layered
smoke is **additive** — it never replaces an existing gate.

---

## 15. Artifact Locations

| Artefact | Path | In Git? |
|----------|------|---------|
| layered smoke report (fast, per-CI-run) | `ci-results/layered-smoke/smoke.json` (+ `smoke.log`) | no (CI artefact) |
| layered smoke report (local, ` --evidence`) | `artifacts/forensics/smoke_result.json` | **gitignored** |
| custom path report | `nse smoke --report path/to/file.json` | caller-chosen |
| runtime gate report | `ci-results/runtime_gate.json` / `artifacts/forensics/runtime_gate_result.json` | no / gitignored |
| one-file chain report | `ci-results/smoke/junit.xml` + `pytest.txt` | no |

Generated reports are **gitignored** (`artifacts/` + `ci-results/`).
Never commit a `smoke_result.json`.

---

## 16. Troubleshooting

| Symptom | Likely cause | Action |
|---------|--------------|--------|
| `CLI is not valid JSON` (stderr noise before JSON) | structlog console handler polluted stdout | Use `nse smoke --json` (runner detaches console handlers); or run `python -m nexus_scalp.cli.main smoke --json 2>stderr.log` |
| `MODEL-05 SKIP (MISSING_ARTIFACT)` | private champion artifact not provisioned locally | Honest — not a code defect; the gate remains `PASS` when this is the only SKIP |
| `SEAM count != 0` | a test path accidentally called `order_send` | Check `runner.py` tripwire; fix the paper-adapter wrapper |
| `UNKNOWN regime` in logs during smoke | expected: synthetic bars may trigger `MISSING_FEATURES` warning | Informational; the smoke proves the chain completes anyway |
| `readiness` / `first-tick` budget `WARN` | warm-start variance on dev hardware | WARN only — never blocks; tune `BUDGETS` in `result_contract.py` if warranted |

---

## 17. Coverage Registry

The machine-readable registry lives at `src/nexus_scalp/smoke/coverage_matrix.py`
(`COVERAGE` + `NEGATIVE_CASES`) and is included in every `--json` report's
`evidence.coverage`. Adding a new smoke concern means adding one entry there
— no concern silently dropped.

---

## 18. Related Docs

- `docs/architecture/runtime-certification-gate.md` — the canonical `L0–L9`
  runtime gate (`scripts/ci/runtime_gate.py`).
- `src/nexus_scalp/smoke/runner.py` — the layered runner (this guide's engine).
- `src/nexus_scalp/forensics/deploy_gate.py` — forensic deploy gate consumed
  at `L9` (and at runtime).
- `.github/workflows/ci.yml` — CI wiring (fast tier in `quality`).
