# POST-70D DEPLOY GATE — canonical deployment safety contract

> TASK-12 §44 (AGENT-12, 2026-08-19).
> One health engine (`nexus_scalp.forensics`) + one canonical gate contract
> (`run_deploy_gate`). Hooks/CI call the engine; they NEVER re-implement
> health rules (TASK-12 §5).

## Semantics (§6)

| Health state | Gate decision | Deployment behavior | CLI exit (§10) |
| :--- | :--- | :--- | :--- |
| PASS | ALLOW | Deploy | 0 |
| WARNING | ALLOW_WITH_WARNING | Deploy with warning | 0 |
| DEGRADED | REVIEW_REQUIRED | Review required (policy-dependent) | 2 |
| UNKNOWN | REVIEW_REQUIRED | NEVER silently pass — review required | 2 |
| CRITICAL | BLOCK | Block deployment | 1 |
| engine failure | FORENSIC_ENGINE_UNAVAILABLE | Fail-safe block (§39) | 3 |

## Invariants

1. **UNKNOWN ≠ PASS** (§7): a check that cannot verify health reports
   UNKNOWN; the gate treats UNKNOWN as REVIEW_REQUIRED. No fallback logic
   such as `exception -> assume healthy`, `missing DB -> assume healthy`,
   `API unavailable -> assume healthy` exists or may be added.
2. **CRITICAL = BLOCK** (§6): any CRITICAL check blocks regardless of
   policy override — no averaging away (§50 of TASK-11).
3. **One engine**: beforePush.sh / beforePush.ps1 / CI / API all invoke
   `nexus forensic --deploy-gate` (or `GET /api/forensics/deploy-gate`).
   No duplicated health rules in hooks.
4. **Fail-safe** (§39): if the engine itself raises, the gate returns
   `FORENSIC_ENGINE_UNAVAILABLE` and blocks/reviews — never silently passes.

## Evidence (§8)

Every gate decision is persisted to
`artifacts/forensics/deploy_gate_result.json`:

```json
{
  "decision": "REVIEW_REQUIRED",
  "overall_status": "UNKNOWN",
  "timestamp": "...",
  "correlation_id": "...",
  "commit_sha": "...",
  "check_count": 34,
  "critical_count": 0,
  "warning_count": 6,
  "degraded_count": 2,
  "unknown_count": 5,
  "blocking_checks": [],
  "health_snapshot_id": "...",
  "engine_error": ""
}
```

## Interfaces

- CLI: `nexus forensic --deploy-gate [--json]`
  exit 0 = allowed · 1 = blocked · 2 = review required · 3 = engine unavailable
- API: `GET /api/forensics/deploy-gate`
  returns `{gate: {overall_status, deployment_allowed, blocking_reasons,
  health_snapshot_id, commit_sha, checks...}, last_gate: {...}}`
- Hooks: beforePush.sh step 5/5 and beforePush.ps1 step 5/5 call the CLI;
  exit 1/3 abort the push, exit 2 prints a review warning.

## Policy source

The policy table above is the repository governance contract for deployment
safety. Changes require a DEC-XXXX decision record + review; the mapping
lives in `forensics/deploy_gate.py::DEPLOY_POLICY`.