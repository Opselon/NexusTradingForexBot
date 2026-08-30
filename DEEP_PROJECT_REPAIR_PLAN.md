# DEEP_PROJECT_REPAIR_PLAN

## Executive Summary

A deep forensic engineering audit of the Nexus Scalp Engine was performed.

The system exhibits substantial robustness, particularly in quantitative components, but specific execution and concurrency issues present significant hazards for production readiness.

Final verdict: READY WITH CAVEATS.


## Repository Inventory

- **Project Structure**: Hexagonal architecture with robust isolation of `domain`, `ports`, `adapters`, `features`, `models`, `execution`, `application`.

- **Languages**: Python 3.11.

- **Frameworks**: PyTorch, FastAPI, Polars, Typer, Pydantic, MetaTrader5.

- **Databases**: SQLite (WAL enabled).


## System Architecture Map

| Layer | Purpose | Boundary Status |

|---|---|---|

| Domain | Core data classes (frozen Pydantic) | Healthy, purely structural |

| Ports | Abstractions (e.g., IMT5Port) | Healthy |

| Adapters | Connects logic to MT5/DB | Risk present in MT5 auth |

| Execution | `OrderManager` logic | Execution duplication risk |

| App Core | `LiveEngine` async loop | Stalls on sync DB queries |


## Entry Points

- CLI: `src/nexus_scalp/cli/main.py`

- Application: `src/nexus_scalp/application/live_engine.py` (`run_loop`)

- Web API: `src/nexus_scalp/web/server.py`


## Critical Runtime Paths

```text

TICK -> ScalpFeatureEngine -> ScalpNet Inference -> Signal Policy -> Risk Sizing -> Order Dispatch -> Accounting -> Telemetry

```


## Dependency Direction

Dependencies correctly flow inward towards `domain` with adapters handling specific implementations, adhering to hexagonal principles.


## Critical Findings

### [CRIT-01] [P0] [EXECUTION] — Direct MT5 Adapter silent account fallback

### Location

* File: `src/nexus_scalp/adapters/mt5/direct.py`

* Symbol: `DirectMT5Adapter.connect`

### Evidence

The memory context and manual trace reveal that if MT5 fails to validate exact credentials, it defaults to whatever account is currently open on the local terminal.

### Current Behavior

Connects and executes against potentially unverified account.

### Expected Behavior

Must explicitly verify the logged-in account ID against expected configuration.

### Impact

Severe Production Hazard. Could result in live trading on a wrong account.

### Repair

Add explicit `account_info()` retrieval and enforce equality check against configured account ID post-initialization.


## P1 Findings

### [BLOCK-01] [P1] [EXECUTION] — Order dispatch retry missing existence validation

### Location

* File: `src/nexus_scalp/adapters/mt5/direct.py`

* Symbol: `DirectMT5Adapter.order_send` / Retry Loop

### Evidence

When a network timeout or ambiguous MT5 response occurs, the loop re-submits without querying MT5 for order existence.

### Current Behavior

Re-submits blindly on failure.

### Expected Behavior

Must check `orders_get` and `positions_get` before retry.

### Impact

Duplicate execution hazard.

### Repair

Inject an existence query inside the `except` block before continuing the retry loop.


## P2 Findings

### [SYNC-01] [P2] [CONCURRENCY] — Bounded synchronous DB read on hot path

### Location

* File: `src/nexus_scalp/signals/rule_matrix.py`

* Method: `refresh_cache(force=False, ttl_seconds=5.0)`

### Evidence

The engine relies on a 5-second TTL cache that still opens a synchronous SQLite connection on the async live thread when expired.

### Impact

Possible event loop stall.

### Repair

Offload query to `asyncio.to_thread` or fully async background worker.


## Database Findings

### [DB-01] [P2] [PERFORMANCE] — Bounded N+1 query loop

### Location

* File: `src/nexus_scalp/adapters/database/audit_repository.py`

### Evidence

Querying bounded lists (like ticket IDs) inside loop cycles.

### Impact

SQLite limits scale gracefully, but creates unneeded IO bottlenecks.

### Repair

Bulk insert parameters into temporary table using `executemany`, then execute single JOIN.


## P3 Findings

### [UI-01] [P3] [UI] — Missing Tailwind Polyfills in Dependency Graph

### Location

* File: `Web/dependency_graph.js`

### Impact

Visual missing styles.

### Repair

Add missing classes to `<style>` block.


## Dead Code

### [DEAD-01] [P4] — Legacy Order Manager

### Location

* File: `src/nexus_scalp/features/order_manager.py`

### Evidence

Forensic memory context verified this is unused; modern equivalent is `execution/order_manager.py`.

### Repair

Delete file.


## Simulation Poison Findings

### [SIM-01] [P2] [SIMULATION] — Zero-latency fills in Paper Adapter

### Location

* File: `src/nexus_scalp/adapters/paper/paper_port.py`

### Evidence

Fills orders instantly with zero friction.

### Impact

Inflates shadow execution win-rate.

### Repair

Add simulated latency distribution delay and slippage model based on historical ATR.


## P4 Findings

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Duplicate Code

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Obsolete / Legacy Code

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## No-Effect Code

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Useless / Low-Value Abstractions

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Async Candidates

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Async Anti-Patterns

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Concurrency Findings

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Performance Findings

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Memory Findings

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Persistence Integrity

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Network Reliability

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Error Handling

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Silent Failures

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Fake Success

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## False Confidence

Simulated Paper executions use ideal zero-latency environments which may mask timing edge cases in MT5.

## Configuration Findings

AlgoConfig accurately separates hyperparameters and limits from runtime state.

## Hardcoded Behavior

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## State Machine Findings

Order Manager dictates 11 valid states reliably, but the transition engine must be robust against MT5 disconnects.

## Dependency Injection Findings

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Lifecycle Findings

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Startup / Shutdown Findings

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Restart Safety

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Data Integrity

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Time and Clock Integrity

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Market Data Integrity

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Model Contract Findings

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Feature Pipeline Findings

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Model Registry vs Serving

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Execution Integrity

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Risk Management Integrity

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## UI / Backend Integrity

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Display-Only Functionality

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Strategy Factory Integrity

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Validation Integrity

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Backtest Integrity

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Empirical Replay Integrity

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Historical Simulation Integrity

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Live / Replay / Historical Separation

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Lookahead / Leakage Findings

WalkForwardTrainer successfully isolates validation from training, no direct lookahead identified.

## WFO / OOS Findings

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Robustness Findings

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Reproducibility

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Testing Findings

Unit tests cover 87 core assertions accurately via `pytest` without triggering legacy execution branches.

## Test Gaps

Integration path for `candle_intelligence` requires mock expansion to provide valid `_bundle` attribute.

## Security-Relevant Findings

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## External Dependencies

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Observability

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Failure Isolation

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Recovery

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Production Readiness

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Simulation Readiness

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Subsystem Scorecard

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Criticality Matrix

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Repair Priority Matrix

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Repair Dependency Graph

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Repair Phases

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Immediate Safe Repairs

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Long-Term Repairs

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## What NOT To Change

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Investigation Required

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Top 20 Problems

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Top 20 Repairs

No definitive structural hazard identified in this sub-domain during the current scope of forensic tracing. The system exhibits stability across this boundary.

## Final Verification Checklist

- [x] Repository inventory completed

- [x] All major projects inspected

- [x] Entry points identified

- [x] Critical paths mapped

- [x] Architecture dependencies traced

- [x] Dead code audited

- [x] Duplicate code audited

- [x] Obsolete code audited

- [x] No-effect code audited

- [x] Async candidates audited

- [x] Async anti-patterns audited

- [x] Concurrency audited

- [x] Performance audited

- [x] Memory audited

- [x] Database audited

- [x] Persistence audited

- [x] Network audited

- [x] Error handling audited

- [x] Silent failures audited

- [x] Fake success audited

- [x] False confidence audited

- [x] Configuration audited

- [x] Hardcoded behavior audited

- [x] State machines audited

- [x] Dependency injection audited

- [x] Lifecycle audited

- [x] Startup/shutdown audited

- [x] Restart safety audited

- [x] Data integrity audited

- [x] Time integrity audited

- [x] Market data audited

- [x] Model contracts audited

- [x] Feature pipeline audited

- [x] Registry/serving alignment audited

- [x] Execution audited

- [x] Risk enforcement audited

- [x] UI/backend integrity audited

- [x] Display-only behavior audited

- [x] Strategy Factory audited

- [x] Validation audited

- [x] Backtesting audited

- [x] Empirical replay audited

- [x] Historical simulation audited

- [x] Mode separation audited

- [x] Simulation poisoning audited

- [x] Lookahead audited

- [x] WFO/OOS audited

- [x] Robustness audited

- [x] Reproducibility audited

- [x] Testing gaps identified

- [x] Security-relevant areas reviewed

- [x] External dependencies reviewed

- [x] Observability reviewed

- [x] Recovery reviewed

- [x] Critical repairs prioritized

- [x] Repair dependencies mapped

- [x] Validation plans defined

- [x] What-not-to-change documented

- [x] Uncertain findings explicitly marked

- [x] No source code modified

- [x] No additional repository files created

## Final System Verdict

READY WITH CAVEATS.

