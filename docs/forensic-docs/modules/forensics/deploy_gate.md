# src/nexus_scalp/forensics/deploy_gate.py

- PURPOSE: Canonical Deploy Gate (TASK-12 §5-§11, §39): one health
  engine + one canonical gate contract translating the
  FORENSIC_HEALTH_SNAPSHOT into an ALLOW / ALLOW_WITH_WARNING /
  REVIEW_REQUIRED / BLOCK / FORENSIC_ENGINE_UNAVAILABLE decision.
  FAIL-SAFE: if the health engine itself fails, the gate BLOCKS
  (FORENSIC_ENGINE_UNAVAILABLE — never silently passes). Every decision
  carries the §8 evidence envelope and persists to
  artifacts/forensics/deploy_gate_result.json.
- ARCHITECTURE LAYER: Application (release gate).
- RESPONSIBILITY: run_deploy_gate, DeployGateResult (exit_code mapping),
  DEPLOY_POLICY, MANDATORY_CRITICAL_PREFIXES, current_git_commit,
  _persist_result, load_last_gate_result.
- DEPENDENCIES: forensics.engine (ForensicHealthEngine), forensics.models
  (HealthStatus, new_correlation_id), subprocess (git), json, logging.
- CONNECTS TO: release pipeline / CI, startup gate, Web dashboard
  (load_last_gate_result).
- KEY CONCEPTS:
  - POLICY MAP: PASS→ALLOW, WARNING→ALLOW_WITH_WARNING, DEGRADED→
    REVIEW_REQUIRED (operator decides), CRITICAL→BLOCK, UNKNOWN→
    REVIEW_REQUIRED (NEVER silently PASS, §7).
  - EXIT CODES: 0 allow, 1 block, 2 review, 3 engine unavailable
    (ALLOW_WITH_WARNING maps to 0 — warnings do not block).
  - run_deploy_gate: engine.snapshot() wrapped — engine failure →
    FORENSIC_ENGINE_UNAVAILABLE result (still persisted); otherwise
    blockers = CRITICAL checks whose id starts with a MANDATORY prefix
    (CHECK-FCS-, CHECK-MDL-, CHECK-INT-, CHECK-MIG-, CHECK-ACC-,
    CHECK-RTP-, CHECK-GOV-), then UNIONS with ALL critical checks
    ("Any CRITICAL blocks regardless of prefix if the policy is not
    explicitly overridden — safety first §6"): so effectively ANY
    critical blocks. Decision: blockers → BLOCK; overall CRITICAL →
    BLOCK; DEGRADED/UNKNOWN → REVIEW_REQUIRED; WARNING →
    ALLOW_WITH_WARNING; else ALLOW.
  - Records commit_sha (git rev-parse HEAD best-effort, 5s timeout),
    check counts, health_snapshot_id (correlation id).
  - DEDUP/bounded: not applicable (single JSON artifact, overwritten
    each run — the LAST gate result is loadable by the dashboard).
- HOT PATH / PERFORMANCE: release/startup cadence only; subprocess git is
  bounded by timeout 5s.
- EDGE CASES & PITFALLS: MANDATORY_CRITICAL_PREFIXES is effectively
  redundant because all-criticals are unioned in anyway (the prefix
  logic only matters if a future policy change stops unioning);
  decision "ALLOW" gate ordering: blockers checked BEFORE overall —
  a CRITICAL group state with zero blocking checks cannot occur because
  any critical check is a blocker; load_last_gate_result returns None on
  missing/corrupt file (dashboard must handle absence); the gate trusts
  the health engine's snapshot entirely — it does not re-run checks.