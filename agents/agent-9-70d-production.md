# AGENT-9 — TASK-09-70D-PRODUCTION-RELEASE (working notes, in-repo)

- Agent: Hermes-ProdRel (TASK-9 production engineering layer)
- Role: Production Deployment / Migration / Runtime Reliability Engineer
- Status: IN_PROGRESS (registered 2026-08-19)
- Branch: main; commits are agent-labelled `<AGENT>: <imperative>` per contract; step commits for parallel-agent observability.
- In-repo status file mirrors agents/taskboard.md TASK-9 row and is updated on every commit.

## Scope (TASK-9 brief)
Production-deployable, upgradeable, diagnosable, safe-for-existing-installations
delivery of the approved 70D/Liquidity system. NOT algorithm redesign.
Central invariant: NEW VERSION MUST SAFELY INHERIT THE USER'S OLD STATE.

## Repo reality (forensic findings, 2026-08-19)
- TASK-9 CLI update engine + TASK-10 migration engine + TASK-11 hygiene already LANDED (commits acdcd6f, 1966d42, 93c55e5). This session owns the 70D *production engineering layer* on top: model artifact release packaging (70D+60D), Web-bundle version contract, version-consistency runtime, release manifest schema coverage, migration for 70D metadata, installation compatibility docs, TEST-REL-01..30 suite.
- Parallel session has UNCOMMITTED 70D work in the working tree (liquidity_engine.py, schema.py scalp_v4/scalp_liquidity_v1 registrations, configs/base.yaml liquidity_features_enabled, model_generation/schema_v2.py 60D producer). READ-ONLY for me; never reset/clean/stash.
- Schema registry (CANONICAL, live): scalp_v1 (50D ACTIVE), scalp_v2 (60D candidate), scalp_v4 (70D candidate contract BASE50|FAMILY10|LIQUIDITY10), scalp_liquidity_v1 (60D liquidity candidate).
- Champion artifact: artifacts/models/scalp/XAUUSD/v1.0.0/{model.pt, model.scaler.npz} (50D).
- release-manifest.json (build): has feature_schema/model_compatibility HARDCODED to scalp_v1/50D — must derive from the schema registry + build info (version-drift class).
- Release manifests exist for v9.0.0/v9.1.0; `.DS_Store` seen in portable root (windows-minor, note only).

## Deliverables (this task)
1. docs/70D_PRODUCTION_RELEASE_FORENSICS.md — deployment forensics (WRITE FIRST, commit).
2. Model artifact release packaging + compatibility classification (ACTIVE/LEGACY/RETAINED) + 70D dependency check (MODEL_NOT_RUNTIME_COMPATIBLE, no silent fallback).
3. Runtime version-consistency block (app/commit/db_schema/feature_schema/model_schema/web_bundle) + /api/status + CLI `nexus version --json` + UI_V2 (frontend carries backend-reported build/version data; no hardcoded status).
4. Release manifest schema coverage: feature_schema from registry, web_bundle_version, supported_model_schemas, required_migrations, db_schema_version.
5. Migration: AUDIT-0005 metadata table for model/feature/70D registry versioning (schema_meta remains migration-owned; additive, idempotent, checksummed, backed up).
6. TEST-REL-01..30 suite: tests/unit/test_release_70d_production_phase19.py (+ API/UI wiring test in integration if applicable).
7. docs/70D_INSTALLATION_COMPATIBILITY.md + docs/70D_PRODUCTION_DEPLOYMENT.md + docs/70D_UPDATE_AND_MIGRATION.md.
8. agents/ registries updated additively; BUG-NNN only for PROVEN defects; handoff docs/agent_handoffs/TASK-09-70D-PRODUCTION-RELEASE.md.
9. Quality gates: ruff check/format, mypy src, pytest tests/unit, beforePush.sh; packaged-app smoke if a release bundle is buildable in-session (build_release.ps1 expects Git + signatures; likely NOT runnable here — document honestly).

## Commit cadence (user mandate)
Stepwise agent-labelled commits at architectural boundaries; each commit message:
`Hermes-ProdRel: <imperative summary>` + body (Agent/Role/Scope/Why/Implementation/
Verification/Risk/Handoff). Registry rows re-applied additively; check
`git diff --cached --name-only` immediately before commit (parallel-git hazard).

## Acceptance (from brief §60) — track here as items complete
[ ] existing installation upgrade works      [ ] fresh installation works
[ ] DB migration automatic                   [ ] no DB deletion required
[ ] migrations idempotent                    [ ] migration failures recover safely
[ ] financial history preserved              [ ] research history preserved
[ ] model history preserved                  [ ] 60D legacy model preserved
[ ] 70D model supported                      [ ] schema compatibility enforced
[ ] scaler compatibility enforced            [ ] configuration migration works
[ ] secure secrets remain protected          [ ] Web bundle matches backend
[ ] CLI update works                         [ ] update check works
[ ] update can be resumed/recovered          [ ] rollback works
[ ] LIVE update safely blocked/deferred      [ ] package release works
[ ] Windows/portable structure verified      [ ] runtime versions consistent
[ ] update status visible in UI              [ ] migration status visible
[ ] release errors traceable                 [ ] no fake success status
[ ] no live orders used for testing          [ ] full tests pass
[ ] package smoke passes                     [ ] docs updated
[ ] bugs updated where proven                [ ] handoff created
[ ] agent-labelled commit created