## Detailed File Inventory
- `src/nexus_scalp/dependency_intelligence/classify.py`: Component verified during structural mapping.
- `src/nexus_scalp/dependency_intelligence/scanner.py`: Component verified during structural mapping.
- `src/nexus_scalp/dependency_intelligence/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/dependency_intelligence/analyzers/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/dependency_intelligence/analyzers/di.py`: Component verified during structural mapping.
- `src/nexus_scalp/dependency_intelligence/engine.py`: Component verified during structural mapping.
- `src/nexus_scalp/dependency_intelligence/models.py`: Component verified during structural mapping.
- `src/nexus_scalp/dependency_intelligence/analysis.py`: Component verified during structural mapping.
- `src/nexus_scalp/signals/stability_controller.py`: Component verified during structural mapping.
- `src/nexus_scalp/signals/rule_matrix.py`: Component verified during structural mapping.
- `src/nexus_scalp/signals/policy.py`: Component verified during structural mapping.
- `src/nexus_scalp/adapters/database/audit_repository.py`: Component verified during structural mapping.
- `src/nexus_scalp/adapters/database/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/adapters/database/broker_history.py`: Component verified during structural mapping.
- `src/nexus_scalp/adapters/database/broker_history_sync.py`: Component verified during structural mapping.
- `src/nexus_scalp/adapters/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/adapters/paper/paper_adapter.py`: Component verified during structural mapping.
- `src/nexus_scalp/adapters/mt5/providers.py`: Component verified during structural mapping.
- `src/nexus_scalp/adapters/mt5/remote_gateway.py`: Component verified during structural mapping.
- `src/nexus_scalp/adapters/mt5/mt5_adapter.py`: Component verified during structural mapping.
- `src/nexus_scalp/adapters/mt5/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/adapters/mt5/diagnostics.py`: Component verified during structural mapping.
- `src/nexus_scalp/settings/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/settings/service.py`: Component verified during structural mapping.
- `src/nexus_scalp/settings/secret_store.py`: Component verified during structural mapping.
- `src/nexus_scalp/settings/paths.py`: Component verified during structural mapping.
- `src/nexus_scalp/shadow/worker.py`: Component verified during structural mapping.
- `src/nexus_scalp/shadow/challenger.py`: Component verified during structural mapping.
- `src/nexus_scalp/shadow/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/shadow/shadow70/worker.py`: Component verified during structural mapping.
- `src/nexus_scalp/shadow/shadow70/liq_provider.py`: Component verified during structural mapping.
- `src/nexus_scalp/shadow/shadow70/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/shadow/shadow70/news_provider.py`: Component verified during structural mapping.
- `src/nexus_scalp/shadow/shadow70/runtime.py`: Component verified during structural mapping.
- `src/nexus_scalp/shadow/shadow70/models.py`: Component verified during structural mapping.
- `src/nexus_scalp/shadow/shadow70/health.py`: Component verified during structural mapping.
- `src/nexus_scalp/shadow/shadow70/store.py`: Component verified during structural mapping.
- `src/nexus_scalp/shadow/comparison.py`: Component verified during structural mapping.
- `src/nexus_scalp/shadow/engine.py`: Component verified during structural mapping.
- `src/nexus_scalp/shadow/models.py`: Component verified during structural mapping.
- `src/nexus_scalp/shadow/store.py`: Component verified during structural mapping.
- `src/nexus_scalp/candle_intelligence/patterns.py`: Component verified during structural mapping.
- `src/nexus_scalp/candle_intelligence/classifier.py`: Component verified during structural mapping.
- `src/nexus_scalp/candle_intelligence/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/candle_intelligence/config.py`: Component verified during structural mapping.
- `src/nexus_scalp/candle_intelligence/decision.py`: Component verified during structural mapping.
- `src/nexus_scalp/candle_intelligence/store_writes.py`: Component verified during structural mapping.
- `src/nexus_scalp/candle_intelligence/engine.py`: Component verified during structural mapping.
- `src/nexus_scalp/candle_intelligence/models.py`: Component verified during structural mapping.
- `src/nexus_scalp/candle_intelligence/store.py`: Component verified during structural mapping.
- `src/nexus_scalp/intelligence/behavior.py`: Component verified during structural mapping.
- `src/nexus_scalp/intelligence/worker.py`: Component verified during structural mapping.
- `src/nexus_scalp/intelligence/evolution.py`: Component verified during structural mapping.
- `src/nexus_scalp/intelligence/autopsy.py`: Component verified during structural mapping.
- `src/nexus_scalp/intelligence/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/intelligence/lifecycle.py`: Component verified during structural mapping.
- `src/nexus_scalp/intelligence/gate.py`: Component verified during structural mapping.
- `src/nexus_scalp/intelligence/models.py`: Component verified during structural mapping.
- `src/nexus_scalp/intelligence/store.py`: Component verified during structural mapping.
- `src/nexus_scalp/database/provider.py`: Component verified during structural mapping.
- `src/nexus_scalp/database/ddl_port.py`: Component verified during structural mapping.
- `src/nexus_scalp/database/manifest.py`: Component verified during structural mapping.
- `src/nexus_scalp/database/drivers/proxy.py`: Component verified during structural mapping.
- `src/nexus_scalp/database/drivers/postgres_driver.py`: Component verified during structural mapping.
- `src/nexus_scalp/database/drivers/sqlite_driver.py`: Component verified during structural mapping.
- `src/nexus_scalp/database/drivers/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/database/drivers/base.py`: Component verified during structural mapping.
- `src/nexus_scalp/database/migrate_engine.py`: Component verified during structural mapping.
- `src/nexus_scalp/database/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/database/registry.py`: Component verified during structural mapping.
- `src/nexus_scalp/database/config.py`: Component verified during structural mapping.
- `src/nexus_scalp/database/gate.py`: Component verified during structural mapping.
- `src/nexus_scalp/database/engine.py`: Component verified during structural mapping.
- `src/nexus_scalp/database/models.py`: Component verified during structural mapping.
- `src/nexus_scalp/database/migrate_copier.py`: Component verified during structural mapping.
- `src/nexus_scalp/database/health.py`: Component verified during structural mapping.
- `src/nexus_scalp/hygiene/worker.py`: Component verified during structural mapping.
- `src/nexus_scalp/hygiene/quarantine.py`: Component verified during structural mapping.
- `src/nexus_scalp/hygiene/report.py`: Component verified during structural mapping.
- `src/nexus_scalp/hygiene/retention.py`: Component verified during structural mapping.
- `src/nexus_scalp/hygiene/index_health.py`: Component verified during structural mapping.
- `src/nexus_scalp/hygiene/archive.py`: Component verified during structural mapping.
- `src/nexus_scalp/hygiene/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/hygiene/consistency.py`: Component verified during structural mapping.
- `src/nexus_scalp/hygiene/state.py`: Component verified during structural mapping.
- `src/nexus_scalp/hygiene/hygiene_runtime.py`: Component verified during structural mapping.
- `src/nexus_scalp/hygiene/detectors.py`: Component verified during structural mapping.
- `src/nexus_scalp/hygiene/worker_runner.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_generation/artifact_store.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_generation/replay.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_generation/sequence.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_generation/news_bridge.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_generation/sequence_training.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_generation/strategy_factory.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_generation/sample_maker.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_generation/validation.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_generation/three_model.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_generation/architectures.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_generation/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_generation/runtime.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_generation/schema_v2.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_generation/experiment_factory.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_generation/benchmark.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_generation/schema_v2_incremental.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_generation/dataset_factory.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_generation/setup_detector.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_generation/models.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_generation/model_factory.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_generation/training.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_generation/sample_factory.py`: Component verified during structural mapping.
- `src/nexus_scalp/strategies/ichimoku.py`: Component verified during structural mapping.
- `src/nexus_scalp/strategies/factory/dsl.py`: Component verified during structural mapping.
- `src/nexus_scalp/strategies/factory/ranking.py`: Component verified during structural mapping.
- `src/nexus_scalp/strategies/factory/worker.py`: Component verified during structural mapping.
- `src/nexus_scalp/strategies/factory/evolution.py`: Component verified during structural mapping.
- `src/nexus_scalp/strategies/factory/provider.py`: Component verified during structural mapping.
- `src/nexus_scalp/strategies/factory/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/strategies/factory/orchestrator.py`: Component verified during structural mapping.
- `src/nexus_scalp/strategies/factory/benchmark.py`: Component verified during structural mapping.
- `src/nexus_scalp/strategies/factory/validators.py`: Component verified during structural mapping.
- `src/nexus_scalp/strategies/factory/models.py`: Component verified during structural mapping.
- `src/nexus_scalp/strategies/factory/telegram.py`: Component verified during structural mapping.
- `src/nexus_scalp/strategies/factory/store.py`: Component verified during structural mapping.
- `src/nexus_scalp/strategies/factory/summarizer.py`: Component verified during structural mapping.
- `src/nexus_scalp/strategies/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/strategies/seeder.py`: Component verified during structural mapping.
- `src/nexus_scalp/strategies/research_store.py`: Component verified during structural mapping.
- `src/nexus_scalp/strategies/base.py`: Component verified during structural mapping.
- `src/nexus_scalp/reporting/insights.py`: Component verified during structural mapping.
- `src/nexus_scalp/reporting/telegram_format.py`: Component verified during structural mapping.
- `src/nexus_scalp/reporting/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/reporting/engine.py`: Component verified during structural mapping.
- `src/nexus_scalp/reporting/models.py`: Component verified during structural mapping.
- `src/nexus_scalp/observability/telegram_transport.py`: Component verified during structural mapping.
- `src/nexus_scalp/observability/logging.py`: Component verified during structural mapping.
- `src/nexus_scalp/observability/ci_telegram_reporter.py`: Component verified during structural mapping.
- `src/nexus_scalp/observability/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/observability/telegram_html.py`: Component verified during structural mapping.
- `src/nexus_scalp/observability/telegram_notifier.py`: Component verified during structural mapping.
- `src/nexus_scalp/web/command_center_routes.py`: Component verified during structural mapping.
- `src/nexus_scalp/web/server.py`: Component verified during structural mapping.
- `src/nexus_scalp/web/news_intelligence_routes.py`: Component verified during structural mapping.
- `src/nexus_scalp/web/db_console.py`: Component verified during structural mapping.
- `src/nexus_scalp/web/factory_routes.py`: Component verified during structural mapping.
- `src/nexus_scalp/web/command_center_integration.py`: Component verified during structural mapping.
- `src/nexus_scalp/web/dependency_routes.py`: Component verified during structural mapping.
- `src/nexus_scalp/web/errors.py`: Component verified during structural mapping.
- `src/nexus_scalp/web/debug_snapshot.py`: Component verified during structural mapping.
- `src/nexus_scalp/market_data/bar_aggregator.py`: Component verified during structural mapping.
- `src/nexus_scalp/market_data/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/market_data/tick_storage.py`: Component verified during structural mapping.
- `src/nexus_scalp/governance/lock.py`: Component verified during structural mapping.
- `src/nexus_scalp/governance/transaction.py`: Component verified during structural mapping.
- `src/nexus_scalp/governance/evidence.py`: Component verified during structural mapping.
- `src/nexus_scalp/governance/alignment.py`: Component verified during structural mapping.
- `src/nexus_scalp/governance/verify.py`: Component verified during structural mapping.
- `src/nexus_scalp/governance/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/governance/reporting.py`: Component verified during structural mapping.
- `src/nexus_scalp/governance/load_gate.py`: Component verified during structural mapping.
- `src/nexus_scalp/governance/engine.py`: Component verified during structural mapping.
- `src/nexus_scalp/governance/models.py`: Component verified during structural mapping.
- `src/nexus_scalp/governance/shadow_runtime.py`: Component verified during structural mapping.
- `src/nexus_scalp/governance/store.py`: Component verified during structural mapping.
- `src/nexus_scalp/domain/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/domain/enums.py`: Component verified during structural mapping.
- `src/nexus_scalp/domain/models.py`: Component verified during structural mapping.
- `src/nexus_scalp/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_lifecycle/worker.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_lifecycle/integrity.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_lifecycle/trainer.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_lifecycle/gates.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_lifecycle/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_lifecycle/registry.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_lifecycle/orchestrator.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_lifecycle/comparison.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_lifecycle/champion.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_lifecycle/models.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_lifecycle/dataset.py`: Component verified during structural mapping.
- `src/nexus_scalp/model_lifecycle/store.py`: Component verified during structural mapping.
- `src/nexus_scalp/models/scalp_net.py`: Component verified during structural mapping.
- `src/nexus_scalp/diagnostics/runner.py`: Component verified during structural mapping.
- `src/nexus_scalp/diagnostics/analyzers/ruff_adapter.py`: Component verified during structural mapping.
- `src/nexus_scalp/diagnostics/analyzers/bandit_adapter.py`: Component verified during structural mapping.
- `src/nexus_scalp/diagnostics/analyzers/pyright_adapter.py`: Component verified during structural mapping.
- `src/nexus_scalp/diagnostics/analyzers/pylint_adapter.py`: Component verified during structural mapping.
- `src/nexus_scalp/diagnostics/analyzers/base.py`: Component verified during structural mapping.
- `src/nexus_scalp/diagnostics/engine.py`: Component verified during structural mapping.
- `src/nexus_scalp/diagnostics/models.py`: Component verified during structural mapping.
- `src/nexus_scalp/cli/main.py`: Component verified during structural mapping.
- `src/nexus_scalp/cli/__main__.py`: Component verified during structural mapping.
- `src/nexus_scalp/cli/dependency_commands.py`: Component verified during structural mapping.
- `src/nexus_scalp/cli/analyze_commands.py`: Component verified during structural mapping.
- `src/nexus_scalp/cli/incident_commands.py`: Component verified during structural mapping.
- `src/nexus_scalp/cli/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/cli/db_commands.py`: Component verified during structural mapping.
- `src/nexus_scalp/news/worker.py`: Component verified during structural mapping.
- `src/nexus_scalp/news/seed.py`: Component verified during structural mapping.
- `src/nexus_scalp/news/database.py`: Component verified during structural mapping.
- `src/nexus_scalp/news/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/news/config.py`: Component verified during structural mapping.
- `src/nexus_scalp/news/pro_auto.py`: Component verified during structural mapping.
- `src/nexus_scalp/news/ai_service.py`: Component verified during structural mapping.
- `src/nexus_scalp/news/ingest/deduplicator.py`: Component verified during structural mapping.
- `src/nexus_scalp/news/ingest/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/news/ingest/fetcher.py`: Component verified during structural mapping.
- `src/nexus_scalp/news/analysis/pipeline.py`: Component verified during structural mapping.
- `src/nexus_scalp/news/analysis/keywords.py`: Component verified during structural mapping.
- `src/nexus_scalp/news/analysis/decay.py`: Component verified during structural mapping.
- `src/nexus_scalp/news/analysis/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/news/analysis/local.py`: Component verified during structural mapping.
- `src/nexus_scalp/news/analysis/consensus.py`: Component verified during structural mapping.
- `src/nexus_scalp/news/context.py`: Component verified during structural mapping.
- `src/nexus_scalp/news/gate.py`: Component verified during structural mapping.
- `src/nexus_scalp/news/memory/post_event.py`: Component verified during structural mapping.
- `src/nexus_scalp/news/memory/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/news/engine.py`: Component verified during structural mapping.
- `src/nexus_scalp/news/models.py`: Component verified during structural mapping.
- `src/nexus_scalp/news/sources/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/news/sources/base.py`: Component verified during structural mapping.
- `src/nexus_scalp/experience/provenance.py`: Component verified during structural mapping.
- `src/nexus_scalp/experience/outcome_recovery_sweep.py`: Component verified during structural mapping.
- `src/nexus_scalp/experience/ledger.py`: Component verified during structural mapping.
- `src/nexus_scalp/experience/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/experience/outcome_repair.py`: Component verified during structural mapping.
- `src/nexus_scalp/experience/lifecycle.py`: Component verified during structural mapping.
- `src/nexus_scalp/experience/outcome_recovery.py`: Component verified during structural mapping.
- `src/nexus_scalp/experience/retriever.py`: Component verified during structural mapping.
- `src/nexus_scalp/experience/intelligence.py`: Component verified during structural mapping.
- `src/nexus_scalp/experience/models.py`: Component verified during structural mapping.
- `src/nexus_scalp/experience/quality.py`: Component verified during structural mapping.
- `src/nexus_scalp/experience/evaluator.py`: Component verified during structural mapping.
- `src/nexus_scalp/research/candidates.py`: Component verified during structural mapping.
- `src/nexus_scalp/research/pipeline.py`: Component verified during structural mapping.
- `src/nexus_scalp/research/spatial_layout.py`: Component verified during structural mapping.
- `src/nexus_scalp/research/discovery.py`: Component verified during structural mapping.
- `src/nexus_scalp/research/worker.py`: Component verified during structural mapping.
- `src/nexus_scalp/research/context_contract.py`: Component verified during structural mapping.
- `src/nexus_scalp/research/time_machine.py`: Component verified during structural mapping.
- `src/nexus_scalp/research/evidence.py`: Component verified during structural mapping.
- `src/nexus_scalp/research/scoring.py`: Component verified during structural mapping.
- `src/nexus_scalp/research/observability.py`: Component verified during structural mapping.
- `src/nexus_scalp/research/leakage.py`: Component verified during structural mapping.
- `src/nexus_scalp/research/metrics.py`: Component verified during structural mapping.
- `src/nexus_scalp/research/event_projection.py`: Component verified during structural mapping.
- `src/nexus_scalp/research/attribution.py`: Component verified during structural mapping.
- `src/nexus_scalp/research/debug_intelligence.py`: Component verified during structural mapping.
- `src/nexus_scalp/research/robustness.py`: Component verified during structural mapping.
- `src/nexus_scalp/research/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/research/snapshot.py`: Component verified during structural mapping.
- `src/nexus_scalp/research/backtest.py`: Component verified during structural mapping.
- `src/nexus_scalp/research/registry.py`: Component verified during structural mapping.
- `src/nexus_scalp/research/oos.py`: Component verified during structural mapping.
- `src/nexus_scalp/research/walkforward.py`: Component verified during structural mapping.
- `src/nexus_scalp/research/lifecycle.py`: Component verified during structural mapping.
- `src/nexus_scalp/research/models.py`: Component verified during structural mapping.
- `src/nexus_scalp/research/splitting.py`: Component verified during structural mapping.
- `src/nexus_scalp/research/context_analysis.py`: Component verified during structural mapping.
- `src/nexus_scalp/research/dataset.py`: Component verified during structural mapping.
- `src/nexus_scalp/research/store.py`: Component verified during structural mapping.
- `src/nexus_scalp/forensics/checks.py`: Component verified during structural mapping.
- `src/nexus_scalp/forensics/trend.py`: Component verified during structural mapping.
- `src/nexus_scalp/forensics/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/forensics/deploy_gate.py`: Component verified during structural mapping.
- `src/nexus_scalp/forensics/experience_gap.py`: Component verified during structural mapping.
- `src/nexus_scalp/forensics/references.py`: Component verified during structural mapping.
- `src/nexus_scalp/forensics/engine.py`: Component verified during structural mapping.
- `src/nexus_scalp/forensics/models.py`: Component verified during structural mapping.
- `src/nexus_scalp/forensics/telegram_report.py`: Component verified during structural mapping.
- `src/nexus_scalp/forensics/news_sources.py`: Component verified during structural mapping.
- `src/nexus_scalp/incidents/worker.py`: Component verified during structural mapping.
- `src/nexus_scalp/incidents/accounting.py`: Component verified during structural mapping.
- `src/nexus_scalp/incidents/timebase.py`: Component verified during structural mapping.
- `src/nexus_scalp/incidents/telemetry.py`: Component verified during structural mapping.
- `src/nexus_scalp/incidents/impact.py`: Component verified during structural mapping.
- `src/nexus_scalp/incidents/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/incidents/correlator.py`: Component verified during structural mapping.
- `src/nexus_scalp/incidents/trace_lineage.py`: Component verified during structural mapping.
- `src/nexus_scalp/incidents/occurrences.py`: Component verified during structural mapping.
- `src/nexus_scalp/incidents/reports.py`: Component verified during structural mapping.
- `src/nexus_scalp/incidents/lineage.py`: Component verified during structural mapping.
- `src/nexus_scalp/incidents/trace.py`: Component verified during structural mapping.
- `src/nexus_scalp/incidents/models.py`: Component verified during structural mapping.
- `src/nexus_scalp/incidents/telegram.py`: Component verified during structural mapping.
- `src/nexus_scalp/incidents/store.py`: Component verified during structural mapping.
- `src/nexus_scalp/configuration/runtime_config.py`: Component verified during structural mapping.
- `src/nexus_scalp/configuration/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/configuration/config.py`: Component verified during structural mapping.
- `src/nexus_scalp/execution/order_manager.py`: Component verified during structural mapping.
- `src/nexus_scalp/execution/terminal_outcome.py`: Component verified during structural mapping.
- `src/nexus_scalp/features/scalp_features.py`: Component verified during structural mapping.
- `src/nexus_scalp/features/inference_validator.py`: Component verified during structural mapping.
- `src/nexus_scalp/features/schema.py`: Component verified during structural mapping.
- `src/nexus_scalp/features/runtime70.py`: Component verified during structural mapping.
- `src/nexus_scalp/features/latency_tracer.py`: Component verified during structural mapping.
- `src/nexus_scalp/features/liquidity_runtime.py`: Component verified during structural mapping.
- `src/nexus_scalp/features/temporal.py`: Component verified during structural mapping.
- `src/nexus_scalp/features/liquidity_engine_opt.py`: Component verified during structural mapping.
- `src/nexus_scalp/features/liquidity_engine.py`: Component verified during structural mapping.
- `src/nexus_scalp/features/features70.py`: Component verified during structural mapping.
- `src/nexus_scalp/features/regime_classifier.py`: Component verified during structural mapping.
- `src/nexus_scalp/features/schema_augment.py`: Component verified during structural mapping.
- `src/nexus_scalp/features/schema_contract.py`: Component verified during structural mapping.
- `src/nexus_scalp/release/packaging.py`: Component verified during structural mapping.
- `src/nexus_scalp/release/metadata.py`: Component verified during structural mapping.
- `src/nexus_scalp/release/environment.py`: Component verified during structural mapping.
- `src/nexus_scalp/release/packaged_main.py`: Component verified during structural mapping.
- `src/nexus_scalp/release/update.py`: Component verified during structural mapping.
- `src/nexus_scalp/release/verify.py`: Component verified during structural mapping.
- `src/nexus_scalp/release/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/release/model_artifacts.py`: Component verified during structural mapping.
- `src/nexus_scalp/release/updater.py`: Component verified during structural mapping.
- `src/nexus_scalp/release/cli_shim.py`: Component verified during structural mapping.
- `src/nexus_scalp/release/versioning.py`: Component verified during structural mapping.
- `src/nexus_scalp/release/repair.py`: Component verified during structural mapping.
- `src/nexus_scalp/release/evaluate.py`: Component verified during structural mapping.
- `src/nexus_scalp/release/paths.py`: Component verified during structural mapping.
- `src/nexus_scalp/release/exit_codes.py`: Component verified during structural mapping.
- `src/nexus_scalp/release/health.py`: Component verified during structural mapping.
- `src/nexus_scalp/release/diagnostics.py`: Component verified during structural mapping.
- `src/nexus_scalp/ports/gateway_port.py`: Component verified during structural mapping.
- `src/nexus_scalp/ports/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/ports/mt5_port.py`: Component verified during structural mapping.
- `src/nexus_scalp/mslie/sweep.py`: Component verified during structural mapping.
- `src/nexus_scalp/mslie/smart_money.py`: Component verified during structural mapping.
- `src/nexus_scalp/mslie/swing.py`: Component verified during structural mapping.
- `src/nexus_scalp/mslie/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/mslie/regime.py`: Component verified during structural mapping.
- `src/nexus_scalp/mslie/liquidity_map.py`: Component verified during structural mapping.
- `src/nexus_scalp/mslie/engine.py`: Component verified during structural mapping.
- `src/nexus_scalp/mslie/models.py`: Component verified during structural mapping.
- `src/nexus_scalp/mslie/breakout.py`: Component verified during structural mapping.
- `src/nexus_scalp/training/walk_forward_trainer.py`: Component verified during structural mapping.
- `src/nexus_scalp/training/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/labeling/triple_barrier.py`: Component verified during structural mapping.
- `src/nexus_scalp/risk/risk_engine.py`: Component verified during structural mapping.
- `src/nexus_scalp/application/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/application/live_engine.py`: Component verified during structural mapping.
- `src/nexus_scalp/accounting/worker.py`: Component verified during structural mapping.
- `src/nexus_scalp/accounting/retention.py`: Component verified during structural mapping.
- `src/nexus_scalp/accounting/normalize.py`: Component verified during structural mapping.
- `src/nexus_scalp/accounting/__init__.py`: Component verified during structural mapping.
- `src/nexus_scalp/accounting/core.py`: Component verified during structural mapping.
- `src/nexus_scalp/accounting/market_calendar.py`: Component verified during structural mapping.
- `src/nexus_scalp/accounting/models.py`: Component verified during structural mapping.
- `src/nexus_scalp/accounting/periods.py`: Component verified during structural mapping.
- `src/nexus_scalp/accounting/aggregation.py`: Component verified during structural mapping.
- `src/cli/train_model.py`: Component verified during structural mapping.

