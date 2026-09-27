# Full integration report

## Base and inventory
- `origin/main`: `033c6dcdc1e18a461c9b84ec231f7b94e8a0eecd`
- Integration worktree: `C:/c/tmp/nse-full-integration`
- Branch: `agent/hermes/full-integration`
- Protected dual-db worktrees and original checkouts were excluded and untouched.
- Eligible committed sources were inventoried from registered worktrees. Dirty source worktrees were preserved; no uncommitted files were staged or copied.
- Duplicate tips: EUML CLI/contract/provision/secrets/startup/studio/tests/UI all point to `522ce4526736dfa60edbed08eca6deebea026087`; several detached/source worktrees had no common base or no committed delta and were not merge candidates.

## Dispositions
- **INTEGRATED** `agent/hermes/main-integration` / `0c8eb88603602ce1a34e2f6888d827fff3d0291d`: merged as `d5d3e6e` (merge commit). Carried coherent MT5 parity, trace, replay, memory-bound, gateway, review-lock and integration provenance changes. `integration-conflicts.txt` retained as source provenance.
- **ATTEMPTED, REVERTED / UNRESOLVED REQUIRES HUMAN REVIEW** `agent/hermes/pg-mig-schema` / `1654015331d33d0656476a42e8b4e5610711ce7f`: merge conflicted in `src/nexus_scalp/database/migration/pg_schema.py`; the candidate removed the existing DBA migration layer when resolved with the lane version. Reverted immediately to preserve current integration behavior. The lane remains untouched and its changes are not silently claimed as integrated.
- **ALREADY REPRESENTED / NO-OP** `agent/feature/db-pg-audit`, `agent/feature/mt5-parity-d`, `agent/feature/mt5-parity-e`, `agent/feature/mt5-parity-f`, `agent/phase2/trace-cli-fusion`, `agent/feature/review-lock-read-only`: `git merge` reported already up to date after the main-integration bundle.
- **NOT MERGED / OVERLAP OR AMBIGUITY** `agent/hermes/pg-read-plane`, `agent/feature/DB-FABRIC-NEWSPG`, `agent/feature/mt5-parity-a`, `agent/feature/mt5-parity-b`, `agent/hermes/sec-remediation-round2`, `agent/hermes/eur-d`, `agent/hermes/trading-ux9`, `agent/hermes/i18nc-d`: each produced content conflicts with already integrated code or shared contracts. Each merge was aborted; source branches/worktrees remain unchanged.
- **DUPLICATE/SUPERSEDED** detached/duplicate worktrees and branches with identical tips or no delta versus the base were omitted with their evidence retained in the machine inventory pass.

## Required conflict records
- **SOURCE LANES:** `agent/hermes/pg-mig-schema` + current integration
- **FILES:** `src/nexus_scalp/database/migration/pg_schema.py`
- **CONFLICT:** lane version removed 286 lines of the existing DBA migration layer while current integration retained it.
- **RESOLUTION:** aborted merge and reverted the merge commit; retained current integration version.
- **WHY:** automatic/manual lane resolution would have silently dropped an existing PostgreSQL contract.
- **TESTS:** focused tests were attempted after restoration; collection was blocked by missing `fastapi` in the available interpreter.

## Verification
- `git status --short`: clean after report commit.
- `git diff --check`: passed.
- Conflict-marker scan: no actual merge markers; separator-only lines and test-output artifacts were excluded/classified as decorative/generated.
- Diff stat versus origin/main: recorded by command; final integration consists of the main-integration bundle plus this report.
- Focused command: `PYTHONPATH=src C:/Users/Capsizer/source/repos/NexusTradingForexBot/.venv/Scripts/python.exe -m pytest tests/unit/test_mcp_adapter_contract.py tests/unit/test_replay_routes_contract.py tests/unit/test_trace_mt5_emit.py -q -p no:cacheprovider`.
- Result: **BLOCKED**, exit 2 during collection because `fastapi` is unavailable in the selected venv/interpreter; no test assertion result is claimed.

## Preservation
- Dirty worktrees were preserved and not modified.
- No source worktree or branch was deleted, reset, stashed, cleaned, or force-updated.
- Dual-db sources were explicitly excluded and untouched.
- PR is for human review only and must not be merged by this operation.
