# TASK-007 — Model Artifacts Inventory & Signed Bundle Distribution

## Priority
P1

## Status
PENDING

## Objective
Verify official model distribution pipeline (PR #250 rev2 contract) with cryptographic Ed25519 signatures.

## Why This Task Exists
Ensures clients and live engines only execute verified, tamper-proof model weights published by the project owner.

## Current Evidence
src/nexus_scalp/model_provisioning/official_contract.py; official_install.py; .github/workflows/official-model.yml.

## Scope
Validate bundle building, staging verification, and transactional slot installation.

## Explicit Non-Goals
Do not sign or publish dev starter models as official models.

## Dependencies
TASK-004

## Source Areas
src/nexus_scalp/model_provisioning/official_contract.py, src/nexus_scalp/model_provisioning/official_install.py

## Files Likely Involved
src/nexus_scalp/model_provisioning/official_contract.py, src/nexus_scalp/model_provisioning/official_install.py

## Investigation Required
Verify persistent slot flock behavior under multi-process contention.

## Implementation Outline
Run offline e2e distribution drill: build bundle -> sign -> verify -> install -> READY.

## Tests Required
tests/integration/test_official_model_distribution_e2e.py

## Benchmark Requirements
Run critical suite to confirm zero regressions.

## Evidence Required
Exact execution trace and test output proving acceptance criteria are met.

## Acceptance Criteria
All official model contract tests pass (143 focused tests green).

## Human Stop Conditions
None required.

## Expected Output
Tested, verified PR with passing tests and updated task status.