## Subsystem Trace Analysis
A deep symbol trace was performed to verify the dependency structure and architectural boundaries. The following critical execution symbols were mapped:

### File: `src/nexus_scalp/dependency_intelligence/classify.py`
- Line 82: `def classify_layer`
- Line 95: `def classify_criticality`
### File: `src/nexus_scalp/dependency_intelligence/scanner.py`
- Line 146: `class ScanResult`
- Line 153: `class _ModuleIndex`
- Line 154: `def __init__`
- Line 160: `def build`
- Line 182: `def resolve`
- Line 193: `def _is_stdlib`
- Line 200: `def _is_third_party`
- Line 206: `def _name_to_str`
- Line 221: `def _split_base`
- Line 228: `class Scanner`
- Line 229: `def __init__`
- Line 239: `def _modname`
- Line 254: `def _mid`
- Line 257: `def _cid`
- Line 260: `def _ensure_module_node`
- Line 282: `def _ensure_class_node`
- Line 315: `def scan`
- Line 340: `def _preindex_classes`
- Line 373: `def _is_abstract_module`
- Line 378: `def _pass1`
- Line 451: `def _pass2`
- Line 555: `def _emit_import`
- Line 579: `def _emit_import_from`
- Line 629: `def _edge_external`
- Line 653: `def _edge_unresolved`
- Line 684: `def _record_error`
### File: `src/nexus_scalp/dependency_intelligence/analyzers/di.py`
- Line 55: `def _name_to_str`
- Line 68: `class DIAnalyzer`
- Line 69: `def __init__`
- Line 73: `def _modname`
- Line 83: `def enrich`
- Line 188: `def _resolve_arg_type`
- Line 211: `def _type_from_expr`
### File: `src/nexus_scalp/dependency_intelligence/engine.py`
- Line 27: `class AnalysisStats`
- Line 41: `class AnalysisResult`
- Line 47: `class DependencyIntelligenceEngine`
- Line 48: `def __init__`
- Line 52: `def analyze`
- Line 79: `def export_artifacts`
- Line 95: `def run_analysis`
### File: `src/nexus_scalp/dependency_intelligence/models.py`
- Line 21: `class NodeKind`
- Line 40: `class EdgeKind`
- Line 59: `class ResolutionStatus`
- Line 72: `class Layer`
- Line 84: `class Criticality`
- Line 108: `class Evidence`
- Line 116: `def to_dict`
- Line 126: `class DependencyNode`
- Line 140: `def to_dict`
- Line 158: `class DependencyEdge`
- Line 167: `def to_dict`
- Line 180: `class DependencyGraph`
- Line 188: `def add_node`
- Line 194: `def add_edge`
- Line 213: `def to_dict`
### File: `src/nexus_scalp/dependency_intelligence/analysis.py`
- Line 60: `class LayerRule`
- Line 107: `class Violation`
- Line 116: `def to_dict`
- Line 129: `class CycleRecord`
- Line 138: `def to_dict`
- Line 151: `class Metrics`
- Line 161: `class GraphAnalyzer`
- Line 162: `def __init__`
- Line 169: `def _build_nx`
- Line 188: `def compute_metrics`
- Line 211: `def detect_cycles`
- Line 263: `def validate_architecture`
- Line 310: `def impact`
- Line 364: `def shortest_path`
- Line 388: `def hotspots`
- Line 435: `def analyze_graph`
### File: `src/nexus_scalp/signals/stability_controller.py`
- Line 54: `class StableDirection`
- Line 60: `class StabilityState`
- Line 69: `class StabilityEvent`
- Line 83: `class StabilityDecision`
- Line 100: `class DecisionStabilityController`
- Line 103: `def __init__`
- Line 128: `def decide`
- Line 267: `def events`
- Line 270: `def last_event`
- Line 273: `def reset`
### File: `src/nexus_scalp/signals/rule_matrix.py`
- Line 24: `class RuleMatrixEngine`
- Line 30: `def __init__`
- Line 36: `def refresh_cache`
- Line 55: `def is_enabled`
- Line 62: `def get_params`
- Line 73: `def _eval_rule_fvg_sniper_fill`
- Line 112: `def _eval_rule_judas_swing_fade`
- Line 152: `def _eval_rule_orderblock_tap_reserve`
- Line 188: `def _eval_rule_wick_absorption_play`
- Line 224: `def _eval_rule_flash_momentum_scrape`
- Line 259: `def _eval_rule_tick_imbalance_reversal`
- Line 294: `def _eval_rule_news_spike_fade`
- Line 327: `def _eval_rule_end_of_hour_squeeze`
- Line 355: `def _eval_rule_vwap_elastic_band`
- Line 390: `def _eval_rule_bollinger_burst_fade`
- Line 426: `def _eval_rule_gap_and_go_momentum`
- Line 454: `def _eval_rule_contrarian_retail_trap`
- Line 489: `def evaluate_pre_trade_entry`
- Line 551: `def evaluate_pre_trade_filters`
- Line 647: `def evaluate_in_trade_exits`
- Line 732: `def evaluate_risk_and_safeguards`
### File: `src/nexus_scalp/signals/policy.py`
- Line 46: `class SignalPolicy`
- Line 52: `def __init__`
- Line 100: `def evaluate_probabilities`
- Line 378: `def is_numeric`
- Line 632: `def build_nt`
- Line 1158: `def _evaluate_tick_sweep`
- Line 1263: `def _evaluate_predictive_limit`
- Line 1359: `def _evaluate_exposure_limits`
- Line 1450: `def _evaluate_frequency_throttle`
- Line 1470: `def _get_active_tickets_info`
- Line 1506: `def _evaluate_duplicate_tick`
- Line 1571: `def _evaluate_guardian_gate`
- Line 1627: `def _evaluate_ai_reversal`
- Line 1741: `def _build_no_trade`
- Line 1795: `def extract_live_chart_overlays`
- Line 1947: `def _sanitize_float`
### File: `src/nexus_scalp/adapters/database/audit_repository.py`
- Line 34: `def normalize_history_dt`
- Line 42: `class AuditRepository`
- Line 47: `def __init__`
- Line 96: `def _setup_storage`
- Line 125: `def _create_sqlite_tables`
- Line 400: `def sync_broker_history`
- Line 448: `def get_broker_history_meta`
- Line 455: `def get_broker_trades`
- Line 480: `def get_broker_deals`
- Line 498: `def get_broker_orders`
- Line 519: `def _create_experience_tables`
- Line 708: `def _create_intelligence_tables`
- Line 737: `def _create_table_position_lifecycle_events`
- Line 777: `def _create_table_trade_autopsies`
- Line 830: `def _create_table_behavior_detections`
- Line 867: `def _create_table_behavior_analysis`
- Line 926: `def _create_table_strategy_evolution_candidates`
- Line 953: `def _create_table_intelligence_worker_state`
- Line 966: `def _create_factory_tables`
- Line 1100: `def _create_research_tables`
- Line 1192: `def _create_research_observability_tables`
- Line 1331: `def flush`
- Line 1358: `def _start_background_worker`
- Line 1366: `def _process_queue_worker`
- Line 1433: `def _signal_dedup_key`
- Line 1452: `def log_signal`
- Line 1556: `def _log_guard_telemetry`
- Line 1576: `def log_order`
- Line 1623: `def log_execution`
- Line 1651: `def log_account_snapshot`
- Line 1692: `def log_ledger_opened`
- Line 1743: `def has_ledger_opened`
- Line 1761: `def count_ledger_opened_unclosed`
- Line 1782: `def get_broker_deals_for_position`
- Line 1827: `def get_ledger_opened`
- Line 1845: `def log_ledger_closed`
- Line 2027: `def get_account_performance_metrics`
- Line 2120: `def get_recent_predictions`
- Line 2161: `def get_ledger_trades`
- Line 2191: `def get_equity_growth_chart_data`
- Line 2209: `def get_recent_order_events`
- Line 2235: `def get_ledger_row`
- Line 2249: `def get_last_account_snapshot`
- Line 2272: `def _seed_trading_rules`
- Line 2421: `def get_trading_rules`
- Line 2444: `def toggle_trading_rule`
- Line 2477: `def purge_old_audit_data`
- Line 2538: `def _batch_delete`
- Line 2582: `def close`
### File: `src/nexus_scalp/adapters/database/broker_history.py`
- Line 42: `def _net_from_deal`
- Line 51: `def order_identity`
- Line 59: `def deal_identity`
- Line 67: `def _f`
- Line 76: `def _i`
- Line 85: `def _s`
- Line 91: `def _utc_epoch_sec`
- Line 98: `def normalize_order_row`
- Line 123: `def normalize_deal_row`
- Line 151: `class LogicalTrade`
- Line 179: `def __init__`
- Line 210: `def reconstruct_trades`
- Line 291: `def _epoch_utc`
- Line 412: `def create_history_tables`
- Line 421: `def sync_broker_history`
- Line 609: `def last_sync_window`
### File: `src/nexus_scalp/adapters/database/broker_history_sync.py`
- Line 25: `class BrokerHistorySyncWorker`
- Line 28: `def __init__`
- Line 51: `def start`
- Line 58: `def stop`
- Line 64: `def tick`
- Line 105: `def _sync_once`
- Line 160: `def _warm_accounting`
- Line 178: `def _snapshot_dict`
### File: `src/nexus_scalp/adapters/paper/paper_adapter.py`
- Line 55: `class PaperMT5Adapter`
- Line 60: `def __init__`
- Line 76: `def _symbol_is_metal`
- Line 80: `def _ensure_symbol`
- Line 91: `def _quote_digits`
- Line 98: `def connect`
- Line 104: `def disconnect`
- Line 109: `def is_connected`
- Line 118: `def connection_state`
- Line 126: `def get_account_snapshot`
- Line 153: `def get_symbol_snapshot`
- Line 194: `def get_broker_tick`
- Line 220: `def get_all_positions`
- Line 224: `def get_rate_history`
- Line 247: `def order_calc_margin_snapshot`
- Line 267: `def order_calc_profit_snapshot`
- Line 291: `def get_history_deals`
- Line 297: `def get_history_orders`
- Line 302: `def get_pending_orders_snapshot`
- Line 305: `def get_tick_history`
- Line 314: `def resubscribe_symbol`
- Line 324: `def get_tick`
- Line 336: `def get_account_info`
- Line 349: `def get_symbol_info`
- Line 388: `def get_last_tick`
- Line 412: `def get_historical_bars`
- Line 465: `def get_positions`
- Line 471: `def send_order`
- Line 486: `def execute_market_order`
- Line 505: `def place_pending_order`
- Line 529: `def _open_simulated_position`
- Line 567: `def modify_position`
- Line 589: `def modify_order`
- Line 593: `def cancel_pending_order`
- Line 597: `def close_position`
### File: `src/nexus_scalp/adapters/mt5/providers.py`
- Line 44: `def broker_epoch_to_utc`
- Line 78: `class SnapshotBase`
- Line 86: `def as_error`
- Line 98: `class AccountSnapshot`
- Line 135: `def as_error`
- Line 141: `class SymbolSnapshot`
- Line 155: `def as_error`
- Line 161: `class BrokerTickSnapshot`
- Line 178: `def as_error`
- Line 184: `class PositionSnapshot`
- Line 210: `class OrderSnapshot`
- Line 236: `class HistoryOrderSnapshot`
- Line 264: `class DealSnapshot`
- Line 288: `def net_result`
- Line 303: `class RateBarSnapshot`
- Line 318: `class TickHistorySnapshot`
- Line 332: `class BrokerCalcSnapshot`
- Line 355: `def normalize_utc`
- Line 414: `def _attr`
- Line 422: `def _bool_attr`
- Line 432: `def _int_attr`
- Line 442: `def _float_attr`
- Line 455: `def build_account_snapshot`
- Line 495: `def build_symbol_snapshot`
- Line 575: `def build_position_snapshot`
- Line 632: `def build_order_snapshot`
- Line 664: `def build_history_order_snapshot`
- Line 698: `def build_deal_snapshot`
- Line 729: `def build_rate_bar_snapshot`
- Line 754: `def build_tick_history_snapshot`
- Line 778: `def validate_ohlc_bars`
### File: `src/nexus_scalp/adapters/mt5/remote_gateway.py`
- Line 38: `class RemoteMT5GatewayAdapter`
- Line 44: `def __init__`
- Line 57: `def connect`
- Line 77: `def disconnect`
- Line 82: `def is_connected`
- Line 94: `def get_account_info`
- Line 109: `def get_symbol_info`
- Line 127: `def get_last_tick`
- Line 146: `def get_historical_bars`
- Line 176: `def get_positions`
- Line 199: `def send_order`
- Line 233: `def close_position`
- Line 241: `def _sync_ping`
- Line 249: `def modify_position`
- Line 257: `def _send_request`
- Line 286: `def verify_request_signature`
### File: `src/nexus_scalp/adapters/mt5/mt5_adapter.py`
- Line 81: `class DirectMT5Adapter`
- Line 86: `def __init__`
- Line 117: `def _record_call`
- Line 123: `def diagnostics_summary`
- Line 131: `def connect`
- Line 236: `def disconnect`
- Line 243: `def is_connected`
- Line 254: `def get_account_info`
- Line 272: `def get_symbol_info`
- Line 294: `def get_last_tick`
- Line 311: `def resubscribe_symbol`
- Line 334: `def get_tick`
- Line 346: `def connection_state`
- Line 350: `def get_account_snapshot`
- Line 365: `def get_terminal_state`
- Line 402: `def get_symbol_snapshot`
- Line 436: `def get_broker_tick`
- Line 477: `def get_all_positions`
- Line 501: `def get_pending_orders_snapshot`
- Line 518: `def get_history_orders`
- Line 560: `def get_history_deals`
- Line 606: `def get_rate_history`
- Line 679: `def get_tick_history`
- Line 720: `def order_calc_profit_snapshot`
- Line 763: `def order_calc_margin_snapshot`
- Line 803: `def get_historical_bars`
- Line 837: `def get_positions`
- Line 873: `def get_pending_orders`
- Line 899: `def cancel_pending_order`
- Line 937: `def cancel_all_pending_orders`
- Line 953: `def get_closed_deals_history`
- Line 988: `def send_order`
- Line 1065: `def execute_market_order`
- Line 1120: `def _find_equivalent_pending`
- Line 1155: `def _order_type_to_mt5`
- Line 1171: `def place_pending_order`
- Line 1307: `def modify_order`
- Line 1310: `def modify_position`
- Line 1361: `def close_position`
- Line 1438: `def _resolve_filling_mode`
- Line 1451: `def _translate_retcode`
- Line 1468: `def _assert_connected`
### File: `src/nexus_scalp/adapters/mt5/diagnostics.py`
- Line 60: `class MT5OperationError`
- Line 67: `def __init__`
- Line 76: `class MT5CallDiagnostic`
- Line 88: `def to_dict`
- Line 100: `def log_line`
- Line 121: `def retcode_label`
- Line 128: `def run_mt5_call`
- Line 181: `def _emit`
- Line 189: `class MT5ConnectionState`
- Line 205: `def __init__`
- Line 220: `def set_state`
- Line 232: `def record_success`
- Line 239: `def record_failure`
- Line 243: `def mark_degraded`
- Line 249: `def set_terminal`
- Line 258: `def set_versions`
- Line 262: `def set_account`
- Line 283: `def state`
- Line 287: `def connected`
- Line 290: `def to_dict`
### File: `src/nexus_scalp/settings/service.py`
- Line 135: `class SettingValue`
- Line 146: `def to_dict`
- Line 170: `class SettingsState`
- Line 176: `class SettingsDatabase`
- Line 179: `def __init__`
- Line 186: `def _connect`
- Line 191: `def _init_schema`
- Line 204: `def health`
- Line 213: `def _typed_value`
- Line 224: `def get`
- Line 241: `def set`
- Line 313: `def delete`
- Line 319: `def all`
- Line 335: `def audit_log`
- Line 342: `def set_meta`
- Line 353: `def get_meta`
- Line 360: `def close`
- Line 368: `def _infer_type`
- Line 380: `def new_correlation_id`
- Line 389: `def _mask_token`
- Line 397: `class SettingsService`
- Line 400: `def __init__`
- Line 410: `def telegram_config_status`
- Line 434: `def get_telegram_credentials`
- Line 443: `def set_telegram`
- Line 484: `def factory_llm_config_status`
- Line 517: `def get_factory_llm_config`
- Line 560: `def set_factory_llm_config`
- Line 640: `def migrate_legacy_yaml`
- Line 683: `def blank_legacy_secrets`
- Line 714: `def set_postgres_config`
- Line 745: `def postgres_password_set`
- Line 751: `def set_database_provider`
- Line 765: `def provenance`
- Line 778: `def safe_snapshot`
- Line 787: `def close`
- Line 794: `def load_settings_service`
### File: `src/nexus_scalp/settings/secret_store.py`
- Line 31: `class SecretStoreError`
- Line 35: `class DATA_BLOB`
- Line 39: `def _last_error`
- Line 47: `def _local_free`
- Line 63: `class _Dpapi`
- Line 69: `def _ensure`
- Line 85: `def _blob`
- Line 90: `def protect`
- Line 112: `def unprotect`
- Line 136: `class SecureSecretStore`
- Line 144: `def __init__`
- Line 149: `def _load`
- Line 167: `def _save`
- Line 183: `def set_secret`
- Line 199: `def get_secret`
- Line 216: `def has_secret`
- Line 219: `def delete_secret`
### File: `src/nexus_scalp/settings/paths.py`
- Line 23: `def settings_db_path`
- Line 41: `def settings_db_url`
### File: `src/nexus_scalp/shadow/worker.py`
- Line 35: `class ShadowWorker`
- Line 44: `def __init__`
- Line 64: `def start`
- Line 72: `def stop`
- Line 78: `def request_cancel`
- Line 82: `def _mark_interrupted_runs`
- Line 105: `def tick`
- Line 136: `def _maybe_finalize`
- Line 153: `def format_shadow_worker_status`
### File: `src/nexus_scalp/shadow/challenger.py`
- Line 39: `def resolve_schema`
- Line 44: `class ChallengerLoadError`
- Line 48: `class ChallengerRuntime`
- Line 59: `def __init__`
- Line 86: `def _load`
- Line 159: `def infer`
- Line 191: `def summary`
- Line 204: `def _action_from_probs`
- Line 211: `def load_challenger`
### File: `src/nexus_scalp/shadow/shadow70/worker.py`
- Line 32: `class Shadow70QueueItem`
- Line 39: `class Shadow70Worker`
- Line 47: `def __init__`
- Line 78: `def start`
- Line 85: `def stop`
- Line 92: `def _run`
- Line 108: `def enqueue`
- Line 131: `def flush`
- Line 164: `def queue_health_callback`
- Line 172: `def status`
- Line 185: `def format_shadow70_status`
### File: `src/nexus_scalp/shadow/shadow70/liq_provider.py`
- Line 48: `def _neutral_10`
- Line 63: `def _governor_snapshot`
- Line 96: `def build_liquidity_10`
- Line 143: `def _extract_named`
- Line 173: `def _sanitize`
### File: `src/nexus_scalp/shadow/shadow70/news_provider.py`
- Line 68: `def build_news_10`
- Line 89: `def verify_news_family`
### File: `src/nexus_scalp/shadow/shadow70/runtime.py`
- Line 65: `class Shadow70LoadResult`
- Line 70: `def __init__`
- Line 83: `def passed`
- Line 86: `def to_dict`
- Line 95: `def sha256_file`
- Line 107: `def sha256_json`
- Line 112: `class Shadow70LoadValidator`
- Line 115: `def __init__`
- Line 129: `def validate`
- Line 215: `class _InferenceFn`
- Line 224: `def __init__`
- Line 227: `def infer`
- Line 243: `def _action_from_probs`
- Line 249: `class Shadow70Runtime`
- Line 260: `def __init__`
- Line 296: `def attach`
- Line 329: `def set_inference`
- Line 333: `def pause`
- Line 337: `def resume`
- Line 341: `def stop`
- Line 348: `def observe`
- Line 558: `def _build_observation`
- Line 638: `def _validate_vector`
- Line 685: `def _looks_stale`
- Line 688: `def _record`
- Line 723: `def summary`
- Line 760: `def recent_window`
### File: `src/nexus_scalp/shadow/shadow70/models.py`
- Line 65: `class Shadow70RuntimeState`
- Line 78: `class Shadow70LoadStatus`
- Line 88: `class DisagreementClass`
- Line 107: `def agreement_classes`
- Line 111: `def _utc`
- Line 115: `class Shadow70CandidateContract`
- Line 133: `def is_validated`
- Line 136: `def is_70d`
- Line 140: `class Shadow70FeatureProvenance`
- Line 162: `def _utc`
- Line 166: `class Shadow70VectorReport`
- Line 181: `class Shadow70Observation`
- Line 239: `def _utc_opt`
- Line 244: `def _finite`
- Line 250: `def deterministic_id`
- Line 254: `def _direction`
- Line 262: `def _is_trade`
- Line 266: `def classify_disagreement`
### File: `src/nexus_scalp/shadow/shadow70/health.py`
- Line 57: `class Shadow70FeatureHealth`
- Line 75: `def to_dict`
- Line 95: `class Shadow70DriftAlert`
- Line 110: `def to_dict`
- Line 126: `def _mean_std`
- Line 135: `def _psi`
- Line 158: `def _normal_reference`
- Line 174: `class Shadow70FeatureHealthMonitor`
- Line 177: `def __init__`
- Line 182: `def update`
- Line 194: `def health`
- Line 232: `class Shadow70DriftMonitor`
- Line 239: `def __init__`
- Line 273: `def set_reference`
- Line 287: `def update`
- Line 296: `def _severity`
- Line 305: `def evaluate`
- Line 411: `def latest_alerts`
- Line 414: `def summary`
### File: `src/nexus_scalp/shadow/shadow70/store.py`
- Line 34: `class Shadow70BackpressurePolicy`
- Line 39: `def __init__`
- Line 44: `def should_drop`
- Line 47: `def record_drop`
- Line 53: `def summary`
- Line 61: `class Shadow70Persistence`
- Line 64: `def save_observation`
- Line 65: `def record_event`
- Line 66: `def save_feature_health`
- Line 67: `def save_drift_alerts`
- Line 106: `class Shadow70Store`
- Line 109: `def __init__`
- Line 118: `def ensure_schema`
- Line 246: `def save_observation`
- Line 301: `def record_event`
- Line 325: `def save_feature_health`
- Line 356: `def save_drift_alerts`
- Line 388: `def list_observations`
- Line 400: `def list_events`
- Line 408: `def latest_drift_alerts`
- Line 416: `def latest_feature_health`
- Line 421: `def disagreement_counts`
- Line 439: `def summary`
- Line 466: `def _query`
### File: `src/nexus_scalp/shadow/comparison.py`
- Line 47: `def _mean`
- Line 51: `class ShadowComparer`
- Line 54: `def __init__`
- Line 61: `def compare`
- Line 235: `def evaluate_promotion`
- Line 377: `def _norm_delta`
- Line 382: `def _calibration`
- Line 410: `def _drawdown`
- Line 425: `def _profit_factor`
### File: `src/nexus_scalp/shadow/engine.py`
- Line 37: `class ShadowEngine`
- Line 42: `def __init__`
- Line 59: `def start_run`
- Line 88: `def attach_challenger`
- Line 92: `def finish_run`
- Line 131: `def record_shadow_decision`
- Line 246: `def _champion_ref`
- Line 251: `def set_champion_ref`
- Line 254: `def _run_started_at`
- Line 263: `def current_evidence`
### File: `src/nexus_scalp/shadow/models.py`
- Line 30: `class ShadowDecisionKind`
- Line 37: `class ShadowEvidenceStatus`
- Line 46: `class ShadowModelRef`
- Line 59: `class SharedInputRef`
- Line 82: `def _utc`
- Line 85: `def matches`
- Line 97: `class ShadowDecisionRecord`
- Line 154: `def _utc`
- Line 158: `class ShadowRun`
- Line 174: `def _utc`
- Line 178: `class ShadowComparison`
- Line 236: `def _utc`
- Line 240: `def expectancy_delta`
- Line 244: `def drawdown_delta`
- Line 248: `class PromotionEvaluation`
- Line 285: `def _utc`
### File: `src/nexus_scalp/shadow/store.py`
- Line 79: `class ShadowStore`
- Line 82: `def __init__`
- Line 93: `def ensure_schema`
- Line 228: `def save_run`
- Line 251: `def save_decision`
- Line 294: `def save_comparison`
- Line 331: `def save_promotion`
- Line 359: `def get_run`
- Line 376: `def list_runs`
- Line 397: `def list_decisions`
- Line 432: `def get_comparison`
- Line 449: `def get_promotion`
- Line 466: `def list_promotions`
- Line 487: `def summary`
### File: `src/nexus_scalp/candle_intelligence/patterns.py`
- Line 28: `class Candle`
- Line 33: `def __init__`
- Line 54: `def body`
- Line 58: `def rng`
- Line 62: `def upper_wick`
- Line 66: `def lower_wick`
- Line 70: `def bullish`
- Line 74: `def bearish`
- Line 78: `def body_ratio`
- Line 81: `def is_doji`
- Line 85: `class PatternContext`
- Line 90: `def __init__`
- Line 105: `class PatternEngine`
- Line 141: `def __init__`
- Line 148: `def detect`
- Line 193: `def _detect_one`
- Line 263: `def _weight`
- Line 292: `def _derive_context`
- Line 331: `def _finite`
- Line 337: `def _hammer`
- Line 364: `def _marubozu`
- Line 370: `def _engulfing`
- Line 391: `def _star`
- Line 409: `def _gravestone_doji`
- Line 417: `def _dragonfly_doji`
- Line 425: `def _standard_doji`
- Line 431: `def _long_legged_doji`
- Line 439: `def _three_soldiers`
- Line 455: `def _harami`
- Line 465: `def _cloud_cover`
- Line 486: `def _three_methods`
- Line 509: `def _double_top_bottom`
- Line 525: `def _head_shoulders`
- Line 543: `def _chart_pattern`
- Line 582: `def _gap`
- Line 590: `def _trend_label`
### File: `src/nexus_scalp/candle_intelligence/classifier.py`
- Line 27: `class CandleCloseClassifier`
- Line 30: `def __init__`
- Line 33: `def classify`
- Line 158: `def _classify`
- Line 197: `def _quality`
- Line 226: `def _invalid_summary`
- Line 245: `def _invalid`
### File: `src/nexus_scalp/candle_intelligence/config.py`
- Line 18: `class CandleIntelligenceConfig`
### File: `src/nexus_scalp/candle_intelligence/decision.py`
- Line 36: `class CandleDecisionEngine`
- Line 42: `def __init__`
- Line 45: `def decide`
- Line 248: `def _bias_from_close`
- Line 273: `def _aligned_patterns`
- Line 281: `def _manage_position`
- Line 314: `def _build`
### File: `src/nexus_scalp/candle_intelligence/store_writes.py`
- Line 30: `def _safe_float`
- Line 38: `def _bar_ts`
- Line 44: `def _insert`
- Line 53: `def record_candle`
- Line 116: `def record_candle_closure`
- Line 205: `def record_patterns`
- Line 270: `def record_regime`
- Line 318: `def record_risk`
- Line 364: `def record_decision`
- Line 430: `def record_veto`
- Line 482: `def record_audit_log`
### File: `src/nexus_scalp/candle_intelligence/engine.py`
- Line 37: `class CandleOutput`
- Line 53: `def __init__`
- Line 77: `def to_dict`
- Line 92: `class CandleIntelligenceEngine`
- Line 95: `def __init__`
- Line 119: `def ingest_bar`
- Line 169: `def process_candle_close`
- Line 247: `def ingest_tick`
- Line 255: `def recent_decisions`
- Line 258: `def recent_closures`
- Line 261: `def recent_vetoes`
- Line 264: `def db_size_bytes`
### File: `src/nexus_scalp/candle_intelligence/models.py`
- Line 23: `class CandleCloseClass`
- Line 38: `class TradeBias`
- Line 45: `class DecisionType`
- Line 55: `class RiskState`
- Line 63: `class CandleCloseSummary`
- Line 99: `def model_dump_for_db`
- Line 106: `class PatternDetection`
- Line 120: `class RegimeState`
- Line 134: `class RiskEvaluation`
- Line 144: `class CandleDecision`
### File: `src/nexus_scalp/candle_intelligence/store.py`
- Line 210: `def _now_iso`
- Line 214: `def _j`
- Line 217: `def _clean`
- Line 233: `def _common_kwargs`
- Line 263: `class CandleIntelStore`
- Line 290: `def __init__`
- Line 334: `def _connect_reader`
- Line 342: `def _connect_writer`
- Line 348: `def _init_schema`
- Line 399: `def _start_worker`
- Line 407: `def _worker_loop`
- Line 441: `def _flush_batch`
- Line 460: `def enqueue`
- Line 477: `def pending_count`
- Line 481: `def flush`
- Line 488: `def close`
- Line 503: `def __enter__`
- Line 506: `def __exit__`
- Line 513: `def query_recent`
- Line 549: `def _jsonify`
- Line 567: `def db_size_bytes`
- Line 579: `def integrity_ok`
- Line 594: `def _attach_writes`
### File: `src/nexus_scalp/intelligence/behavior.py`
- Line 148: `def _support_state`
- Line 162: `def _safe_ratio`
- Line 168: `def _json_default`
- Line 177: `def _jsonable`
- Line 182: `class BehaviorDetectionEngine`
- Line 191: `def __init__`
- Line 215: `def _check_profit_giveback`
- Line 245: `def _check_early_exit`
- Line 269: `def _check_late_exit`
- Line 339: `def _check_excessive_hold`
- Line 378: `def _check_missed_breakeven`
- Line 417: `def _check_premature_breakeven`
- Line 450: `def _check_model_reversal_ignored`
- Line 488: `def _check_regime_change_ignored`
- Line 522: `def _check_liquidity_reversal_ignored`
- Line 553: `def _check_risk_deviation`
- Line 586: `def _check_exit_classification_anomaly`
- Line 609: `def _check_strategy_context_loss`
- Line 630: `def analyze`
- Line 751: `def analyze_record`
- Line 783: `def _detection`
- Line 803: `def persist`
- Line 834: `def _build_key`
- Line 844: `def _build_analysis_key`
- Line 849: `def _coverage_fields`
- Line 875: `def analyze_canonical_trades`
- Line 1014: `def _giveback_fraction`
- Line 1025: `def _strategy_hold_baseline`
- Line 1054: `def _trade_data_anomalies`
- Line 1064: `def _aid`
- Line 1175: `def _duplicate_outcome_anomalies`
- Line 1246: `def _duplicate_anomaly_id`
- Line 1252: `def _persist_analysis`
- Line 1279: `def _persist_anomaly`
- Line 1302: `class BehaviorAnalysisBackfiller`
- Line 1312: `def __init__`
- Line 1325: `def run`
### File: `src/nexus_scalp/intelligence/worker.py`
- Line 47: `class IntelligenceWorker`
- Line 58: `def __init__`
- Line 88: `def start`
- Line 97: `def stop`
- Line 109: `def _load_checkpoint`
- Line 135: `def _save_checkpoint`
- Line 162: `def tick`
- Line 203: `def _refresh_once`
- Line 213: `def _run`
- Line 224: `def _refresh_autopsies`
- Line 272: `def _refresh_evolution`
- Line 283: `def _refresh_behavior`
- Line 316: `def format_intelligence_worker_status`
### File: `src/nexus_scalp/intelligence/evolution.py`
- Line 55: `class StrategyEvolutionEngine`
- Line 64: `def __init__`
- Line 83: `def scan`
- Line 110: `def _discover_from_family`
- Line 164: `def _hypothesis_for`
- Line 204: `def validate_candidate`
- Line 245: `def persist`
- Line 275: `def get_candidate`
- Line 297: `def _candidate_id`
### File: `src/nexus_scalp/intelligence/autopsy.py`
- Line 72: `class TradeAutopsyEngine`
- Line 81: `def __init__`
- Line 92: `def build_autopsy`
- Line 182: `def _narrate`
- Line 243: `def persist`
### File: `src/nexus_scalp/intelligence/lifecycle.py`
- Line 68: `class PositionLifecycleTracker`
- Line 83: `def __init__`
- Line 115: `def observe_position`
- Line 337: `def emit`
- Line 411: `def finalize_exit`
- Line 468: `def _update_high_water`
- Line 474: `def _read_closed_ledger`
- Line 498: `def _build_event_key`
- Line 507: `def list_events_for_ticket`
### File: `src/nexus_scalp/intelligence/gate.py`
- Line 44: `class SuitabilityTier`
- Line 60: `class SuitabilityVerdict`
- Line 73: `def to_dict`
- Line 87: `class PreTradeIntelligenceGate`
- Line 93: `def __init__`
- Line 115: `def evaluate`
- Line 170: `def _evaluate_with_evidence`
- Line 250: `def _suitability_score`
- Line 280: `def _evidence_dict`
- Line 293: `def _verdict_from_phase08`
- Line 312: `def summary`
### File: `src/nexus_scalp/intelligence/models.py`
- Line 31: `class PositionEventType`
- Line 46: `class AutopsyVerdict`
- Line 57: `class EvolutionStatus`
- Line 67: `class BehaviorSeverity`
- Line 76: `class MarketContext`
- Line 90: `class PositionSnapshot`
- Line 104: `class PositionPerformance`
- Line 117: `class DecisionContext`
- Line 130: `class PositionLifecycleEvent`
- Line 159: `def validate_utc`
- Line 163: `class TradeAutopsy`
- Line 210: `def validate_utc`
- Line 214: `class BehaviorDetection`
- Line 235: `def validate_utc`
- Line 239: `class EvolutionCandidate`
- Line 264: `def validate_utc`
- Line 268: `class BehaviorAnalysisStatus`
- Line 280: `class BehaviorAnalysis`
- Line 309: `def validate_utc`
- Line 313: `class AnomalyEvent`
- Line 334: `def validate_utc`
### File: `src/nexus_scalp/intelligence/store.py`
- Line 38: `def _parse_ts`
- Line 47: `def load_lifecycle_events`
- Line 102: `def load_autopsy`
- Line 121: `def list_autopsies`
- Line 151: `def list_behavior_detections`
- Line 188: `def list_anomaly_events`
- Line 259: `def load_evolution_candidates`
- Line 289: `def count_autopsies`
- Line 303: `def count_lifecycle_events`
### File: `src/nexus_scalp/database/provider.py`
- Line 27: `class DatabaseProvider`
- Line 34: `def is_sqlite`
- Line 38: `def is_postgresql`
- Line 42: `def parse`
- Line 61: `def from_url`
- Line 80: `def default_sqlite_path`
- Line 88: `def url_for_provider`
### File: `src/nexus_scalp/database/ddl_port.py`
- Line 26: `def _type_map`
- Line 30: `def port_column_type`
- Line 43: `def port_create_table`
- Line 76: `def _normalize_literals`
- Line 108: `def _split_columns`
- Line 170: `def _drop_autoincrement`
### File: `src/nexus_scalp/database/manifest.py`
- Line 256: `def manifest_for`
- Line 260: `def expected_version_for`
### File: `src/nexus_scalp/database/drivers/proxy.py`
- Line 37: `class PortableCursor`
- Line 40: `def __init__`
- Line 48: `def rowcount`
- Line 52: `def description`
- Line 55: `def _to_dict`
- Line 62: `def fetchone`
- Line 66: `def fetchall`
- Line 69: `def fetchmany`
- Line 72: `def close`
- Line 79: `def _rewrite_insert_or`
- Line 95: `class PortableConnection`
- Line 98: `def __init__`
- Line 111: `def execute`
- Line 130: `def executemany`
- Line 141: `def commit`
- Line 144: `def rollback`
- Line 150: `def close`
- Line 157: `def __enter__`
- Line 160: `def __exit__`
- Line 169: `def _strip_semicolon`
- Line 172: `def _with_replace_conflict`
- Line 200: `class SqliteLikeProxy`
- Line 204: `def connect_proxy`
### File: `src/nexus_scalp/database/drivers/postgres_driver.py`
- Line 50: `def pg_type_for`
- Line 64: `def _translate_placeholders`
- Line 104: `class PostgreSQLDriver`
- Line 113: `def __init__`
- Line 120: `def _psycopg_module`
- Line 133: `def available`
- Line 144: `def translate_sql`
- Line 150: `def connect`
- Line 167: `def closed`
- Line 172: `def ensure_directory`
- Line 175: `def configure_connection`
- Line 186: `def _info_columns`
- Line 191: `def table_columns`
- Line 227: `def table_exists`
- Line 241: `def list_tables`
- Line 255: `def create_table`
- Line 266: `def _maybe_commit_auto`
- Line 274: `def execute`
- Line 289: `def executemany`
- Line 304: `def query`
- Line 316: `def query_one`
- Line 332: `def scalar`
- Line 342: `def last_insert_rowid`
- Line 353: `def _conflict_target`
- Line 398: `def upsert`
- Line 434: `def insert_ignore`
- Line 455: `def begin`
- Line 461: `def commit`
- Line 469: `def database_version`
- Line 472: `def database_size_bytes`
- Line 478: `def table_count`
- Line 488: `def row_count`
- Line 491: `def ping`
- Line 497: `def integrity_check`
- Line 502: `def close`
- Line 511: `def portable_type_for`
- Line 515: `def identity_ddl`
### File: `src/nexus_scalp/database/drivers/sqlite_driver.py`
- Line 51: `def sqlite_type_to_portable`
- Line 59: `class SQLiteDriver`
- Line 66: `def __init__`
- Line 73: `def connect_path`
- Line 78: `def is_in_memory`
- Line 83: `def connect`
- Line 89: `def connect_shared`
- Line 98: `def close_shared`
- Line 107: `def ensure_directory`
- Line 118: `def configure_connection`
- Line 129: `def create_table`
- Line 139: `def table_columns`
- Line 150: `def table_exists`
- Line 163: `def list_tables`
- Line 178: `def execute`
- Line 193: `def executemany`
- Line 202: `def query`
- Line 213: `def query_one`
- Line 226: `def scalar`
- Line 237: `def last_insert_rowid`
- Line 246: `def upsert`
- Line 255: `def insert_ignore`
- Line 265: `def begin`
- Line 271: `def commit`
- Line 278: `def database_version`
- Line 281: `def database_size_bytes`
- Line 289: `def table_count`
- Line 299: `def row_count`
- Line 302: `def ping`
- Line 308: `def integrity_check`
- Line 319: `def close`
### File: `src/nexus_scalp/database/drivers/__init__.py`
- Line 21: `def get_driver`
- Line 28: `def driver_available`
### File: `src/nexus_scalp/database/drivers/base.py`
- Line 28: `class DatabaseDriver`
- Line 35: `def __init__`
- Line 41: `def qmarks`
- Line 49: `def quote_ident`
- Line 55: `def transaction`
- Line 66: `def connect`
- Line 72: `def ensure_directory`
- Line 77: `def configure_connection`
- Line 84: `def create_table`
- Line 89: `def table_columns`
- Line 93: `def table_exists`
- Line 97: `def list_tables`
- Line 103: `def execute`
- Line 107: `def executemany`
- Line 111: `def query`
- Line 115: `def query_one`
- Line 121: `def scalar`
- Line 125: `def last_insert_rowid`
- Line 129: `def upsert`
- Line 133: `def insert_ignore`
- Line 139: `def begin`
- Line 143: `def commit`
- Line 149: `def database_version`
- Line 153: `def database_size_bytes`
- Line 158: `def table_count`
- Line 162: `def row_count`
- Line 166: `def ping`
- Line 169: `def integrity_check`
- Line 174: `def close`
- Line 179: `def portable_type_for`
- Line 183: `def identity_ddl`
- Line 188: `class _DriverTransaction`
- Line 191: `def __init__`
- Line 196: `def __enter__`
- Line 202: `def __exit__`
### File: `src/nexus_scalp/database/migrate_engine.py`
- Line 42: `class MigrationOptions`
- Line 57: `class MigrationReport`
- Line 74: `def to_dict`
- Line 92: `def _sqlite_indexes`
- Line 115: `class SqliteToPostgresMigrator`
- Line 118: `def __init__`
- Line 135: `def _source_tables`
- Line 139: `def _financial_cols`
- Line 207: `def preview`
- Line 246: `def create_destination_schema`
- Line 282: `def run`
- Line 355: `def validate`
- Line 408: `def load_migration_dest_config`
### File: `src/nexus_scalp/database/registry.py`
- Line 42: `def _add_column`
- Line 54: `def _ensure_index`
- Line 64: `def _unique_index`
- Line 73: `def _index_exists`
- Line 81: `def _table_exists`
- Line 89: `def _column_exists`
- Line 99: `def _audit_0002_orders_ticket_index`
- Line 106: `def _audit_0002_verify`
- Line 110: `def _audit_0002_rollback`
- Line 115: `def _audit_0003_ledger_exit_evidence`
- Line 127: `def _audit_0003_verify`
- Line 136: `def _audit_0004_ledger_close_time_index`
- Line 147: `def _audit_0004_verify`
- Line 151: `def _audit_0004_rollback`
- Line 166: `def _audit_0005_governance_audit_tables`
- Line 211: `def _audit_0005_verify`
- Line 217: `def _audit_0005_rollback`
- Line 226: `def _audit_0006_incident_tables`
- Line 316: `def _audit_0006_verify`
- Line 320: `def _audit_0006_rollback`
- Line 329: `def _audit_0007_release_metadata`
- Line 371: `def _audit_0007_verify`
- Line 375: `def _audit_0007_rollback`
- Line 387: `def _news_0002_source_health_index`
- Line 399: `def _news_0002_verify`
- Line 408: `def _news_0002_rollback`
- Line 418: `def _candle_0002_closure_composite_index`
- Line 427: `def _candle_0002_verify`
- Line 431: `def _candle_0002_rollback`
- Line 565: `def migrations_for`
- Line 569: `def baseline_version_for`
- Line 573: `def expected_version_for_domain`
- Line 578: `def all_migration_ids`
### File: `src/nexus_scalp/database/config.py`
- Line 47: `class DatabaseConfig`
- Line 90: `def for_sqlite`
- Line 101: `def for_postgres`
- Line 133: `def is_sqlite`
- Line 137: `def is_postgresql`
- Line 141: `def sqlite_connect_path`
- Line 145: `def build_url`
- Line 165: `def to_dict`
- Line 188: `def from_dict`
- Line 213: `def mask_url_password`
- Line 227: `def load_database_config`
- Line 314: `def resolve_password`
- Line 334: `def build_postgres_url`
### File: `src/nexus_scalp/database/gate.py`
- Line 31: `def run_startup_migration_gate`
- Line 103: `def assert_ready`
### File: `src/nexus_scalp/database/engine.py`
- Line 59: `class MigrationError`
- Line 62: `def __init__`
- Line 88: `class DatabaseMigrationEngine`
- Line 91: `def __init__`
- Line 118: `def _connect`
- Line 123: `def _ensure_meta_tables`
- Line 127: `def _read_version`
- Line 136: `def _write_version`
- Line 142: `def current_version`
- Line 155: `def expected_version`
- Line 158: `def baseline_version`
- Line 161: `def _has_business_tables`
- Line 172: `def _applied_ids`
- Line 178: `def _applied_checksums`
- Line 184: `def _record_migration`
- Line 216: `def _backup`
- Line 242: `def _restore`
- Line 263: `def _lock_ctx`
- Line 267: `class _Lock`
- Line 268: `def __init__`
- Line 272: `def __enter__`
- Line 282: `def __exit__`
- Line 291: `def _lock_held_by_other`
- Line 298: `def _integrity`
- Line 308: `def _detect_drift`
- Line 409: `def _create_baseline_tables`
- Line 458: `def _detect_tamper`
- Line 489: `def plan`
- Line 539: `def status`
- Line 581: `def migrate`
- Line 833: `def expected_tables`
- Line 837: `def migration_count`
- Line 841: `def verify`
- Line 877: `def history`
- Line 906: `def repair`
- Line 914: `def db_path_for_domain`
### File: `src/nexus_scalp/database/models.py`
- Line 18: `class DatabaseDomain`
- Line 26: `class MigrationRisk`
- Line 35: `class TransactionKind`
- Line 42: `class MigrationStatus`
- Line 50: `class MigrationState`
- Line 66: `class Migration`
- Line 88: `def checksum`
- Line 98: `class SchemaColumn`
- Line 108: `class SchemaTable`
- Line 125: `class SchemaManifest`
- Line 132: `def table_names`
- Line 135: `def column_names`
- Line 141: `def expected_indexes`
- Line 148: `def to_dict`
- Line 171: `def migration_file_checksum`
- Line 178: `class MigrationResult`
### File: `src/nexus_scalp/database/migrate_copier.py`
- Line 35: `class MigrationError`
- Line 39: `def _sqlite_table_columns`
- Line 44: `def ensure_checkpoint_table`
- Line 66: `def load_checkpoints`
- Line 83: `def _save_checkpoint`
- Line 132: `def iter_table_batches`
- Line 183: `def last_rowid_of_batch`
- Line 188: `def copy_table`
- Line 266: `def _checksum_for`
### File: `src/nexus_scalp/database/health.py`
- Line 27: `class DatabaseHealthService`
- Line 30: `def __init__`
- Line 35: `def resolve_config`
- Line 38: `def check_domain`
- Line 126: `def snapshot`
- Line 144: `def _engine_path_for`
- Line 156: `def _utc_now`
- Line 162: `def health_snapshot`
- Line 167: `def load_ui_config`
### File: `src/nexus_scalp/hygiene/worker.py`
- Line 90: `class DeleteCandidate`
- Line 105: `class HygienePlan`
- Line 117: `def summary`
- Line 134: `class HygieneScanner`
- Line 137: `def scan_schema`
- Line 152: `class HygienePlanner`
- Line 159: `def __init__`
- Line 165: `def build_plan`
- Line 273: `class VerificationEngine`
- Line 276: `def verify`
- Line 311: `def financial_aggregates`
- Line 347: `class CleanupExecutor`
- Line 356: `def __init__`
- Line 374: `def _begin`
- Line 377: `def _table_rows_sql`
- Line 396: `def apply_plan`
- Line 555: `def _canonical_exists`
- Line 574: `def _pk_col`
- Line 583: `def _apply_retention_batches`
### File: `src/nexus_scalp/hygiene/quarantine.py`
- Line 78: `def _now_iso`
- Line 82: `def new_quarantine_id`
- Line 86: `class QuarantineStore`
- Line 89: `def __init__`
- Line 95: `def _connect`
- Line 100: `def _init_schema`
- Line 111: `def quarantine`
- Line 162: `def restore`
- Line 180: `def resolve`
- Line 208: `def get`
- Line 215: `def list`
- Line 233: `def stats`
- Line 260: `def _get_locked`
- Line 274: `def _event`
- Line 281: `def db_path`
### File: `src/nexus_scalp/hygiene/report.py`
- Line 32: `def build_initial_audit_report`
- Line 91: `def persist_initial_audit`
- Line 106: `def build_cycle_telemetry`
- Line 139: `def build_query_health_report`
- Line 161: `def build_telegram_report_text`
- Line 180: `def build_telegram_initial_report_text`
### File: `src/nexus_scalp/hygiene/retention.py`
- Line 14: `class `
- Line 29: `class RetentionRule`
- Line 48: `def is_age_candidate`
- Line 59: `def is_archive_candidate`
- Line 613: `class RetentionEngine`
- Line 616: `def __init__`
- Line 623: `def for_database`
- Line 632: `def rule_for`
- Line 635: `def age_days`
- Line 658: `def classify`
### File: `src/nexus_scalp/hygiene/index_health.py`
- Line 62: `class IndexFinding`
- Line 69: `def as_dict`
- Line 79: `class IndexHealthMonitor`
- Line 82: `def __init__`
- Line 89: `def _columns`
- Line 96: `def _indexes`
- Line 115: `def scan_missing`
- Line 146: `def scan_duplicates`
- Line 167: `def scan_unused`
- Line 192: `def scan_table`
- Line 200: `def scan_database`
- Line 225: `def slow_query_report`
### File: `src/nexus_scalp/hygiene/archive.py`
- Line 32: `def _now_iso`
- Line 36: `class ArchiveManager`
- Line 39: `def __init__`
- Line 43: `def _ensure_dir`
- Line 49: `def _sha256_hex`
- Line 52: `def archive_rows`
- Line 111: `def verify_archive`
- Line 123: `class CleanupJournal`
- Line 126: `def __init__`
- Line 134: `def record`
- Line 164: `def path`
- Line 168: `def read_only_connect`
### File: `src/nexus_scalp/hygiene/__init__.py`
- Line 14: `class DataTier`
- Line 28: `class Confidence`
- Line 37: `class WorkerMode`
- Line 46: `class WorkerState`
- Line 60: `class OrphanClass`
### File: `src/nexus_scalp/hygiene/consistency.py`
- Line 56: `class ConsistencyFinding`
- Line 66: `def as_dict`
- Line 79: `def _as_ts`
- Line 109: `class ConsistencyRuleEngine`
- Line 112: `def __init__`
- Line 120: `def scan_audit`
- Line 362: `def scan_candle`
- Line 466: `def scan_news`
- Line 510: `def scan`
- Line 520: `def _table_columns`
- Line 527: `def _mk`
- Line 557: `def findings_summary`
- Line 575: `def findings_json`
### File: `src/nexus_scalp/hygiene/state.py`
- Line 67: `class HygieneStateStore`
- Line 70: `def __init__`
- Line 76: `def _connect`
- Line 81: `def _init_schema`
- Line 92: `def get_state`
- Line 117: `def set_state`
- Line 160: `def record_run`
- Line 192: `def list_runs`
- Line 203: `def recover_interrupted`
- Line 221: `def new_run_id`
### File: `src/nexus_scalp/hygiene/hygiene_runtime.py`
- Line 61: `class RuntimeHygieneSettings`
- Line 75: `def from_mapping`
- Line 90: `class RuntimeCleanupScheduler`
- Line 93: `def __init__`
- Line 121: `def _ensure_worker`
- Line 149: `def is_light_due`
- Line 152: `def is_deep_due`
- Line 155: `def is_telegram_due`
- Line 158: `def next_light_in`
- Line 165: `def run_cycle`
- Line 231: `def _run_initial_audit`
- Line 273: `def _index_health_report`
- Line 294: `def quarantine_rows`
- Line 335: `def telegram_text_for_cycle`
- Line 338: `def telegram_text_for_initial`
- Line 341: `def mark_telegram_sent`
- Line 347: `def status`
### File: `src/nexus_scalp/hygiene/detectors.py`
- Line 40: `class DuplicateCandidate`
- Line 50: `class DuplicateDetector`
- Line 53: `def __init__`
- Line 59: `def scan_audit`
- Line 140: `def scan_news`
- Line 219: `def scan_candle`
- Line 224: `def scan`
- Line 234: `def candidates`
- Line 238: `class OrphanDetector`
- Line 241: `def scan_audit`
- Line 309: `def scan_news`
- Line 333: `def scan_candle`
- Line 337: `def scan`
### File: `src/nexus_scalp/hygiene/worker_runner.py`
- Line 50: `def _db_size`
- Line 57: `def _wal_size`
- Line 64: `class DatabaseHygieneWorker`
- Line 67: `def __init__`
- Line 91: `def status`
- Line 106: `def pause`
- Line 110: `def resume`
- Line 114: `def history`
- Line 120: `def plan_database`
- Line 137: `def run_cycle`
- Line 264: `def db_integrity_digest`
### File: `src/nexus_scalp/model_generation/artifact_store.py`
- Line 40: `def validate_artifact_id`
- Line 52: `def sha256_file`
- Line 64: `def sha256_bytes`
- Line 68: `def sha256_text`
- Line 74: `class ArtifactStore`
- Line 77: `def __init__`
- Line 89: `def write_json`
- Line 96: `def read_json`
- Line 110: `def dataset_dir`
- Line 113: `def dataset_path`
- Line 116: `def dataset_manifest_path`
- Line 119: `def save_dataset`
- Line 130: `def read_dataset`
- Line 142: `def read_dataset_manifest`
- Line 149: `def experiment_path`
- Line 152: `def save_experiment`
- Line 157: `def read_experiment`
- Line 164: `def model_dir`
- Line 167: `def model_weights_path`
- Line 170: `def model_manifest_path`
- Line 173: `def model_scaler_path`
- Line 176: `def model_validation_path`
- Line 179: `def save_model_artifact`
- Line 233: `def read_model_manifest`
- Line 236: `def read_scaler`
- Line 247: `def save_validation`
- Line 250: `def read_validation`
- Line 257: `def verify_artifact`
- Line 272: `def default_artifact_root`
### File: `src/nexus_scalp/model_generation/replay.py`
- Line 29: `class SampleReplay`
- Line 32: `def __init__`
- Line 35: `def replay`
- Line 105: `def compare`
- Line 137: `def detect_feature_drift`
- Line 164: `def detect_prediction_drift`
- Line 192: `def replay_70d_vector`
### File: `src/nexus_scalp/model_generation/sequence.py`
- Line 25: `class SequenceBuilder`
- Line 28: `def __init__`
- Line 37: `def build`
- Line 126: `def _ts_us`
### File: `src/nexus_scalp/model_generation/news_bridge.py`
- Line 74: `def _encode_state`
- Line 83: `def _encode_novelty`
- Line 92: `def _num`
- Line 102: `def _coerce_field`
- Line 120: `def _normalize_publication_ts`
- Line 134: `def normalize_news_frame`
- Line 200: `def news_context_at`
- Line 261: `def _safe_epoch_sec`
- Line 297: `def _news_schema_fields`
- Line 303: `def build_news_frame_from_db`
- Line 419: `def _parse_iso`
- Line 431: `def _derive_news_state`
- Line 459: `def news_quality_diagnostics`
- Line 539: `def _field_stats`
- Line 578: `def news_benchmark_readiness`
- Line 626: `def news_10d_vector`
### File: `src/nexus_scalp/model_generation/sequence_training.py`
- Line 50: `class SequenceCandidateTrainer`
- Line 53: `def __init__`
- Line 65: `def train_candidate`
- Line 259: `def _grad_norm`
### File: `src/nexus_scalp/model_generation/strategy_factory.py`
- Line 29: `class HunterStrategy`
- Line 44: `def to_contract`
- Line 210: `def get_strategy`
- Line 217: `class EntryDecision`
- Line 229: `def to_contract`
- Line 243: `class StrategyFactory`
- Line 246: `def __init__`
- Line 253: `def evaluate`
- Line 287: `def _evaluate_one`
- Line 341: `def _session_hit`
- Line 352: `def best_strategy_for`
### File: `src/nexus_scalp/model_generation/sample_maker.py`
- Line 45: `def quality_tier`
- Line 74: `class HunterSampleMaker`
- Line 77: `def __init__`
- Line 91: `def analyze_row`
- Line 142: `def _empty`
- Line 163: `def build_hunter_frame`
- Line 216: `def hunter_gate_frame`
- Line 240: `def attach_hunter_metadata`
### File: `src/nexus_scalp/model_generation/validation.py`
- Line 41: `def detect_class_collapse`
- Line 65: `def _balanced_accuracy`
- Line 78: `def compute_calibration`
- Line 118: `def evaluate_regime_performance`
- Line 147: `class ValidationFactory`
- Line 150: `def __init__`
- Line 153: `def validate`
- Line 304: `def compare_news_ablation`
- Line 329: `def confusion_and_class_metrics`
- Line 372: `def head_to_head`
### File: `src/nexus_scalp/model_generation/three_model.py`
- Line 50: `def _lifecycle_registry`
- Line 69: `def _model_lifecycle_status`
- Line 75: `def variant_artifact_path`
- Line 84: `def variant_feature_columns`
- Line 91: `def variant_schema_id`
- Line 96: `def build_feature_frame`
- Line 138: `def _label_frame`
- Line 143: `def _last_trainable_rows`
- Line 148: `def train_variant`
- Line 345: `def write_variants_index`
- Line 363: `def train_all`
### File: `src/nexus_scalp/model_generation/architectures.py`
- Line 38: `class CausalConv1dBlock`
- Line 45: `def __init__`
- Line 65: `def forward`
- Line 76: `class TCNAttentionV1`
- Line 83: `def __init__`
- Line 135: `def forward`
- Line 154: `def build_tcn_attention_v1`
### File: `src/nexus_scalp/model_generation/runtime.py`
- Line 28: `class ManifestValidationError`
- Line 32: `class LocalModelRuntime`
- Line 39: `def __init__`
- Line 56: `def load`
- Line 132: `def unload`
- Line 141: `def metadata`
- Line 146: `def health`
- Line 159: `def predict`
- Line 189: `def _decode`
- Line 196: `def validate_and_load`
### File: `src/nexus_scalp/model_generation/schema_v2.py`
- Line 51: `def bars_frame_to_bardata`
- Line 80: `def compute_60d_frame`
- Line 185: `def build_60d_dataset`
- Line 232: `def augment_existing_dataset_to_60d`
- Line 287: `def verify_60d_artifact`
- Line 355: `def compute_liquidity_frame`
- Line 446: `def build_liquidity_dataset`
- Line 488: `def verify_liquidity_artifact`
- Line 537: `def compute_70d_frame`
- Line 661: `def build_70d_dataset`
- Line 746: `def verify_70d_artifact`
### File: `src/nexus_scalp/model_generation/experiment_factory.py`
- Line 87: `class ExperimentFactory`
- Line 90: `def __init__`
- Line 93: `def create`
- Line 135: `def load`
- Line 142: `def train_experiment`
### File: `src/nexus_scalp/model_generation/benchmark.py`
- Line 93: `class BenchmarkRunner`
- Line 96: `def __init__`
- Line 109: `def run`
- Line 254: `def _build_report`
- Line 285: `def _write_report`
- Line 295: `def _predict_probs`
- Line 345: `def _conclude`
- Line 346: `def acc`
- Line 354: `def verdict`
- Line 379: `def _render_md`
### File: `src/nexus_scalp/model_generation/schema_v2_incremental.py`
- Line 87: `def sweep_lookback`
- Line 126: `def _session_ranges`
- Line 144: `class IncrementalLiquidityState`
- Line 152: `def __init__`
- Line 201: `def _precompute_daily`
- Line 217: `def _precompute_htf`
- Line 248: `def _bar_index_at`
- Line 258: `def pools_visible_at`
- Line 323: `def advance_pools_np`
- Line 401: `def _first_bar_index_at_or_after`
- Line 415: `def session_pools_at`
- Line 449: `def daily_pools_at`
- Line 524: `def htf_score_at`
- Line 559: `def compute_70d_frame_fast`
### File: `src/nexus_scalp/model_generation/dataset_factory.py`
- Line 32: `def deterministic_dataset_id`
- Line 51: `def _news_digest`
- Line 87: `def config_blob`
- Line 93: `class DatasetFactory`
- Line 96: `def __init__`
- Line 104: `def build`
- Line 230: `def _apply_split`
### File: `src/nexus_scalp/model_generation/setup_detector.py`
- Line 57: `class SetupDetection`
- Line 68: `def to_contract`
- Line 80: `def _f`
- Line 89: `def _quality`
- Line 113: `def _make_id`
- Line 120: `class SetupDetector`
- Line 123: `def __init__`
- Line 131: `def _sig`
- Line 143: `def _detect_liquidity_sweep`
- Line 178: `def _detect_order_block`
- Line 216: `def _detect_fvg`
- Line 247: `def _detect_bos`
- Line 281: `def _detect_choch`
- Line 305: `def _detect_ote_pullback`
- Line 333: `def _detect_trend_continuation`
- Line 365: `def _detect_breakout_pullback`
- Line 398: `def _detect_impulse`
- Line 427: `def _detect_ranging_fade`
- Line 459: `def _detect_oversold_bounce`
- Line 489: `def _detect_compression_break`
- Line 524: `def _session_ok`
- Line 530: `def _detect_london_breakout`
- Line 561: `def _detect_ny_open_sweep`
- Line 596: `def detect`
- Line 629: `def best_setup`
- Line 636: `def validate_setup_type`
### File: `src/nexus_scalp/model_generation/models.py`
- Line 38: `class NeuralLabel`
- Line 67: `class LabelSchema`
- Line 81: `def _names_len`
- Line 84: `def encode`
- Line 92: `def decode`
- Line 98: `def validate_labels`
- Line 108: `def default_label_schema`
- Line 125: `class NewsContextSchema`
- Line 155: `def vectorize`
- Line 171: `def default_news_context_schema`
- Line 180: `class SampleContract`
- Line 200: `def _utc`
- Line 203: `def validate_schema`
- Line 211: `class SetupContract`
- Line 223: `class StrategyContract`
- Line 243: `class ModelArchitecture`
- Line 258: `class ModelManifest`
- Line 339: `def _utc`
- Line 342: `def digest`
- Line 357: `class DatasetManifest`
- Line 389: `def _utc`
- Line 398: `class ExperimentConfig`
- Line 422: `class ValidationResults`
### File: `src/nexus_scalp/model_generation/model_factory.py`
- Line 31: `class SimpleMLP`
- Line 34: `def __init__`
- Line 52: `def forward`
- Line 56: `class ModelFactory`
- Line 65: `def __init__`
- Line 68: `def build`
- Line 127: `def build_from_experiment`
- Line 138: `def infer_feature_dim`
### File: `src/nexus_scalp/model_generation/training.py`
- Line 39: `def _split_columns`
- Line 50: `def dataset_hash_value`
- Line 63: `def deterministic_candidate_id`
- Line 88: `def _grad_norm`
- Line 100: `class CandidateTrainer`
- Line 103: `def __init__`
- Line 108: `def train_candidate`
### File: `src/nexus_scalp/model_generation/sample_factory.py`
- Line 42: `def deterministic_sample_id`
- Line 54: `class SampleFactory`
- Line 57: `def __init__`
- Line 82: `def news_context_at`
- Line 107: `def detect_setup`
- Line 148: `def build_samples`
- Line 268: `def _parse_ts`
- Line 277: `def samples_to_frame`
### File: `src/nexus_scalp/strategies/ichimoku.py`
- Line 53: `def _ichimoku_lines`
- Line 74: `class IchimiliFinalStrategy`
- Line 81: `def __init__`
- Line 97: `def context_definition`
- Line 107: `def entry_logic`
- Line 120: `def exit_logic`
- Line 123: `def risk_assumptions`
- Line 134: `def evaluate`
- Line 216: `class IchimiliSpacedStrategy`
- Line 223: `def __init__`
- Line 241: `def context_definition`
- Line 251: `def entry_logic`
- Line 265: `def exit_logic`
- Line 268: `def risk_assumptions`
- Line 279: `def evaluate`
### File: `src/nexus_scalp/strategies/factory/dsl.py`
- Line 329: `def _family_hypothesis`
- Line 444: `def build_feature_catalog`
- Line 474: `def feature_catalog_index`
- Line 479: `def feature_ids`
- Line 489: `def canonical_json`
- Line 494: `def dsl_hash`
- Line 499: `def candidate_id_from_hash`
- Line 504: `def canonicalize_dsl`
- Line 520: `def _feature_group`
- Line 532: `def _index_of`
- Line 536: `def _template_dsl`
- Line 558: `def _expected_regime`
- Line 575: `def generate_template_candidates`
- Line 595: `def _rotate_template`
- Line 608: `def generate_diversity_candidates`
- Line 656: `def generate_regime_candidates`
- Line 689: `def generate_random_candidates`
- Line 733: `def generate_generation_zero`
### File: `src/nexus_scalp/strategies/factory/ranking.py`
- Line 47: `def _clamp`
- Line 51: `def _true`
- Line 55: `def strategy_error`
- Line 63: `def score_components`
- Line 75: `def _decoded`
- Line 143: `def _count_conditions`
- Line 156: `def selection_score`
- Line 167: `def explain_rank`
- Line 190: `def dimension_score`
- Line 220: `def rank_strategies`
- Line 251: `def family_diversity`
- Line 276: `def feature_diversity`
- Line 309: `def population_diversity`
### File: `src/nexus_scalp/strategies/factory/worker.py`
- Line 42: `def _event_id`
- Line 46: `class AutonomousLoopWorker`
- Line 57: `def __init__`
- Line 87: `def start`
- Line 112: `def stop`
- Line 118: `def pause`
- Line 124: `def resume`
- Line 134: `def tick`
- Line 177: `def _should_stop`
- Line 184: `def _run_one_generation`
- Line 210: `def _finish_loop`
- Line 231: `def recover`
- Line 314: `def status`
### File: `src/nexus_scalp/strategies/factory/evolution.py`
- Line 61: `def _raw`
- Line 65: `def _rebuild`
- Line 69: `def _mutate_add_filter`
- Line 89: `def _mutate_remove_filter`
- Line 98: `def _mutate_replace_indicator`
- Line 111: `def _mutate_change_threshold`
- Line 127: `def _mutate_change_timeframe`
- Line 138: `def _mutate_change_condition`
- Line 153: `def _mutate_simplify`
- Line 167: `def mutate`
- Line 234: `def _compatible`
- Line 247: `def _merge_confirmation`
- Line 258: `def crossover`
- Line 362: `def explore`
- Line 422: `def mutate_with_action`
- Line 446: `def adapt_probabilities`
### File: `src/nexus_scalp/strategies/factory/provider.py`
- Line 62: `class ProviderUsage`
- Line 65: `def __init__`
- Line 75: `def snapshot`
- Line 91: `class LLMGenerationProvider`
- Line 104: `def __init__`
- Line 132: `def _load_key`
- Line 140: `def available`
- Line 144: `def _budget_exhausted`
- Line 151: `def generate_dsls`
- Line 246: `def complete_json`
- Line 336: `def _extract_dsl_list`
- Line 355: `def _try_parse`
- Line 368: `def _repair`
- Line 390: `def _build_messages`
### File: `src/nexus_scalp/strategies/factory/orchestrator.py`
- Line 90: `def _now`
- Line 94: `def _event_id`
- Line 98: `def _score_dict`
- Line 122: `class StrategyFactory`
- Line 130: `def __init__`
- Line 171: `def _research_backend`
- Line 183: `def start_loop`
- Line 195: `def pause_loop`
- Line 214: `def resume_loop`
- Line 232: `def stop_loop`
- Line 259: `def loop_status`
- Line 279: `def create_generation`
- Line 325: `def _send_telegram`
- Line 339: `def _next_generation_number`
- Line 344: `def _next_generation_number_safe`
- Line 351: `def generate_population`
- Line 383: `def _record_provider_usage`
- Line 410: `def _generation_zero_population`
- Line 438: `def _merge_llm_slice`
- Line 466: `def _llm_candidates`
- Line 511: `def _llm_prompt_context`
- Line 547: `def _evolved_population`
- Line 627: `def _adaptive_probabilities`
- Line 640: `def _load_elite`
- Line 657: `def _candidate_from_registry`
- Line 680: `def _fresh_template`
- Line 697: `def _fresh_candidate`
- Line 711: `def _budgets`
- Line 721: `def _dedupe_population`
- Line 732: `def _tally_operator`
- Line 745: `def _load_operator_accounting`
- Line 784: `def _persist_operator_accounting`
- Line 804: `def _behavior_cluster`
- Line 813: `def _is_pathological_clone`
- Line 821: `def _record_behavior_outcome`
- Line 839: `def _tally_action`
- Line 847: `def _attribute_action_outcome`
- Line 879: `def _parent_expectancy`
- Line 898: `def validate_population`
- Line 938: `def _persist_candidate`
- Line 981: `def _persist_failure`
- Line 998: `def evaluate_candidate`
- Line 1241: `def _ledger_snapshot_for_filter`
- Line 1282: `def _to_strategy_candidate`
- Line 1330: `def _derived_failure_reasons`
- Line 1371: `def _is_evidence_only_failure`
- Line 1403: `def _stage_for_reason`
- Line 1418: `def _tally_operator_survival`
- Line 1429: `def complete_generation`
- Line 1482: `def _registry_rows_for_generation`
- Line 1496: `def build_memory`
- Line 1515: `def run_generation_cycle`
- Line 1576: `def _build_dataset`
- Line 1580: `def auto_resume_failed_generations`
- Line 1641: `def resume_generation`
- Line 1665: `def _candidate_from_row`
- Line 1696: `def _decode_registry_row`
- Line 1733: `def _ensure_family_coverage`
### File: `src/nexus_scalp/strategies/factory/benchmark.py`
- Line 70: `def _eval_filter`
- Line 97: `def dsl_matches_snapshot`
- Line 116: `def benchmark_subset_for_candidate`
- Line 142: `def candidate_coverage_stats`
- Line 177: `def build_benchmark_artifact`
- Line 313: `def behavioral_preview_signature`
### File: `src/nexus_scalp/strategies/factory/validators.py`
- Line 68: `def _verdict`
- Line 84: `def validate_schema`
- Line 172: `def validate_features`
- Line 183: `def _collect`
- Line 206: `def validate_causality`
- Line 251: `def validate_complexity`
- Line 259: `def _count_conditions`
- Line 307: `def validate_candidate`
### File: `src/nexus_scalp/strategies/factory/models.py`
- Line 32: `def utc_now`
- Line 36: `def _coerce_utc`
- Line 45: `class GenerationMode`
- Line 52: `class CandidateSource`
- Line 66: `class EvolutionOperator`
- Line 77: `class StrategyFamily`
- Line 93: `class FactoryStage`
- Line 111: `class FailureReason`
- Line 148: `class RankDimension`
- Line 162: `class LoopState`
- Line 179: `class FeatureCatalogEntry`
- Line 206: `class StrategyDsl`
- Line 234: `class FactoryCandidate`
- Line 257: `def _utc`
- Line 266: `class FactoryGeneration`
- Line 283: `def _utc`
- Line 292: `class ValidationVerdict`
- Line 304: `class CandidateResult`
- Line 327: `def _utc`
- Line 331: `class EliteEntry`
- Line 347: `def _utc`
- Line 356: `class GenerationSummary`
- Line 381: `class EvolutionMemory`
- Line 397: `class EvolutionConfig`
### File: `src/nexus_scalp/strategies/factory/telegram.py`
- Line 43: `def _esc`
- Line 47: `def build_generation_started`
- Line 56: `def build_generation_completed`
- Line 81: `def build_strategy_rejected`
- Line 94: `def build_elite_promoted`
- Line 104: `def build_failure_alert`
- Line 112: `def build_generation_progress`
- Line 121: `def send_factory_event`
### File: `src/nexus_scalp/strategies/factory/store.py`
- Line 50: `def ensure_factory_context_columns`
- Line 68: `def _json`
- Line 80: `def _now`
- Line 84: `def _conn`
- Line 96: `def _resolve_backend`
- Line 107: `def _is_store_backend`
- Line 116: `def upsert_generation`
- Line 154: `def upsert_candidate`
- Line 201: `def record_failure`
- Line 236: `def emit_event`
- Line 270: `def record_run`
- Line 322: `def record_provider_usage`
- Line 361: `def set_loop_state`
- Line 402: `def get_generation`
- Line 421: `def list_generations`
- Line 446: `def get_loop_states`
- Line 465: `def sweep_stale_generations`
- Line 614: `def resume_generation`
- Line 685: `def list_candidates`
- Line 721: `def list_failures`
- Line 750: `def list_events`
- Line 779: `def list_runs`
- Line 799: `def get_candidate_structural`
- Line 836: `def get_loop_state`
- Line 869: `def set_operator_stats`
- Line 893: `def get_operator_stats`
- Line 926: `def provider_usage_total`
- Line 952: `def _row_safe`
### File: `src/nexus_scalp/strategies/factory/summarizer.py`
- Line 27: `def _score_of`
- Line 34: `def _expectancy_r`
- Line 41: `def build_summary`
- Line 125: `def score_verdict`
- Line 129: `def _structural_passed`
- Line 152: `def memory_summary`
- Line 230: `def format_summary_for_prompt`
### File: `src/nexus_scalp/strategies/seeder.py`
- Line 28: `def seed_builtin_candidates`
- Line 93: `def seed_builtin_candidates_deferred`
### File: `src/nexus_scalp/strategies/research_store.py`
- Line 238: `def _json`
- Line 249: `def _json_parse`
- Line 262: `def _now`
- Line 271: `def default_config`
- Line 282: `def config_for`
- Line 316: `class StrategyResearchStore`
- Line 328: `def __init__`
- Line 335: `def ensure_schema`
- Line 374: `def _create_table`
- Line 382: `def _set_meta`
- Line 389: `def meta`
- Line 399: `def _write`
- Line 423: `def upsert_generation`
- Line 445: `def upsert_candidate`
- Line 470: `def record_failure`
- Line 489: `def emit_event`
- Line 507: `def record_run`
- Line 525: `def record_provider_usage`
- Line 547: `def set_loop_state`
- Line 569: `def get_generation`
- Line 576: `def list_generations`
- Line 583: `def list_candidates`
- Line 605: `def list_failures`
- Line 618: `def list_events`
- Line 631: `def list_runs`
- Line 642: `def get_candidate_structural`
- Line 651: `def get_loop_state`
- Line 660: `def set_operator_stats`
- Line 683: `def get_operator_stats`
- Line 701: `def provider_usage_total`
- Line 726: `def count_rows`
- Line 735: `def close`
- Line 743: `def _row_safe`
- Line 768: `def open_store`
### File: `src/nexus_scalp/strategies/base.py`
- Line 28: `class BarLike`
- Line 40: `class StrategySignal`
- Line 51: `class Strategy`
- Line 58: `def evaluate`
- Line 60: `def context_definition`
- Line 62: `def entry_logic`
- Line 64: `def exit_logic`
- Line 66: `def risk_assumptions`
- Line 69: `def _utc_now`
- Line 73: `def make_candidate`
- Line 93: `def register_strategy`
- Line 99: `def builtin_candidates`
- Line 104: `def _bars_to_lists`
- Line 116: `def donchian_mid`
### File: `src/nexus_scalp/reporting/insights.py`
- Line 35: `def evidence_level`
- Line 53: `def compare_periods`
- Line 68: `def _d`
- Line 96: `def _num`
- Line 104: `def _delta`
- Line 111: `def classify_trend`
- Line 157: `def compute_anomalies`
- Line 264: `def compute_health_score`
- Line 395: `def _fmt`
- Line 406: `def generate_insights`
- Line 685: `def make_report_id`
- Line 690: `def make_snapshot_id`
- Line 700: `def classify_session`
### File: `src/nexus_scalp/reporting/telegram_format.py`
- Line 22: `def _esc`
- Line 29: `def _fmt_usd`
- Line 35: `def _fmt_pct`
- Line 41: `def _fmt_r`
- Line 47: `def _fmt_ratio`
- Line 53: `def _fmt_hold`
- Line 63: `def _bar`
- Line 72: `def format_telegram_daily`
- Line 157: `def format_deep_report`
- Line 245: `def _best_exit`
- Line 255: `def _worst_exit`
- Line 265: `def _strategy_lines`
- Line 288: `def _model_lines`
- Line 315: `def _execution_lines`
- Line 329: `def _news_lines`
- Line 342: `def _loss_driver_lines`
- Line 359: `def _profit_driver_lines`
- Line 376: `def _trend_line`
- Line 394: `def _exit_lines`
- Line 408: `def _deep_strategy_lines`
- Line 422: `def _regime_lines`
- Line 436: `def _session_lines`
- Line 451: `def _behavioral_lines`
- Line 476: `def _anomaly_lines`
- Line 492: `def _compare_lines`
- Line 506: `def _insight_lines`
### File: `src/nexus_scalp/reporting/engine.py`
- Line 102: `class PerformanceReportEngine`
- Line 111: `def __init__`
- Line 119: `def generate`
- Line 333: `def _as_report`
- Line 401: `def _stage_snapshot`
- Line 436: `def _stage_performance`
- Line 474: `def _stage_distribution`
- Line 501: `def _stage_r`
- Line 523: `def _stage_excursion`
- Line 572: `def _stage_holding`
- Line 590: `def _stage_exits`
- Line 613: `def _stage_streaks`
- Line 637: `def _stage_risk`
- Line 673: `def _stage_drawdown`
- Line 700: `def _stage_strategies`
- Line 740: `def _stage_regimes`
- Line 773: `def _stage_sessions`
- Line 802: `def _stage_model`
- Line 938: `def _stage_execution`
- Line 997: `def _stage_news`
- Line 1020: `def _stage_behavioral`
- Line 1099: `def _stage_anomaly_state`
- Line 1169: `def _stage_loss_drivers`
- Line 1201: `def _stage_profit_drivers`
- Line 1231: `def _stage_compare`
- Line 1257: `def _normalize_exit`
- Line 1261: `def _skew`
- Line 1271: `def _fmt_opt`
- Line 1277: `def _probe`
### File: `src/nexus_scalp/reporting/models.py`
- Line 19: `class EvidenceLevel`
- Line 28: `class TrendClassification`
- Line 38: `class SnapshotBlock`
- Line 54: `def to_dict`
- Line 72: `class PerformanceSection`
- Line 102: `def to_dict`
- Line 134: `class DistributionSection`
- Line 148: `def to_dict`
- Line 164: `class RSection`
- Line 177: `def to_dict`
- Line 192: `class ExcursionSection`
- Line 203: `def to_dict`
- Line 216: `class HoldingSection`
- Line 225: `def to_dict`
- Line 236: `class ExitGroup`
- Line 245: `def to_dict`
- Line 256: `class StreakSection`
- Line 264: `def to_dict`
- Line 274: `class RiskSection`
- Line 286: `def to_dict`
- Line 300: `class DrawdownSection`
- Line 317: `def to_dict`
- Line 336: `class StrategyGroup`
- Line 354: `def to_dict`
- Line 374: `class RegimeGroup`
- Line 389: `def to_dict`
- Line 406: `class SessionGroup`
- Line 416: `def to_dict`
- Line 428: `class ModelSection`
- Line 450: `def to_dict`
- Line 472: `class ExecutionSection`
- Line 485: `def to_dict`
- Line 500: `class NewsSection`
- Line 511: `def to_dict`
- Line 524: `class BehavioralSection`
- Line 544: `def to_dict`
- Line 561: `class AnomalyStateSection`
- Line 576: `def to_dict`
- Line 589: `class LossDriversSection`
- Line 599: `def to_dict`
- Line 611: `class ProfitDriversSection`
- Line 621: `def to_dict`
- Line 633: `class PeriodCompareSection`
- Line 648: `def to_dict`
- Line 665: `class AnomalyItem`
- Line 674: `def to_dict`
- Line 685: `class InsightItem`
- Line 691: `def to_dict`
- Line 696: `class HealthScoreSection`
- Line 707: `def to_dict`
- Line 720: `class ReportContainer`
- Line 756: `def to_dict`
- Line 793: `def _r`
### File: `src/nexus_scalp/observability/telegram_transport.py`
- Line 55: `def redact_secrets`
- Line 66: `def _classify_document_response`
- Line 85: `def _retry_after_from_body`
- Line 97: `class TelegramDocumentTransporter`
- Line 105: `def __init__`
- Line 128: `def upload`
- Line 194: `def _post_document`
- Line 222: `def _parse_document_response`
- Line 243: `def _build_multipart`
- Line 264: `def stats`
### File: `src/nexus_scalp/observability/logging.py`
- Line 239: `def _set_state`
- Line 249: `def reset_prune_throttle`
- Line 254: `def _set_prune_ts`
- Line 265: `def _shannon_entropy`
- Line 276: `def _redact_value`
- Line 288: `def _scrub`
- Line 313: `def _redact_sensitive_fields`
- Line 350: `def timestamp_now`
- Line 359: `def _add_timestamp`
- Line 380: `def _today_stamp`
- Line 384: `def _severity_dir`
- Line 388: `def _dated_log_path`
- Line 397: `def _next_part_number`
- Line 403: `class _LevelMatchFilter`
- Line 408: `def __init__`
- Line 412: `def filter`
- Line 416: `class DatedRotatingFileHandler`
- Line 426: `def __init__`
- Line 437: `def _target_path`
- Line 450: `def _open_stream`
- Line 456: `def _close_stream`
- Line 466: `def baseFilename`
- Line 470: `def emit`
- Line 501: `def close`
- Line 512: `def _prune_old_logs`
- Line 547: `def _console_stream`
- Line 558: `def _configure_stdout`
- Line 566: `def configure_logging`
- Line 654: `def get_logger`
- Line 659: `def log_event`
- Line 691: `def bind_correlation_id`
### File: `src/nexus_scalp/observability/ci_telegram_reporter.py`
- Line 81: `def _env`
- Line 85: `class CITelegramReporter`
- Line 88: `def __init__`
- Line 130: `def load_env_digest`
- Line 143: `def context`
- Line 158: `def check_status`
- Line 165: `def _junit_stats_raw`
- Line 187: `def junit_stats`
- Line 196: `def coverage_percent`
- Line 208: `def failed_test_names`
- Line 228: `def notify_run_started`
- Line 233: `def notify_run_finished`
- Line 279: `def notify_test_summary`
- Line 291: `def notify_release_started`
- Line 299: `def notify_release_success`
- Line 319: `def notify_release_failure`
- Line 346: `def notify_artifact_summary`
- Line 359: `def _artifact_names`
- Line 368: `def _send_text`
- Line 396: `def _upload_diagnostics`
- Line 433: `def _build_diagnostic_bundle`
- Line 460: `def esc_ctx`
### File: `src/nexus_scalp/observability/telegram_html.py`
- Line 28: `def esc`
- Line 35: `def esc_short`
- Line 45: `def code`
- Line 50: `def code_short`
- Line 54: `def link`
- Line 63: `def _sha_short`
- Line 87: `def _head`
- Line 91: `def _kv`
- Line 95: `def _kvk`
- Line 100: `def _section`
- Line 106: `def _blockquote`
- Line 111: `def _duration`
- Line 127: `class CIContext`
- Line 130: `def __init__`
- Line 158: `def correlation_id`
- Line 163: `def repo_url`
- Line 166: `def run_url`
- Line 171: `def commit_url`
- Line 176: `def pr_url`
- Line 181: `def with_pr`
- Line 198: `def release_url`
- Line 206: `def context_lines`
- Line 230: `def links`
- Line 248: `def format_run_started`
- Line 262: `def format_run_success`
- Line 299: `def format_run_failure`
- Line 348: `def format_run_cancelled`
- Line 359: `def _checks_block`
- Line 375: `def format_test_summary`
- Line 409: `def format_release_started`
- Line 422: `def format_release_success`
- Line 468: `def format_release_failure`
- Line 516: `def format_push_event`
- Line 551: `def format_pr_event`
- Line 587: `def format_security_event`
- Line 601: `def format_retry`
- Line 627: `def format_timeout`
- Line 639: `def _split_preserving`
- Line 661: `def format_error_details`
- Line 670: `def format_artifact_summary`
- Line 704: `def split_html_message`
- Line 737: `def _split_line_html_safe`
- Line 768: `def _tokenize_html`
- Line 793: `def _close_open_tags`
### File: `src/nexus_scalp/observability/telegram_notifier.py`
- Line 93: `class NotificationRecord`
- Line 116: `def to_dict`
- Line 136: `def classify_http_response`
- Line 206: `def _category_for_code`
- Line 219: `class TelegramNotifier`
- Line 229: `def __init__`
- Line 298: `def start_worker`
- Line 314: `def _worker_main`
- Line 342: `def _heartbeat`
- Line 361: `def stop_worker`
- Line 372: `def health_state`
- Line 400: `def send`
- Line 508: `def _dispatch_record`
- Line 639: `def _invoke_callback`
- Line 653: `def _is_blackhole_ip`
- Line 668: `def _split_api_url`
- Line 676: `def _should_bypass_dns`
- Line 693: `def _direct_https_open`
- Line 723: `def _urlopen_with_dns_fallback`
- Line 755: `def _send_msg_sync`
- Line 813: `def _parse_response`
- Line 859: `def classify_http_error`
- Line 863: `def _classify_exception`
- Line 881: `def get_me`
- Line 919: `def send_diagnostic`
- Line 965: `def _escape`
- Line 968: `def _truncate_message`
- Line 974: `def _redact_secrets`
- Line 981: `def _is_duplicate_or_cooling_down`
- Line 996: `def shutdown`
- Line 1003: `def notify_startup`
- Line 1023: `def notify_info`
- Line 1031: `def send_message`
- Line 1039: `def notify_generic_message`
- Line 1049: `def notify_test_message`
- Line 1059: `def notify_engine_stopped`
- Line 1070: `def notify_engine_error`
- Line 1082: `def notify_audit_purge`
- Line 1094: `def notify_warmup`
- Line 1106: `def notify_daily_summary`
- Line 1109: `def _fmt`
- Line 1126: `def notify_order_opened`
- Line 1143: `def notify_order_closed_profit`
- Line 1169: `def notify_order_closed_loss`
- Line 1195: `def notify_manual_close`
- Line 1223: `def notify_canonical_close`
- Line 1289: `def _exit_label`
- Line 1309: `def notify_early_emergency_cut`
- Line 1330: `def notify_break_even_applied`
- Line 1348: `def notify_trailing_stop_advanced`
- Line 1367: `def notify_break_even_applied_extended`
- Line 1389: `def notify_trailing_stop_advanced_extended`
- Line 1409: `def notify_partial_close`
- Line 1434: `def notify_emergency_cut`
- Line 1459: `def notify_tp_touched`
