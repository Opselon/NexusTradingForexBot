# tests/unit/test_execution_architecture.py

- GUARDS: Execution architecture: god-mode override, predictive limit orders, tick-sweep execution, pending-order manager + falling knife, risk sizing/exposure/portfolio context.
- KEY ASSERTIONS:
  - `test_god_mode_override` (emergency override bypasses normal sizing); `test_predictive_limit_orders`; `test_tick_sweep_execution`; `test_pending_order_manager_and_falling_knife` (pending orders constrained while knife falls); `test_risk_sizing_exposure_and_portfolio_context` (12 asserts).
- PITFALLS IT ENCODES: overrides must be explicit and bounded (never a silent bypass); pending orders must not stack during a falling knife.
- NOTES: DummyPendingOrder/DummyMT5Port harness; exercises OrderLifecycleManager + RiskEngine + SignalPolicy + AuditRepository together.
