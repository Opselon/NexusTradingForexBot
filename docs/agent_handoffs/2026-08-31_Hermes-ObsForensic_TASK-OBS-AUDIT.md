# 2026-08-31 Hermes-ObsForensic Observability & Forensic Trace Audit (TASK-OBS-AUDIT)

- Agent: Hermes-ObsForensic | Role: Observability / Forensic Trace auditor
- Date: 2026-08-31 | Branch: main
- Starting HEAD: 1f60832 | Ending HEAD: 56ec54b (audit span also observed dcc80d7 by parallel agent)
- Task: TASK-OBS-AUDIT | CHANGE-ID: CHG-0033
- Commits: d065e13 (taskboard registration), 56ec54b (deliverables + CHG-0033)
- Files changed: docs/architecture/observability-map.md (new), artifacts/forensics/observability-audit.json (new, -f add), agents/taskboard.md (row), agents/change_control.md (CHG-0033)
- Functions/classes changed: NONE (audit-only mandate honored; zero runtime code touched)
- Contracts changed: none (OBSERVABILITY_AUDIT v1 is a documentation artifact)
- Invariants: none touched
- Tests added: none (no code); Verification = 10-scenario black-box reconstruction from runtime evidence only + 6 parallel read-only evidence sweeps + live probes (X-Request-ID echo 200, /api/debug/trace TRACE_LOOKUP_ERROR dead import, /health READY, CLI status/forensic JSON)
- Bugs discovered: NONE new (existing BUG-177 overlaps OBS-002 class; BUG-070 overlaps OBS-009; BUG-162 lesson overlaps OBS-001 fail-safe visibility)
- Risks: none introduced. Key audit risks for operators: /api/debug/trace silently dead while health=READY; log<->DB correlation join impossible by id; shutdown/stop evidence absent; update chain reconstructable only from update-state.json.
- Unfinished work: none for audit scope. Future fix tasks should claim OBS-001..OBS-016 from artifacts/forensics/observability-audit.json.
- EXACT NEXT-AGENT INSTRUCTIONS:
  1. Highest-value single repair: OBS-001 - fix `from nexus_scalp.adapters.audit_db import get_default_audit_db_path` at web/server.py:4769 (module does not exist at HEAD; locate canonical path via adapters/database or AppConfig) and add a health/trace-endpoint self-check.
  2. OBS-002/OBS-003 (P0): extend _redact_value allowlist for id-shaped tokens (EXEC-/req_/upd-/fh-/INC- prefixes) and wire bind_correlation_id() per boot/decision; add regression tests asserting EXEC- ids survive redaction.
  3. OBS-004/OBS-005: structured audit-writer failure events + thread execution_id into audit_experiences writes (experience/intelligence.py:556-573).
- Known traps honored: structlog renders to stdout not caplog; artifacts/ gitignored (forced add like prior evidence JSONs); registry files edited byte-safely via python bytes (CRLF preserved); parallel CHG-0032-A1 decomposition WIP untouched (cli/*.py, forensics checks slices).
