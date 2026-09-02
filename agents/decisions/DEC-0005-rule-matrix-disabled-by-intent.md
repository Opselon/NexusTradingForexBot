# DECISIONS — Nexus Scalp Engine (NSE)

> Maintained per the MASTER MULTI-AGENT CONTRACT §39 (see `agents/multi-agent-git-contract.md`).
> Important architectural choices receive a decision record DEC-XXXX.
> Required: Decision, Context, Evidence, Alternatives, Chosen path, Why, Consequences.
> This prevents future agents from accidentally reversing established safety decisions.

## DEC-0001 — UNKNOWN broker exit reason stays UNKNOWN
- **Decision:** When broker exit evidence is incomplete/ambiguous, classification stays UNKNOWN rather than being promoted to MANUAL_CLOSE or another confident class.
- **Context:** BUG-081 — exit classifier produced falsehoods by assuming MANUAL under incomplete evidence; ledger lost money twice.
- **Evidence:** agents/bugs.md BUG-081; exit-classification forensics.
- **Alternatives:** (a) default to MANUAL_CLOSE when reason missing; (b) drop the trade from accounting.
- **Chosen path:** UNKNOWN evidence stays UNKNOWN (INV-012).
- **Why:** silent promotion corrupts learning outcomes and accounting lineage.
- **Consequences:** EXIT_CLASSIFICATION contract v2 (evidence precedence); downstream consumers must handle UNKNOWN.

## DEC-0002 — nodejs runtime role
- **Decision:** Node.js is required at runtime only for the Web build toolchain; the Python engine never invokes node at runtime.

## DEC-0002 — hermes-kanban-swarm-integration
- **Decision:** see entry in this file (appended by Hermes-Kanban-Swarm integration task).

## DEC-0003 — hermes-kanban-swarm-integration
- **Decision:** see entry in this file (appended by Hermes-Kanban-Swarm integration task).

## DEC-0005 — RuleMatrix full-sweep entry rules ship disabled by intent (operator opt-in via UI toggle); not strategy starvation (2026-09-03, Hermes-Subagent rule-matrix liveness audit)
- **Decision:** The `trading_rules_config` seed keeping every rule `is_enabled=0` is an INTENDED default, not a defect. No code change to seeding, no auto-enable path, and no forced enablement will be added. The rule matrix is an operator-controlled overlay on the AI policy, enabled per-rule via the Web Rules panel (`/api/rules/toggle`) or directly via `AuditRepository.toggle_trading_rule`, with a 5s TTL cache that picks up toggles live.
- **Context:** Detective brief asked whether the rule_matrix "full sweep" strategy layer is dead in the live engine by intent or defect (UNINTENDED-STARVATION candidate distinct from the training-layer starvation).
- **Evidence:**
  - Seed ships disabled: `src/nexus_scalp/adapters/database/audit_repository.py:2420` — `"""Seeds the trading_rules_config table with all 30+ rules, disabled by default."""`; INSERT at `:2562` hardcodes `VALUES (?, 0, ?, ?)` for all 30 rules; verified on a fresh repo: 30 rules, 0 enabled.
  - Intent documented in shipping UI copy: `Web/index.html:2044` — "PERSISTENCE CONTROL: All 30+ institutional rules are disabled by default. Enabling a rule overrides standard PyTorch AI."
  - Sanctioned enable path exists and works: `Web/app.js:6576,6648` → `POST /api/rules/toggle` → `src/nexus_scalp/web/diagnostics_state_routes.py:522` → `AuditRepository.toggle_trading_rule` (`audit_repository.py:2591`) + forced cache refresh (`diagnostics_state_routes.py:532`). Live probe on a temp DB: toggle → `refresh_cache(force=True)` → `is_enabled` flips True.
  - Live wiring is reachable, not orphaned: `signals/policy.py:741-778` (filters → entries) and `execution/order_manager.py:4545-4548` (in-trade exits) call the matrix on the live tick path; LiveEngine constructs `RuleMatrixEngine` (`application/live_engine.py:739`) and injects it into SignalPolicy (`:1126`) and OrderLifecycleManager (`:1138`).
  - Inverse concern (filters becoming redundant with policy's own gates) is unfounded: `policy.py` contains zero `RULE_*` references — the `RULE_SPREAD_SQUEEZE_ONLY` / `RULE_LIQUIDITY_SWEEP_CONFIRM` filter semantics exist only in `rule_matrix.py:564-576`.
  - Historical corroboration: `docs/TRADE_AVAILABILITY_FORENSIC_FINAL.md:84,112` and `docs/agent_handoffs/2026-08-19_Hermes-Forensic-02_trade-availability.md:34` — forensic agents repeatedly observed "all 30 DB trading rules DISABLED — RuleMatrix contributed nothing" and treated it as configuration fact, not a bug; `docs/forensic-docs/modules/signals/rule_matrix.md:46` documents fresh-DB-disabled as the expected state.
- **Alternatives:** (a) auto-enable the 12 full-sweep entry rules — rejected: silently overrides the AI policy with hand-coded sniper entries (some with hardcoded 1.5/2.5 gold-price stops) and contradicts the documented persistence-control intent; (b) enable only filter/safeguard rules by default — rejected: inverse-risk (LIQUIDITY_SWEEP_CONFIRM / KILLZONE filters would mass-block the AI's own candidates); (c) leave as is.
- **Chosen path:** (c) — disabled-by-default is the designed state; the layer is latent, not dead: it activates per-rule the moment an operator toggles it, with live cache refresh.
- **Why:** the matrix is a deliberate operator-controlled persistence/override layer ("Enabling a rule overrides standard PyTorch AI"), consistent with the repo's broader pattern of opt-in side systems (news auto-analysis, telegram, training all default off).
- **Consequences:** no regression for the starvation class at the LIVE layer: the AI policy + risk + experience gates remain the sole default trade source. Any future "give me more trades" work must go through evidence-gated strategy work (strategy_factory/sample_maker, owned by other agents), NOT by mass-flipping rule toggles in code. One residual hygiene note (not a defect): `evaluate_risk_and_safeguards` (rule_matrix.py:734) has no production caller and never had one — dead-surface cleanup is a separate, low-priority task.

## Registry notes
- New decisions: append DEC-XXXX entries; reference BUG-NNN / CHANGE-ID / TASK-ID where applicable.
