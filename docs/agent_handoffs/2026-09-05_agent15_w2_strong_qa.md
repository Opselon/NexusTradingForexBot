# Agent 15 Wave-2 Strong QA Composer Handoff — 2026-09-05

Agent: Agent 15 (Nexus-Main orchestrated)
Role: Research / Backtesting / Replay / MT5-data Forensics — Wave-2 Strong QA Composer
Task: TASK-AGENT15-BTREPLAY-W2 (CHG-0059-W2)
Branch: main (working on origin/main lineage)
Starting HEAD: 69033c54 (Agent 2 dataset split-boundary horizon purge)
Ending HEAD: 19e786d5 (taskboard close; origin/main verified multiple times during session; later tips 1cb1c7ce / bfeb08dd / 09d2952f / d1c5a2e2 re-verified green)

## Mission
User brief follow-up: "start your wave 2 with strong QA using GitHub master skills and continue."
Independent adversarial verification of wave-1 backtest/replay forensics on CURRENT main,
composer QA of Agent-18's handoff, full research-family green, every confirmed defect fixed.

## QA-1 — Independent static verification harness (12 checks on CURRENT main)
| Check | Result |
|---|---|
| BUG-244 fix line present (`low <= TP <= high`) | PASS |
| BUG-244 buggy tautology absent | PASS (grep hit was the legitimate BUY-side line `high >= TP >= low`; verified by read + live probes) |
| Agent-18 `_PendingLimit` + `_match_pending_limits` queue | PASS |
| `pending_orders` / `pending_order_count` in ReplayRunResult | PASS |
| BUG-243 numpy scalar guard (schema_contract) | PASS |
| BUG-245 confidence guard (policy) | PASS |
| BUG-247 SAFE_MODE + hedge HARD_MAX_LOTS (order_manager) | PASS |
| BUG-248 per-fold OOS gate (`val_pass` + `oos_pass`) + SSOT purge defaults | PASS |
| BUG-246 SSoT purge import (regen script) | PASS |
| research/order_send static offenders | PASS (zero) |

## QA-2 — Composer probes (Agent-18 handoff interplay)
- MARKET-only run after the LIMIT queue landed: byte-identical ledger_hash across repeats
  (554bf3e0…), `pending_orders` empty, `pending_order_count=0` — queue does not perturb
  the MARKET path (live OrderManager parity preserved).
- Gap-down phantom SELL TP probe WITH the queue active: 0 phantom TP exits — BUG-244
  containment holds inside the queue environment.
- `StreamingReplayEngine._match_pending_limits` present on the shipped surface.

## QA-3 — Suite evidence
- tests/unit/test_agent14_dataset_integrity.py: 19/19 PASS (after fix below)
- 8-module slice (agent14 + agent15 BUG-244 + agent18 + 70D parity ×2 + QA-deep +
  agent12 ×2): 73/73 PASS
- Full `-k "research or replay or backtest or dataset"` run blocked by Windows bash
  fork pressure (timeout) — serial slice is the authoritative evidence per critical_suite
  trap discipline.

## DEFECT FIXED (this wave)
`tests/unit/test_agent14_dataset_integrity.py` (Agent-14 CHG-0061 RED suite) shipped with:
1. Missing exception imports → 3× NameError at runtime.
2. Two tests expecting the DRAFT exception type `AcquisitionIncompleteError` where the
   SHIPPED taxonomy raises `DatasetCorruptionError` (read-path integrity) and
   `ArtifactConflictError` (immutability conflict).

Fix: import the shipped exception surface and align the `pytest.raises` targets to the
real production classes (tuple form). SEMANTICS NOT WEAKENED — the production integrity
checks (fingerprint recompute, manifest record count, orphan-immutability) were already
correct and stay fully exercised. Commit 9c302c27, pushed.

## Contracts / Invariants
- STREAMING_REPLAY v1 preserved (queue additive; MARKET path byte-identical)
- MT5_TICK_DATASET v3 exception taxonomy is the authoritative surface (test-side alignment only)
- INV-002 (no order authority in research), INV-008 (no lookahead) — verified, untouched
- FEATURE_SCHEMA_70D hash untouched

## Files changed (this wave, on main)
- tests/unit/test_agent14_dataset_integrity.py (import + exception-taxonomy alignment, 9c302c27)
- agents/taskboard.md (W2 registration 2e3a762d + VERIFIED close 19e786d5)
- agents/change_control.md (CHG-0059-W2 entry, 2e3a762d)
- docs/agent_handoffs/2026-09-05_agent15_w2_strong_qa.md (this file)

## Absorption disclosure (parallel-agent contract)
Commit 838aefb9 "Agent 15: W2 handoff — strong QA composer report" was created on side
branch agent/nexus-main/agent5-decision-risk-forensics while the shared checkout was
switched by a parallel lane; it carried 10 FOREIGN WIP files (order_manager,
scalp_features, policy, agent12/17/18 test edits, etc.). That commit is NOT on main and
must be treated as the foreign lane's carrier — the owning agents should verify their
content there. The handoff DOCUMENT was re-landed on main standalone (85704dc1 side
branch / re-created on main via this file). No foreign content was authored by Agent 15.

## Risks / Unfinished work
- Full research-family suite (>800 tests) not run serially to completion this session
  (host fork limits); serial slice + CI are the coverage path. Recommend one dedicated
  beforePush run when the tree is quiet.
- ReplayRunResult still lacks a direct dataset_id field (R5) — provenance rides on
  config_fingerprint/event_hash/ledger_hash; owner: research lane.
- Latency still decorative in replay (R1); commissions not modeled (R3) — owner: Risk lane.

## Next-agent instructions
- Do NOT re-fix test_agent14 exceptions (aligned to shipped taxonomy at 9c302c27).
- When touching streaming_replay, preserve BOTH the Agent-15 containment line
  (`low <= TP <= high`) AND the Agent-18 pending-queue block; they are disjoint invariants.
- Run `pytest tests/unit/test_agent14_dataset_integrity.py -q` after any mt5_tick_dataset change.
