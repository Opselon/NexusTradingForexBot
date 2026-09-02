# 70D Full Application Flow (TASK-70D-SYSTEM-FLOW-FORENSICS)

> Agent: Hermes-Forensic-70D · 2026-08-19
> The complete application as one flow — data, workers, chart, API, UI.

```
START (python -m nexus_scalp.cli.main run --mode PAPER|LIVE|SHADOW)
   │
   ▼
AppConfig.load_config (configs/base.yaml + env)
   │
   ▼
Adapters (DirectMT5 / RemoteGateway / Paper) + SettingsService
   │
   ▼
Subsystems:
   ScalpFeatureEngine (50D) · RegimeClassifier · SignalPolicy · RuleMatrix
   RiskEngine · OrderManager · AuditRepository (SQLite WAL)
   NewsEngine (news.db) · LiquidityGovernor · CandleIntel (candle_intel.db)
   AccountingCore · Intelligence · ResearchPipeline · ModelLifecycle
   Governance (load gate) · ShadowEngine · Shadow70Runtime
   Migration gate (TASK-10) BEFORE READY
   │
   ▼
ModelBundle load (Champion 50D scalp_v1; load gate validation)
   │
   ▼
LiveEngine.start → run_loop
   │
   ├─ tick path (async, per tick):
   │    tick → bars → 50D features → regime → news context →
   │    Champion inference → policy → risk → execution →
   │    50D shadow record → 70D shadow observation (BUG-105 fixed) →
   │    overlays → SSE broadcast
   │
   ├─ worker kicks (asyncio.to_thread, off tick path):
   │    accounting · history_sync · intelligence · research ·
   │    training · shadow · news · hygiene · shadow70
   │
   └─ periodic: governance health · purge · telegram daily/warmup
   │
   ▼
Web server (FastAPI): /api/status · /api/live/state · /api/chart/history ·
   /api/mt5/status · /api/liquidity/* · /api/news/* · /api/research/* ·
   /api/models/* (incl. shadow70) · /api/rules · /api/db/* · /api/diagnostics/*
   │
   ▼
Frontend (Web/app.js + api_client.js): state merge (versioned) ·
   chart candles + overlays · liquidity panel · news panel ·
   70D shadow panel · rules toggles
```

## Error paths (explicit, no fake success)

| Stage | Failure | Behavior |
| :--- | :--- | :--- |
| MT5 disconnected | adapter.get_rate_history raises | chart falls back ENGINE_STATE with source field; /api/mt5/status reports connection error_state |
| News unavailable | context unavailable | CurrentNewsContext(available=False); gate no-op; UI shows empty state |
| Liquidity producer fails | compute_liquidity_features raises | governor status UNAVAILABLE + reason; neutral 10D with explicit marker |
| 70D model missing | no registry candidate | Shadow70LoadGate: NO_VALIDATED_CANDIDATE; runtime IDLE (truthful) |
| Wrong model dimension | artifact dim != schema | load gate SHADOW_BLOCKED / InferenceValidator DIMENSION_MISMATCH |
| DB unavailable | audit queue write fails | queued writes logged; live path unaffected (INV-001) |
| Telegram fails | notifier error | worker health DEGRADED + failure category; never silent |
| Worker exception | tick() raises | cycle-isolated; `[WORKER] event=KICK_FAILED`; next cycle retries |

## One-source-of-truth invariants (audited)

1. Schema registry (features/schema.py) is the only dimension authority.
2. schema_contract.py is the only 70D contract (hash, family layout).
3. AccountingCore is the only PnL/drawdown authority (no frontend recompute).
4. Champion model artifact is the only production inference source.
5. AuditRepository queue is the only DB write path on the hot path.
6. SettingsService is the only Telegram/liquidity config persistence
   (INV-010/BUG-080 discipline).
7. Shadow70 observes only (INV-018: no adapter/order manager/risk engine).
8. Every API leaf carries provenance + timestamp; null ≠ fake zero.

## Verification evidence (this task, all green)

- 75 shadow70+parity unit tests · 34 inference/replay tests · 31 parity/
  dataset tests · 4 BUG-105 regressions · 105 combined run.
- Deterministic 70D assembly trace (artifacts/forensics/feature_vector_trace.json).
- Repro probe proves the dead-code bug and the fix (happy 1 / forced-fail 2).
- Git: BUG-105 fix + forensic map pushed (066a7ba mine; absorbed into swarm).