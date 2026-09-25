# AGENT-11 HANDOFF — Execution/Risk/OrderManager Deep Forensic (2026-09-05)

Agent: Agent-11 (Nexus-Main orchestrated)
Role: Execution / Risk / OrderManager deep forensic + fix
Task: user brief 2026-09-05 EXECUTION / RISK / ORDERMANAGER DEEP FORENSIC + FIX
Branch: main
Starting HEAD: 54d1e904
Ending HEAD: 8735c343 (all Agent-11 commits ancestors of origin/main)

## Scope delivered
Four confirmed defects, every one FAIL-BEFORE proven with an executable probe, then fixed, regression-tested, and pushed:

- BUG-239 (152e8ebe): RiskEngine.evaluate_proposal crashed with UnboundLocalError (slippage_usd) on the micro-account + insufficient-margin path, and the trailing micro-account exception could resurrect a volume the free-margin guard had already zeroed. Fix: slippage_usd initialized before the impact loop; micro rescue only applies to an impact-reduced positive volume and re-verifies margin legality; margin-zeroed volume stays zeroed (fail-closed MICRO_ACCOUNT_*_REJECTED).
- BUG-240 (ad06738f): the MAX_TOTAL_EXPOSURE gate counted only symbol-scoped tickets although the contract (docstring + signals/policy.py) is engine-wide. Repro: a EURUSD position left the XAUUSD gate open. Fix: count_total_exposure(symbol=None).
- BUG-241 (ad06738f): the 3-rejection SAFE_MODE breaker existed only on the hedge path (execute_order); dispatch_order (the ONLY primary entry path) neither honored nor fed it. Fix: dispatch_order blocks when the circuit is open ([ENTRY_BLOCKED] layer=SAFE_MODE + terminal NOT_DISPATCHED outcome) and feeds the counter on both market and pending refusals (transitions at 3, resets on success).
- BUG-242 (d0a9b6d4): /api/positions/close + /api/positions/modify called engine.adapter.* directly - the only broker-mutation surface outside OrderLifecycleManager (repo-wide census). Fix: new close_position_manual / modify_position_manual wrappers (MANUAL_CLOSE evidence BEFORE the broker call, MANUAL audit rows, cache release, mechanism rollback on refusal); web routes resolve engine.order_manager and call the wrappers (manager unavailable -> 400).

## Registry artifacts
- agents/bugs.md: BUG-239/240/241/242 rows + CHG-0064 cross-reference block.
- agents/change_control.md: CHG-0064 entry (1dd60429).
- agents/taskboard.md: TASK-AGENT11-EXEC-RISK row.
- Note: Agent-6 also used IDs BUG-239/240 for web control-surface defects on the same day (436e4c50 branch line). Both defect classes are documented; the next registry pass should disambiguate the duplicate IDs.

## Test artifacts (both on origin/main)
- tests/unit/test_agent11_execution_risk_forensic.py — 14 tests (4x BUG-239, 3x BUG-240, 3x BUG-241, 4x BUG-242 incl. black-box web probe).
- tests/unit/test_agent11_scenario_coverage_contract.py — 6 tests pinning the real router surface: 21 explicit S-codes (14 CLOSE, S32, S44, S47, S48, S52, S56, S60 default HOLD), dispatcher-understood action set, profit-shield guard count, emergency-close priority ordering, state-machine bypass set.

## Verified clean (no defect, probes on real code)
- Dispatch idempotency (request_id terminal after first send).
- Ambiguous-fill recovery + pending idempotency guard (mt5_adapter).
- Cause-aware pending recovery (BUG-231 lane, preserved).
- Broker-truth reconciliation (INV-011): TicketsCache rebuild per tick, reconcile_pending_state repairs internal view, reconcile_missed_closes reconstructs from deal history.
- SHADOW boundary on the main decision path + hedge path (BUG-212 lane).
- Model-layer validation: TradeProposal/TradeOrder reject NaN/Inf/volume<=0.
- Router profit-shield: no winning trade closed by an emergency scenario.
- Position state machine: deterministic hysteresis, emergency bypass set correct.

## Residual risks (documented, not fixed - out of scope or low value)
- Sparse 60-scenario numbering (21 explicit codes) is a doc/contract debt; coverage suite now pins the real surface so drift is caught.
- enable_kill_switch has no active caller (SAFE_MODE breaker is the live path).
- Paper adapter has no mark-to-market and no margin enforcement (simulation fidelity limits; risk layer remains authoritative).
- Web operator mutations run on the FastAPI threadpool concurrently with the tick loop; adapters carry no lock - safety rests on MT5 idempotency guards + manager-side cache release. A lock would be the next hardening step.

## Next-agent instructions
1. Do NOT re-fix BUG-239/240/241/242 - they are on origin/main with probes.
2. If touching the router, run tests/unit/test_agent11_scenario_coverage_contract.py first; update the census deliberately if scenarios change.
3. Disambiguate the BUG-239/240 ID collision (Agent-11 risk/execution vs Agent-6 web control-surface) in the next registry pass.
4. If hardening concurrency, add an adapter-level lock around web operator mutations; keep INV-004 routing through OrderLifecycleManager.
