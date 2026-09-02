---
title: Troubleshooting
description: Common failure modes and honest diagnostics — doctor first, evidence second.
lang: en
---

# Troubleshooting

> Rule of thumb: `nexus doctor` first — it reports 19 diagnostic categories
> with suggested fixes and is the same gate the engine runs pre-launch.

## Engine refuses to start

| Symptom | Likely cause | Action |
| :--- | :--- | :--- |
| "MT5 terminal missing/not running" | terminal closed / not installed | start MT5, log in (demo first), retry |
| config validation error | YAML contract violation | `nexus config --validate <path>`; fix the named key |
| environment blocked (exit 3) | Windows ARM64 | unsupported by dependency stack; use x64 |
| migration gate failure | schema drift | `nexus db migrate`; check `nexus db hygiene status` |

## UI / dashboard

| Symptom | Likely cause | Action |
| :--- | :--- | :--- |
| dashboard unreachable | port drift (dev UI/API ports can move) | check `nexus status` output / launcher log for the bound port |
| "Cannot GET /" on some port | wrong port — something else is listening | use the port from `nexus status` |
| SSE not streaming | proxy buffering | connect directly to `127.0.0.1:<port>` |

## Orders / execution

| Symptom | Meaning |
| :--- | :--- |
| orders rejected at broker | "Allow Algo Trading" unchecked in MT5 options |
| SAFE_MODE after rejections | circuit breaker tripped (3 rejections) — inspect `nexus incidents` + `/api/debug/trace` |
| no trades for long periods | check regime gate + confidence gate in the debug console; counterfactual evidence shows the confidence gate often filters losing trades — silence can be correct behavior |

## Model / features

| Symptom | Meaning |
| :--- | :--- |
| `SCALER_MISMATCH` / `MODEL_INPUT_DIMENSION_MISMATCH` | bundle doesn't match the active feature schema — the load gate is doing its job; don't bypass it |
| `FEATURE_CONTRACT_MISMATCH` SKIP in logs | retrain record refused (invalid snapshot) — by design, never zero-filled |

## Updates

| Symptom | Meaning |
| :--- | :--- |
| `RELEASE_NOT_FOUND` | no release exists yet — honest state, not an error |
| `NO_UPDATE` | you are current |
| update refuses to run | engine is LIVE — updates are blocked while LIVE by contract |

## Escalation

`nexus export-diagnostics` produces a sanitized ZIP (secrets redacted by
contract). Attach it to an issue — never paste raw logs with credentials.
