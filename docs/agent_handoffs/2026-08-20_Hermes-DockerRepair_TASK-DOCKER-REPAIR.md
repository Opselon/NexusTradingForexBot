# Agent Handoff — TASK-DOCKER-REPAIR (2026-08-20, Hermes-DockerRepair)

## Summary
Repaired the Docker/startup/environment layer of NexusTradingForexBot. The
stack is now a single `core` + `redis` compose project with SQLite-authoritative
persistence (PostgreSQL removed — nothing consumed it), a multi-stage non-root
Dockerfile, a documented `.env.example` contract, an entrypoint that
env-validates → bootstraps dirs → runs the canonical `nexus db` migration gate
→ `exec`s the real command, and a real readiness probe (`GET /health`,
HealthEngine verdicts). Container execution is restricted to PAPER/SHADOW
(LIVE has no MT5 in a container and fails fast).

## Commits
- `41748ec` (absorbed by Hermes-StrategyFactory — my infra files: compose,
  Dockerfile, .env.example, .dockerignore, docker/ scripts, scripts/* wrappers,
  docs/docker.md)
- `d6dd2eb` scratch checker cleanup
- `9bd4f8e` /health + boot env wiring + TEST-DOCKER-01..12
- `e44171b` registries + skill.md §16 + README

## Files
- New: `.env.example`, `.dockerignore`, `docs/docker.md`,
  `scripts/{start.sh,start.ps1,doctor.ps1,reset-dev.ps1,backup-db.ps1}`,
  `tests/unit/test_docker_startup_phase21.py`
- Changed: `docker-compose.yml`, `Dockerfile`, `docker/entrypoint.sh`,
  `docker/healthcheck.sh`, `src/nexus_scalp/web/server.py` (/health),
  `src/nexus_scalp/cli/main.py` (NSE_WEB_HOST/PORT, NSE_LOG_LEVEL),
  `agents/{taskboard.md,bugs.md,skill.md}`, `README.md`

## Verification so far
- `docker compose config --quiet` OK; bash -n + pwsh parser OK on wrappers
- 21 unit tests passed (TEST-DOCKER-01..12 + web_security regression)
- ruff/mypy clean on my diff (remaining findings are other agents' WIP)

## In progress (NOT VERIFIED until done)
- Live container verification: image build, clean start, /health READY,
  restart persistence, config-error clarity, down/up data persistence,
  `docker compose ps` status. Docker daemon confirmed up on the host.

## Contracts/invariants
- DOCKER_STARTUP v1 (compose contract), /HEALTH v1 (verdict contract)
- BUG-125 ledger entry; taskboard TASK-DOCKER-REPAIR row (IN_PROGRESS)
- Skill.md §16 "Docker Runtime & Startup Contract" is the authoritative map

## Next-agent instructions
1. Finish/verify the live docker end-to-end checks (build is running).
2. If the engine does not reach READY in-container (e.g. model missing →
   /health 503 NOT READY), decide: mount a model artifact or document
   DEGRADED dev state — do NOT lower the health bar silently.
3. Update taskboard row → VERIFIED, and report final verdict to the user.
4. Keep `.env.example`/docs/docker.md in sync with any env changes.