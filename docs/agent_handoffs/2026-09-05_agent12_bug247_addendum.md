# Addendum — Agent-12, BUG-247 residual hardening (CHG-0058-FOLLOWUP)

- Date: 2026-09-05
- Parent handoff: `docs/agent_handoffs/2026-09-05_agent12_execution_forensic.md` (CHG-0058 mission)
- Commits this addendum: `bf7a6ade` (code+test), `a1fb7c35` (change-control row), `24464757` (taskboard row).
- Tests: `tests/unit/test_agent12_bug247_hardening.py` (3, paper adapter, offline).
- Verdict patch: the delegated sweep found P1/P2 as GAPS inside the single authority
  (file `subagent-summary-1-20260905_034332_422383.txt`). The ledger now carries
  the gaps as `agents/bugs.md:BUG-247` (the `BUG-245` id is owned by Agent-17
  OOS lane, so the residual took 247). Fix commit rewrites the two gaps
  fail-closed; the `main` battery commit `bf7a6ade` carries them on
  `origin/main`. The handoff absorbs the addendum as its residual section.

## Why

The CHG-0058 census proved single-authority (no second execution path outside
`execution/order_manager.py`). The sweep listed the hedge (P2) and AI-flip
reversal (P1) as residual GAPS that lived INSIDE the manager but on
different legs than the primary `dispatch_order` path. Both fixes are now
executable on `origin/main`.

## Fix

- `execute_order` (hedge entry): `_clamp_dispatch_volume` now gates every
  `TradeOrder` before the broker write (`src/`, `HARD_MAX_LOTS=10.0` via
  `clamped_vol`).
- `_run_protection_chain` AI-flip fast reversal: the *follow-up*
  `place_pending_order` now checks `global_state == "SAFE_MODE"` before the
  write; the protective CLOSE leg still fires (risk-reducing).

## Evidence

- `tests/unit/test_agent12_bug247_hardening.py::TestBug247*` validates
  `volume=100.0 -> 10.0` and the `SAFE_MODE` suppress inside the protection
  chain. The `main` 21-test sum `test_agent12_*` (18 + 3) is green on every
  run in this window (`tests/unit/test_agent12_execution_forensic.py` plus
  this file). `py_compile` + `ruff` + `mypy` clean on `order_manager.py`.
- `git show origin/main~0:src/nexus_scalp/execution/order_manager.py | grep -c "Hedge entry blocked"` is `1` on `origin/main`.

## Swarm note

Build `bf7a6ade` was first made on `agent4-forensic` and lost to the merge
state in `main`. It was re-applied selectively (`-- execution/order_manager.py
tests/unit/test_agent12_bug247_hardening.py`) with foreign dataset-lane WIP
left unstaged (never overwritten), then landed as `f33c434` / `a1fb7c35` /
`24464757` on `main`.

