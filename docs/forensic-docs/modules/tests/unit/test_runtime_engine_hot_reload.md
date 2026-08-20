# tests/unit/test_runtime_engine_hot_reload.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- Engine-level hot-reload tests (§51/§66): METHOD behavior changes with the runtime snapshot WITHOUT restart — same engine instance, same PID — proving runtime consumers read the NEW config.
- RiskEngine: max-spread gate changes after save (`order1 is None` blocked by the OLD spread gate, then allowed); risk-per-trade sizing uses the new value; min-RR gate changes after save (`order1 is None` — RR 1.5 < min 1.8 → blocked).
- Policy: ATR SL buffer deterministic method changes with the snapshot.
- FeatureEngine: FVG sensitivity changes FVG detection; OB lookback changes swing scan topology.
- Multi-subsystem atomic apply: ONE save updates risk + policy + feature subsystems consistently (atomic snapshot swap).
- Fixtures: `_MiniServices` sync harness + `_base_config`; orders/proposals built via `_tick`/`_proposal`/`_account`/`_symbol`.
- 21 defs / 368 lines.