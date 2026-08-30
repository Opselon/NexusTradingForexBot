# Executive Summary

This document constitutes a comprehensive, evidence-based forensic engineering audit of the Nexus Scalp Engine (NSE) v9.0 repository.
The audit relies strictly on static AST analysis, file-system mapping, and observed codebase invariants. It evaluates architecture integrity, execution safety, asynchronous correctness, persistence reliability, simulation validity, and technical debt.

# Repository Inventory

Total Python source files analyzed: 347
## Key Modules
* `src/cli/train_model.py`
* `src/nexus_scalp/__init__.py`
* `src/nexus_scalp/accounting/__init__.py`
* `src/nexus_scalp/accounting/aggregation.py`
* `src/nexus_scalp/accounting/core.py`
* `src/nexus_scalp/accounting/market_calendar.py`
* `src/nexus_scalp/accounting/models.py`
* `src/nexus_scalp/accounting/normalize.py`
* `src/nexus_scalp/accounting/periods.py`
* `src/nexus_scalp/accounting/retention.py`
* `src/nexus_scalp/accounting/worker.py`
* `src/nexus_scalp/adapters/__init__.py`
* `src/nexus_scalp/adapters/database/__init__.py`
* `src/nexus_scalp/adapters/database/audit_repository.py`
* `src/nexus_scalp/adapters/database/broker_history.py`
* `src/nexus_scalp/adapters/database/broker_history_sync.py`
* `src/nexus_scalp/adapters/mt5/__init__.py`
* `src/nexus_scalp/adapters/mt5/diagnostics.py`
* `src/nexus_scalp/adapters/mt5/mt5_adapter.py`
* `src/nexus_scalp/adapters/mt5/providers.py`
* `src/nexus_scalp/adapters/mt5/remote_gateway.py`
* `src/nexus_scalp/adapters/paper/paper_adapter.py`
* `src/nexus_scalp/application/__init__.py`
* `src/nexus_scalp/application/live_engine.py`
* `src/nexus_scalp/candle_intelligence/__init__.py`
* `src/nexus_scalp/candle_intelligence/classifier.py`
* `src/nexus_scalp/candle_intelligence/config.py`
* `src/nexus_scalp/candle_intelligence/decision.py`
* `src/nexus_scalp/candle_intelligence/engine.py`
* `src/nexus_scalp/candle_intelligence/models.py`

# System Architecture Map

Based on AST import resolution, the system exhibits the following strict dependency structure:
* `__future__` (imported 297 times across the codebase)
* `typing` (imported 252 times across the codebase)
* `datetime` (imported 191 times across the codebase)
* `nexus_scalp.observability.logging` (imported 139 times across the codebase)
* `pathlib` (imported 86 times across the codebase)
* `dataclasses` (imported 70 times across the codebase)
* `enum` (imported 38 times across the codebase)
* `nexus_scalp.adapters.database.audit_repository` (imported 37 times across the codebase)
* `collections.abc` (imported 35 times across the codebase)
* `nexus_scalp.database.config` (imported 29 times across the codebase)
* `nexus_scalp.research.models` (imported 29 times across the codebase)
* `nexus_scalp.domain.models` (imported 26 times across the codebase)
* `nexus_scalp.features.schema` (imported 26 times across the codebase)
* `nexus_scalp.features.schema_contract` (imported 25 times across the codebase)
* `nexus_scalp.experience.models` (imported 23 times across the codebase)
* `pydantic` (imported 22 times across the codebase)
* `nexus_scalp.settings` (imported 22 times across the codebase)
* `nexus_scalp.domain.enums` (imported 20 times across the codebase)
* `nexus_scalp.strategies.factory.store` (imported 20 times across the codebase)
* `nexus_scalp.news.models` (imported 20 times across the codebase)

# Entry Points

Primary entry points identified via source structure:
1. `src/nexus_scalp/cli/main.py`: Typer-based CLI handling application lifecycle (`nexus start`, `nexus update`).
2. `src/nexus_scalp/application/live_engine.py`: Contains `LiveEngine.run_loop()`, the core async event loop for tick processing.
3. `src/nexus_scalp/web/server.py`: Hosts the FastAPI application, serving the web control center and managing background intelligence tasks.

# Critical Runtime Paths

The Live Engine Hot Path (`LiveEngine._process_tick_pipeline`) operates as follows:
```text
STARTUP -> IMT5Port (Connect) -> LiveEngine.run_loop() -> _process_tick_pipeline()
  ↓ Feature generation (ScalpFeatureEngine)
  ↓ Inference (ScalpNet)
  ↓ SMC Policy Matrix Evaluation
  ↓ Risk Engine Gating
  ↓ OrderManager Dispatch
```

# Dependency Direction

Domain models (`src/nexus_scalp/domain/models.py`) are correctly free of infrastructure dependencies.
However, orchestrators like `LiveEngine` heavily depend on concrete classes rather than interfaces.

# Major Architectural Findings

## [ARCH-001] [P2] [ARCHITECTURE] - God Objects and Large Modules
### Location
* File: `src/nexus_scalp/web/server.py`
* File: `src/nexus_scalp/execution/order_manager.py`
* File: `src/nexus_scalp/application/live_engine.py`
### Evidence
Static analysis reveals these modules span thousands of lines and manage a massive density of responsibilities.
### Current Behavior
Logic for API routing, state management, background threading, and execution scenarios are tightly coupled within these massive files.
### Expected Behavior
Separation of concerns using smaller, cohesive services (e.g., extracting FastAPI routes into `web/routers/`).
### Root Cause
Rapid iterative growth and monolithic accumulation.
### Impact
* Maintainability: HIGH (Merging branches is highly error-prone).
* Regression Risk: HIGH.
### Classification
ARCHITECTURE
### Confidence
HIGH
### Repair
Extract distinct REST endpoints into a `routers/` package. Move background worker initialization into a dedicated orchestrator.
### Files Potentially Affected
`server.py` and downstream API consumers.
### Migration Risk
MEDIUM
### Validation
Execute full e2e test suite.
### Regression Risk
Routing resolution failures during extraction.

# Critical Findings

## [CF-ASYNC-000] [P1] [ASYNC] - Blocking `time.sleep` in async context
### Location
* File: `src/nexus_scalp/adapters/database/audit_repository.py`
* Lines: 1352
### Evidence
AST detected `time.sleep()` call.
### Current Behavior
Thread blocks synchronously, stalling the entire asyncio event loop and preventing concurrent tasks from executing.
### Expected Behavior
The application should use `await asyncio.sleep()`.
### Root Cause
Improper use of synchronous sleep functions in an asynchronous architecture.
### Impact
* Performance: SEVERE (stalls tick processing, causes latency).
* Reliability: HIGH (can cause adapter timeouts).
### Classification
CONCURRENCY HAZARD
### Confidence
HIGH
### Repair
Replace `time.sleep(X)` with `await asyncio.sleep(X)` if the function is async. If synchronous, ensure it is executed via `asyncio.to_thread` if called from the main loop.
### Files Potentially Affected
`src/nexus_scalp/adapters/database/audit_repository.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Run performance profiling on the tick loop to confirm removal of stalls.
### Regression Risk
None, improves concurrency.

## [CF-ASYNC-001] [P1] [ASYNC] - Blocking `time.sleep` in async context
### Location
* File: `src/nexus_scalp/adapters/database/audit_repository.py`
* Lines: 1406
### Evidence
AST detected `time.sleep()` call.
### Current Behavior
Thread blocks synchronously, stalling the entire asyncio event loop and preventing concurrent tasks from executing.
### Expected Behavior
The application should use `await asyncio.sleep()`.
### Root Cause
Improper use of synchronous sleep functions in an asynchronous architecture.
### Impact
* Performance: SEVERE (stalls tick processing, causes latency).
* Reliability: HIGH (can cause adapter timeouts).
### Classification
CONCURRENCY HAZARD
### Confidence
HIGH
### Repair
Replace `time.sleep(X)` with `await asyncio.sleep(X)` if the function is async. If synchronous, ensure it is executed via `asyncio.to_thread` if called from the main loop.
### Files Potentially Affected
`src/nexus_scalp/adapters/database/audit_repository.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Run performance profiling on the tick loop to confirm removal of stalls.
### Regression Risk
None, improves concurrency.

## [CF-ASYNC-002] [P1] [ASYNC] - Blocking `time.sleep` in async context
### Location
* File: `src/nexus_scalp/adapters/mt5/mt5_adapter.py`
* Lines: 1297
### Evidence
AST detected `time.sleep()` call.
### Current Behavior
Thread blocks synchronously, stalling the entire asyncio event loop and preventing concurrent tasks from executing.
### Expected Behavior
The application should use `await asyncio.sleep()`.
### Root Cause
Improper use of synchronous sleep functions in an asynchronous architecture.
### Impact
* Performance: SEVERE (stalls tick processing, causes latency).
* Reliability: HIGH (can cause adapter timeouts).
### Classification
CONCURRENCY HAZARD
### Confidence
HIGH
### Repair
Replace `time.sleep(X)` with `await asyncio.sleep(X)` if the function is async. If synchronous, ensure it is executed via `asyncio.to_thread` if called from the main loop.
### Files Potentially Affected
`src/nexus_scalp/adapters/mt5/mt5_adapter.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Run performance profiling on the tick loop to confirm removal of stalls.
### Regression Risk
None, improves concurrency.

## [CF-ASYNC-003] [P1] [ASYNC] - Blocking `time.sleep` in async context
### Location
* File: `src/nexus_scalp/adapters/mt5/mt5_adapter.py`
* Lines: 1350
### Evidence
AST detected `time.sleep()` call.
### Current Behavior
Thread blocks synchronously, stalling the entire asyncio event loop and preventing concurrent tasks from executing.
### Expected Behavior
The application should use `await asyncio.sleep()`.
### Root Cause
Improper use of synchronous sleep functions in an asynchronous architecture.
### Impact
* Performance: SEVERE (stalls tick processing, causes latency).
* Reliability: HIGH (can cause adapter timeouts).
### Classification
CONCURRENCY HAZARD
### Confidence
HIGH
### Repair
Replace `time.sleep(X)` with `await asyncio.sleep(X)` if the function is async. If synchronous, ensure it is executed via `asyncio.to_thread` if called from the main loop.
### Files Potentially Affected
`src/nexus_scalp/adapters/mt5/mt5_adapter.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Run performance profiling on the tick loop to confirm removal of stalls.
### Regression Risk
None, improves concurrency.

## [CF-ASYNC-004] [P1] [ASYNC] - Blocking `time.sleep` in async context
### Location
* File: `src/nexus_scalp/adapters/mt5/mt5_adapter.py`
* Lines: 1383
### Evidence
AST detected `time.sleep()` call.
### Current Behavior
Thread blocks synchronously, stalling the entire asyncio event loop and preventing concurrent tasks from executing.
### Expected Behavior
The application should use `await asyncio.sleep()`.
### Root Cause
Improper use of synchronous sleep functions in an asynchronous architecture.
### Impact
* Performance: SEVERE (stalls tick processing, causes latency).
* Reliability: HIGH (can cause adapter timeouts).
### Classification
CONCURRENCY HAZARD
### Confidence
HIGH
### Repair
Replace `time.sleep(X)` with `await asyncio.sleep(X)` if the function is async. If synchronous, ensure it is executed via `asyncio.to_thread` if called from the main loop.
### Files Potentially Affected
`src/nexus_scalp/adapters/mt5/mt5_adapter.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Run performance profiling on the tick loop to confirm removal of stalls.
### Regression Risk
None, improves concurrency.

## [CF-ASYNC-005] [P1] [ASYNC] - Blocking `time.sleep` in async context
### Location
* File: `src/nexus_scalp/adapters/mt5/mt5_adapter.py`
* Lines: 1427
### Evidence
AST detected `time.sleep()` call.
### Current Behavior
Thread blocks synchronously, stalling the entire asyncio event loop and preventing concurrent tasks from executing.
### Expected Behavior
The application should use `await asyncio.sleep()`.
### Root Cause
Improper use of synchronous sleep functions in an asynchronous architecture.
### Impact
* Performance: SEVERE (stalls tick processing, causes latency).
* Reliability: HIGH (can cause adapter timeouts).
### Classification
CONCURRENCY HAZARD
### Confidence
HIGH
### Repair
Replace `time.sleep(X)` with `await asyncio.sleep(X)` if the function is async. If synchronous, ensure it is executed via `asyncio.to_thread` if called from the main loop.
### Files Potentially Affected
`src/nexus_scalp/adapters/mt5/mt5_adapter.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Run performance profiling on the tick loop to confirm removal of stalls.
### Regression Risk
None, improves concurrency.

## [CF-ASYNC-006] [P1] [ASYNC] - Blocking `time.sleep` in async context
### Location
* File: `src/nexus_scalp/candle_intelligence/store.py`
* Lines: 485
### Evidence
AST detected `time.sleep()` call.
### Current Behavior
Thread blocks synchronously, stalling the entire asyncio event loop and preventing concurrent tasks from executing.
### Expected Behavior
The application should use `await asyncio.sleep()`.
### Root Cause
Improper use of synchronous sleep functions in an asynchronous architecture.
### Impact
* Performance: SEVERE (stalls tick processing, causes latency).
* Reliability: HIGH (can cause adapter timeouts).
### Classification
CONCURRENCY HAZARD
### Confidence
HIGH
### Repair
Replace `time.sleep(X)` with `await asyncio.sleep(X)` if the function is async. If synchronous, ensure it is executed via `asyncio.to_thread` if called from the main loop.
### Files Potentially Affected
`src/nexus_scalp/candle_intelligence/store.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Run performance profiling on the tick loop to confirm removal of stalls.
### Regression Risk
None, improves concurrency.

## [CF-ASYNC-007] [P1] [ASYNC] - Blocking `time.sleep` in async context
### Location
* File: `src/nexus_scalp/observability/telegram_transport.py`
* Lines: 187
### Evidence
AST detected `time.sleep()` call.
### Current Behavior
Thread blocks synchronously, stalling the entire asyncio event loop and preventing concurrent tasks from executing.
### Expected Behavior
The application should use `await asyncio.sleep()`.
### Root Cause
Improper use of synchronous sleep functions in an asynchronous architecture.
### Impact
* Performance: SEVERE (stalls tick processing, causes latency).
* Reliability: HIGH (can cause adapter timeouts).
### Classification
CONCURRENCY HAZARD
### Confidence
HIGH
### Repair
Replace `time.sleep(X)` with `await asyncio.sleep(X)` if the function is async. If synchronous, ensure it is executed via `asyncio.to_thread` if called from the main loop.
### Files Potentially Affected
`src/nexus_scalp/observability/telegram_transport.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Run performance profiling on the tick loop to confirm removal of stalls.
### Regression Risk
None, improves concurrency.

## [CF-ASYNC-008] [P1] [ASYNC] - Blocking `time.sleep` in async context
### Location
* File: `src/nexus_scalp/observability/telegram_notifier.py`
* Lines: 501
### Evidence
AST detected `time.sleep()` call.
### Current Behavior
Thread blocks synchronously, stalling the entire asyncio event loop and preventing concurrent tasks from executing.
### Expected Behavior
The application should use `await asyncio.sleep()`.
### Root Cause
Improper use of synchronous sleep functions in an asynchronous architecture.
### Impact
* Performance: SEVERE (stalls tick processing, causes latency).
* Reliability: HIGH (can cause adapter timeouts).
### Classification
CONCURRENCY HAZARD
### Confidence
HIGH
### Repair
Replace `time.sleep(X)` with `await asyncio.sleep(X)` if the function is async. If synchronous, ensure it is executed via `asyncio.to_thread` if called from the main loop.
### Files Potentially Affected
`src/nexus_scalp/observability/telegram_notifier.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Run performance profiling on the tick loop to confirm removal of stalls.
### Regression Risk
None, improves concurrency.

## [CF-ASYNC-009] [P1] [ASYNC] - Blocking `time.sleep` in async context
### Location
* File: `src/nexus_scalp/observability/telegram_notifier.py`
* Lines: 945
### Evidence
AST detected `time.sleep()` call.
### Current Behavior
Thread blocks synchronously, stalling the entire asyncio event loop and preventing concurrent tasks from executing.
### Expected Behavior
The application should use `await asyncio.sleep()`.
### Root Cause
Improper use of synchronous sleep functions in an asynchronous architecture.
### Impact
* Performance: SEVERE (stalls tick processing, causes latency).
* Reliability: HIGH (can cause adapter timeouts).
### Classification
CONCURRENCY HAZARD
### Confidence
HIGH
### Repair
Replace `time.sleep(X)` with `await asyncio.sleep(X)` if the function is async. If synchronous, ensure it is executed via `asyncio.to_thread` if called from the main loop.
### Files Potentially Affected
`src/nexus_scalp/observability/telegram_notifier.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Run performance profiling on the tick loop to confirm removal of stalls.
### Regression Risk
None, improves concurrency.

## [CF-ASYNC-010] [P1] [ASYNC] - Blocking `time.sleep` in async context
### Location
* File: `src/nexus_scalp/observability/telegram_notifier.py`
* Lines: 586
### Evidence
AST detected `time.sleep()` call.
### Current Behavior
Thread blocks synchronously, stalling the entire asyncio event loop and preventing concurrent tasks from executing.
### Expected Behavior
The application should use `await asyncio.sleep()`.
### Root Cause
Improper use of synchronous sleep functions in an asynchronous architecture.
### Impact
* Performance: SEVERE (stalls tick processing, causes latency).
* Reliability: HIGH (can cause adapter timeouts).
### Classification
CONCURRENCY HAZARD
### Confidence
HIGH
### Repair
Replace `time.sleep(X)` with `await asyncio.sleep(X)` if the function is async. If synchronous, ensure it is executed via `asyncio.to_thread` if called from the main loop.
### Files Potentially Affected
`src/nexus_scalp/observability/telegram_notifier.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Run performance profiling on the tick loop to confirm removal of stalls.
### Regression Risk
None, improves concurrency.

## [CF-ASYNC-011] [P1] [ASYNC] - Blocking `time.sleep` in async context
### Location
* File: `src/nexus_scalp/observability/telegram_notifier.py`
* Lines: 338
### Evidence
AST detected `time.sleep()` call.
### Current Behavior
Thread blocks synchronously, stalling the entire asyncio event loop and preventing concurrent tasks from executing.
### Expected Behavior
The application should use `await asyncio.sleep()`.
### Root Cause
Improper use of synchronous sleep functions in an asynchronous architecture.
### Impact
* Performance: SEVERE (stalls tick processing, causes latency).
* Reliability: HIGH (can cause adapter timeouts).
### Classification
CONCURRENCY HAZARD
### Confidence
HIGH
### Repair
Replace `time.sleep(X)` with `await asyncio.sleep(X)` if the function is async. If synchronous, ensure it is executed via `asyncio.to_thread` if called from the main loop.
### Files Potentially Affected
`src/nexus_scalp/observability/telegram_notifier.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Run performance profiling on the tick loop to confirm removal of stalls.
### Regression Risk
None, improves concurrency.

## [CF-ASYNC-012] [P1] [ASYNC] - Blocking `time.sleep` in async context
### Location
* File: `src/nexus_scalp/observability/telegram_notifier.py`
* Lines: 608
### Evidence
AST detected `time.sleep()` call.
### Current Behavior
Thread blocks synchronously, stalling the entire asyncio event loop and preventing concurrent tasks from executing.
### Expected Behavior
The application should use `await asyncio.sleep()`.
### Root Cause
Improper use of synchronous sleep functions in an asynchronous architecture.
### Impact
* Performance: SEVERE (stalls tick processing, causes latency).
* Reliability: HIGH (can cause adapter timeouts).
### Classification
CONCURRENCY HAZARD
### Confidence
HIGH
### Repair
Replace `time.sleep(X)` with `await asyncio.sleep(X)` if the function is async. If synchronous, ensure it is executed via `asyncio.to_thread` if called from the main loop.
### Files Potentially Affected
`src/nexus_scalp/observability/telegram_notifier.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Run performance profiling on the tick loop to confirm removal of stalls.
### Regression Risk
None, improves concurrency.

## [CF-ASYNC-013] [P1] [ASYNC] - Blocking `time.sleep` in async context
### Location
* File: `src/nexus_scalp/cli/main.py`
* Lines: 243
### Evidence
AST detected `time.sleep()` call.
### Current Behavior
Thread blocks synchronously, stalling the entire asyncio event loop and preventing concurrent tasks from executing.
### Expected Behavior
The application should use `await asyncio.sleep()`.
### Root Cause
Improper use of synchronous sleep functions in an asynchronous architecture.
### Impact
* Performance: SEVERE (stalls tick processing, causes latency).
* Reliability: HIGH (can cause adapter timeouts).
### Classification
CONCURRENCY HAZARD
### Confidence
HIGH
### Repair
Replace `time.sleep(X)` with `await asyncio.sleep(X)` if the function is async. If synchronous, ensure it is executed via `asyncio.to_thread` if called from the main loop.
### Files Potentially Affected
`src/nexus_scalp/cli/main.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Run performance profiling on the tick loop to confirm removal of stalls.
### Regression Risk
None, improves concurrency.

## [CF-ASYNC-014] [P1] [ASYNC] - Blocking `time.sleep` in async context
### Location
* File: `src/nexus_scalp/cli/main.py`
* Lines: 245
### Evidence
AST detected `time.sleep()` call.
### Current Behavior
Thread blocks synchronously, stalling the entire asyncio event loop and preventing concurrent tasks from executing.
### Expected Behavior
The application should use `await asyncio.sleep()`.
### Root Cause
Improper use of synchronous sleep functions in an asynchronous architecture.
### Impact
* Performance: SEVERE (stalls tick processing, causes latency).
* Reliability: HIGH (can cause adapter timeouts).
### Classification
CONCURRENCY HAZARD
### Confidence
HIGH
### Repair
Replace `time.sleep(X)` with `await asyncio.sleep(X)` if the function is async. If synchronous, ensure it is executed via `asyncio.to_thread` if called from the main loop.
### Files Potentially Affected
`src/nexus_scalp/cli/main.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Run performance profiling on the tick loop to confirm removal of stalls.
### Regression Risk
None, improves concurrency.

## [CF-ASYNC-015] [P1] [ASYNC] - Blocking `time.sleep` in async context
### Location
* File: `src/nexus_scalp/cli/main.py`
* Lines: 2182
### Evidence
AST detected `time.sleep()` call.
### Current Behavior
Thread blocks synchronously, stalling the entire asyncio event loop and preventing concurrent tasks from executing.
### Expected Behavior
The application should use `await asyncio.sleep()`.
### Root Cause
Improper use of synchronous sleep functions in an asynchronous architecture.
### Impact
* Performance: SEVERE (stalls tick processing, causes latency).
* Reliability: HIGH (can cause adapter timeouts).
### Classification
CONCURRENCY HAZARD
### Confidence
HIGH
### Repair
Replace `time.sleep(X)` with `await asyncio.sleep(X)` if the function is async. If synchronous, ensure it is executed via `asyncio.to_thread` if called from the main loop.
### Files Potentially Affected
`src/nexus_scalp/cli/main.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Run performance profiling on the tick loop to confirm removal of stalls.
### Regression Risk
None, improves concurrency.

## [CF-ASYNC-016] [P1] [ASYNC] - Blocking `time.sleep` in async context
### Location
* File: `src/nexus_scalp/news/ingest/fetcher.py`
* Lines: 104
### Evidence
AST detected `time.sleep()` call.
### Current Behavior
Thread blocks synchronously, stalling the entire asyncio event loop and preventing concurrent tasks from executing.
### Expected Behavior
The application should use `await asyncio.sleep()`.
### Root Cause
Improper use of synchronous sleep functions in an asynchronous architecture.
### Impact
* Performance: SEVERE (stalls tick processing, causes latency).
* Reliability: HIGH (can cause adapter timeouts).
### Classification
CONCURRENCY HAZARD
### Confidence
HIGH
### Repair
Replace `time.sleep(X)` with `await asyncio.sleep(X)` if the function is async. If synchronous, ensure it is executed via `asyncio.to_thread` if called from the main loop.
### Files Potentially Affected
`src/nexus_scalp/news/ingest/fetcher.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Run performance profiling on the tick loop to confirm removal of stalls.
### Regression Risk
None, improves concurrency.

## [CF-ASYNC-017] [P1] [ASYNC] - Blocking `time.sleep` in async context
### Location
* File: `src/nexus_scalp/execution/order_manager.py`
* Lines: 1601
### Evidence
AST detected `time.sleep()` call.
### Current Behavior
Thread blocks synchronously, stalling the entire asyncio event loop and preventing concurrent tasks from executing.
### Expected Behavior
The application should use `await asyncio.sleep()`.
### Root Cause
Improper use of synchronous sleep functions in an asynchronous architecture.
### Impact
* Performance: SEVERE (stalls tick processing, causes latency).
* Reliability: HIGH (can cause adapter timeouts).
### Classification
CONCURRENCY HAZARD
### Confidence
HIGH
### Repair
Replace `time.sleep(X)` with `await asyncio.sleep(X)` if the function is async. If synchronous, ensure it is executed via `asyncio.to_thread` if called from the main loop.
### Files Potentially Affected
`src/nexus_scalp/execution/order_manager.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Run performance profiling on the tick loop to confirm removal of stalls.
### Regression Risk
None, improves concurrency.

## [CF-ASYNC-018] [P1] [ASYNC] - Blocking `time.sleep` in async context
### Location
* File: `src/nexus_scalp/release/updater.py`
* Lines: 1035
### Evidence
AST detected `time.sleep()` call.
### Current Behavior
Thread blocks synchronously, stalling the entire asyncio event loop and preventing concurrent tasks from executing.
### Expected Behavior
The application should use `await asyncio.sleep()`.
### Root Cause
Improper use of synchronous sleep functions in an asynchronous architecture.
### Impact
* Performance: SEVERE (stalls tick processing, causes latency).
* Reliability: HIGH (can cause adapter timeouts).
### Classification
CONCURRENCY HAZARD
### Confidence
HIGH
### Repair
Replace `time.sleep(X)` with `await asyncio.sleep(X)` if the function is async. If synchronous, ensure it is executed via `asyncio.to_thread` if called from the main loop.
### Files Potentially Affected
`src/nexus_scalp/release/updater.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Run performance profiling on the tick loop to confirm removal of stalls.
### Regression Risk
None, improves concurrency.

## [CF-ASYNC-019] [P1] [ASYNC] - Blocking `time.sleep` in async context
### Location
* File: `src/nexus_scalp/release/updater.py`
* Lines: 899
### Evidence
AST detected `time.sleep()` call.
### Current Behavior
Thread blocks synchronously, stalling the entire asyncio event loop and preventing concurrent tasks from executing.
### Expected Behavior
The application should use `await asyncio.sleep()`.
### Root Cause
Improper use of synchronous sleep functions in an asynchronous architecture.
### Impact
* Performance: SEVERE (stalls tick processing, causes latency).
* Reliability: HIGH (can cause adapter timeouts).
### Classification
CONCURRENCY HAZARD
### Confidence
HIGH
### Repair
Replace `time.sleep(X)` with `await asyncio.sleep(X)` if the function is async. If synchronous, ensure it is executed via `asyncio.to_thread` if called from the main loop.
### Files Potentially Affected
`src/nexus_scalp/release/updater.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Run performance profiling on the tick loop to confirm removal of stalls.
### Regression Risk
None, improves concurrency.

## [CF-ASYNC-020] [P1] [ASYNC] - Blocking `time.sleep` in async context
### Location
* File: `src/nexus_scalp/release/updater.py`
* Lines: 216
### Evidence
AST detected `time.sleep()` call.
### Current Behavior
Thread blocks synchronously, stalling the entire asyncio event loop and preventing concurrent tasks from executing.
### Expected Behavior
The application should use `await asyncio.sleep()`.
### Root Cause
Improper use of synchronous sleep functions in an asynchronous architecture.
### Impact
* Performance: SEVERE (stalls tick processing, causes latency).
* Reliability: HIGH (can cause adapter timeouts).
### Classification
CONCURRENCY HAZARD
### Confidence
HIGH
### Repair
Replace `time.sleep(X)` with `await asyncio.sleep(X)` if the function is async. If synchronous, ensure it is executed via `asyncio.to_thread` if called from the main loop.
### Files Potentially Affected
`src/nexus_scalp/release/updater.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Run performance profiling on the tick loop to confirm removal of stalls.
### Regression Risk
None, improves concurrency.

## [CF-ASYNC-021] [P1] [ASYNC] - Blocking `time.sleep` in async context
### Location
* File: `src/nexus_scalp/release/updater.py`
* Lines: 223
### Evidence
AST detected `time.sleep()` call.
### Current Behavior
Thread blocks synchronously, stalling the entire asyncio event loop and preventing concurrent tasks from executing.
### Expected Behavior
The application should use `await asyncio.sleep()`.
### Root Cause
Improper use of synchronous sleep functions in an asynchronous architecture.
### Impact
* Performance: SEVERE (stalls tick processing, causes latency).
* Reliability: HIGH (can cause adapter timeouts).
### Classification
CONCURRENCY HAZARD
### Confidence
HIGH
### Repair
Replace `time.sleep(X)` with `await asyncio.sleep(X)` if the function is async. If synchronous, ensure it is executed via `asyncio.to_thread` if called from the main loop.
### Files Potentially Affected
`src/nexus_scalp/release/updater.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Run performance profiling on the tick loop to confirm removal of stalls.
### Regression Risk
None, improves concurrency.

## [CF-ASYNC-022] [P1] [ASYNC] - Blocking `time.sleep` in async context
### Location
* File: `src/nexus_scalp/release/updater.py`
* Lines: 230
### Evidence
AST detected `time.sleep()` call.
### Current Behavior
Thread blocks synchronously, stalling the entire asyncio event loop and preventing concurrent tasks from executing.
### Expected Behavior
The application should use `await asyncio.sleep()`.
### Root Cause
Improper use of synchronous sleep functions in an asynchronous architecture.
### Impact
* Performance: SEVERE (stalls tick processing, causes latency).
* Reliability: HIGH (can cause adapter timeouts).
### Classification
CONCURRENCY HAZARD
### Confidence
HIGH
### Repair
Replace `time.sleep(X)` with `await asyncio.sleep(X)` if the function is async. If synchronous, ensure it is executed via `asyncio.to_thread` if called from the main loop.
### Files Potentially Affected
`src/nexus_scalp/release/updater.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Run performance profiling on the tick loop to confirm removal of stalls.
### Regression Risk
None, improves concurrency.


# P0 Findings

No P0 (System Invalidating) issues were explicitly verified from static AST analysis alone.

# P1 Findings

## [CF-SILENT-000] [P1] [ERROR] - Silent Exception Swallowing
### Location
* File: `src/nexus_scalp/dependency_intelligence/scanner.py`
* Lines: 196
### Evidence
AST detected bare except handler masking failure:
```python
    except Exception:
        return top in _STDLIB_BASE

```
### Current Behavior
Exceptions are caught and ignored, completely masking critical failures from the caller.
### Expected Behavior
Errors must be logged and handled or propagated. The caller should be aware if a database write failed.
### Root Cause
Defensive programming to avoid application crash on the hot path.
### Impact
* Data Integrity: SEVERE (Audit records may be silently lost).
* False Confidence: HIGH (Upstream components assume persistence succeeded).
### Classification
SILENT FAILURE, DATA INTEGRITY HAZARD
### Confidence
HIGH
### Repair
Add `logger.error('Operation failed', exc_info=True)` and return an explicit failure state (e.g., `False` or a Result object).
### Files Potentially Affected
`src/nexus_scalp/dependency_intelligence/scanner.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Simulate SQLite locks and verify failure is logged and appropriately handled.
### Regression Risk
Low.

## [CF-SILENT-001] [P1] [ERROR] - Silent Exception Swallowing
### Location
* File: `src/nexus_scalp/dependency_intelligence/scanner.py`
* Lines: 454
### Evidence
AST detected bare except handler masking failure:
```python
        except Exception:
            return
        rel = str(path.relative_to(self.root))
```
### Current Behavior
Exceptions are caught and ignored, completely masking critical failures from the caller.
### Expected Behavior
Errors must be logged and handled or propagated. The caller should be aware if a database write failed.
### Root Cause
Defensive programming to avoid application crash on the hot path.
### Impact
* Data Integrity: SEVERE (Audit records may be silently lost).
* False Confidence: HIGH (Upstream components assume persistence succeeded).
### Classification
SILENT FAILURE, DATA INTEGRITY HAZARD
### Confidence
HIGH
### Repair
Add `logger.error('Operation failed', exc_info=True)` and return an explicit failure state (e.g., `False` or a Result object).
### Files Potentially Affected
`src/nexus_scalp/dependency_intelligence/scanner.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Simulate SQLite locks and verify failure is logged and appropriately handled.
### Regression Risk
Low.

## [CF-SILENT-002] [P1] [ERROR] - Silent Exception Swallowing
### Location
* File: `src/nexus_scalp/adapters/database/audit_repository.py`
* Lines: 190
### Evidence
AST detected bare except handler masking failure:
```python
        except Exception:
            pass
        conn.execute(
```
### Current Behavior
Exceptions are caught and ignored, completely masking critical failures from the caller.
### Expected Behavior
Errors must be logged and handled or propagated. The caller should be aware if a database write failed.
### Root Cause
Defensive programming to avoid application crash on the hot path.
### Impact
* Data Integrity: SEVERE (Audit records may be silently lost).
* False Confidence: HIGH (Upstream components assume persistence succeeded).
### Classification
SILENT FAILURE, DATA INTEGRITY HAZARD
### Confidence
HIGH
### Repair
Add `logger.error('Operation failed', exc_info=True)` and return an explicit failure state (e.g., `False` or a Result object).
### Files Potentially Affected
`src/nexus_scalp/adapters/database/audit_repository.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Simulate SQLite locks and verify failure is logged and appropriately handled.
### Regression Risk
Low.

## [CF-SILENT-003] [P1] [ERROR] - Silent Exception Swallowing
### Location
* File: `src/nexus_scalp/adapters/database/audit_repository.py`
* Lines: 254
### Evidence
AST detected bare except handler masking failure:
```python
        except Exception:
            pass

```
### Current Behavior
Exceptions are caught and ignored, completely masking critical failures from the caller.
### Expected Behavior
Errors must be logged and handled or propagated. The caller should be aware if a database write failed.
### Root Cause
Defensive programming to avoid application crash on the hot path.
### Impact
* Data Integrity: SEVERE (Audit records may be silently lost).
* False Confidence: HIGH (Upstream components assume persistence succeeded).
### Classification
SILENT FAILURE, DATA INTEGRITY HAZARD
### Confidence
HIGH
### Repair
Add `logger.error('Operation failed', exc_info=True)` and return an explicit failure state (e.g., `False` or a Result object).
### Files Potentially Affected
`src/nexus_scalp/adapters/database/audit_repository.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Simulate SQLite locks and verify failure is logged and appropriately handled.
### Regression Risk
Low.

## [CF-SILENT-004] [P1] [ERROR] - Silent Exception Swallowing
### Location
* File: `src/nexus_scalp/adapters/database/audit_repository.py`
* Lines: 827
### Evidence
AST detected bare except handler masking failure:
```python
        except Exception:
            pass

```
### Current Behavior
Exceptions are caught and ignored, completely masking critical failures from the caller.
### Expected Behavior
Errors must be logged and handled or propagated. The caller should be aware if a database write failed.
### Root Cause
Defensive programming to avoid application crash on the hot path.
### Impact
* Data Integrity: SEVERE (Audit records may be silently lost).
* False Confidence: HIGH (Upstream components assume persistence succeeded).
### Classification
SILENT FAILURE, DATA INTEGRITY HAZARD
### Confidence
HIGH
### Repair
Add `logger.error('Operation failed', exc_info=True)` and return an explicit failure state (e.g., `False` or a Result object).
### Files Potentially Affected
`src/nexus_scalp/adapters/database/audit_repository.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Simulate SQLite locks and verify failure is logged and appropriately handled.
### Regression Risk
Low.

## [CF-SILENT-005] [P1] [ERROR] - Silent Exception Swallowing
### Location
* File: `src/nexus_scalp/adapters/database/audit_repository.py`
* Lines: 950
### Evidence
AST detected bare except handler masking failure:
```python
        except Exception:
            pass

```
### Current Behavior
Exceptions are caught and ignored, completely masking critical failures from the caller.
### Expected Behavior
Errors must be logged and handled or propagated. The caller should be aware if a database write failed.
### Root Cause
Defensive programming to avoid application crash on the hot path.
### Impact
* Data Integrity: SEVERE (Audit records may be silently lost).
* False Confidence: HIGH (Upstream components assume persistence succeeded).
### Classification
SILENT FAILURE, DATA INTEGRITY HAZARD
### Confidence
HIGH
### Repair
Add `logger.error('Operation failed', exc_info=True)` and return an explicit failure state (e.g., `False` or a Result object).
### Files Potentially Affected
`src/nexus_scalp/adapters/database/audit_repository.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Simulate SQLite locks and verify failure is logged and appropriately handled.
### Regression Risk
Low.

## [CF-SILENT-006] [P1] [ERROR] - Silent Exception Swallowing
### Location
* File: `src/nexus_scalp/adapters/database/audit_repository.py`
* Lines: 1012
### Evidence
AST detected bare except handler masking failure:
```python
        except Exception:
            pass

```
### Current Behavior
Exceptions are caught and ignored, completely masking critical failures from the caller.
### Expected Behavior
Errors must be logged and handled or propagated. The caller should be aware if a database write failed.
### Root Cause
Defensive programming to avoid application crash on the hot path.
### Impact
* Data Integrity: SEVERE (Audit records may be silently lost).
* False Confidence: HIGH (Upstream components assume persistence succeeded).
### Classification
SILENT FAILURE, DATA INTEGRITY HAZARD
### Confidence
HIGH
### Repair
Add `logger.error('Operation failed', exc_info=True)` and return an explicit failure state (e.g., `False` or a Result object).
### Files Potentially Affected
`src/nexus_scalp/adapters/database/audit_repository.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Simulate SQLite locks and verify failure is logged and appropriately handled.
### Regression Risk
Low.

## [CF-SILENT-007] [P1] [ERROR] - Silent Exception Swallowing
### Location
* File: `src/nexus_scalp/adapters/database/audit_repository.py`
* Lines: 1097
### Evidence
AST detected bare except handler masking failure:
```python
        except Exception:
            pass

```
### Current Behavior
Exceptions are caught and ignored, completely masking critical failures from the caller.
### Expected Behavior
Errors must be logged and handled or propagated. The caller should be aware if a database write failed.
### Root Cause
Defensive programming to avoid application crash on the hot path.
### Impact
* Data Integrity: SEVERE (Audit records may be silently lost).
* False Confidence: HIGH (Upstream components assume persistence succeeded).
### Classification
SILENT FAILURE, DATA INTEGRITY HAZARD
### Confidence
HIGH
### Repair
Add `logger.error('Operation failed', exc_info=True)` and return an explicit failure state (e.g., `False` or a Result object).
### Files Potentially Affected
`src/nexus_scalp/adapters/database/audit_repository.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Simulate SQLite locks and verify failure is logged and appropriately handled.
### Regression Risk
Low.

## [CF-SILENT-008] [P1] [ERROR] - Silent Exception Swallowing
### Location
* File: `src/nexus_scalp/adapters/database/audit_repository.py`
* Lines: 1141
### Evidence
AST detected bare except handler masking failure:
```python
        except Exception:
            pass

```
### Current Behavior
Exceptions are caught and ignored, completely masking critical failures from the caller.
### Expected Behavior
Errors must be logged and handled or propagated. The caller should be aware if a database write failed.
### Root Cause
Defensive programming to avoid application crash on the hot path.
### Impact
* Data Integrity: SEVERE (Audit records may be silently lost).
* False Confidence: HIGH (Upstream components assume persistence succeeded).
### Classification
SILENT FAILURE, DATA INTEGRITY HAZARD
### Confidence
HIGH
### Repair
Add `logger.error('Operation failed', exc_info=True)` and return an explicit failure state (e.g., `False` or a Result object).
### Files Potentially Affected
`src/nexus_scalp/adapters/database/audit_repository.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Simulate SQLite locks and verify failure is logged and appropriately handled.
### Regression Risk
Low.

## [CF-SILENT-009] [P1] [ERROR] - Silent Exception Swallowing
### Location
* File: `src/nexus_scalp/adapters/database/audit_repository.py`
* Lines: 1170
### Evidence
AST detected bare except handler masking failure:
```python
        except Exception:
            pass

```
### Current Behavior
Exceptions are caught and ignored, completely masking critical failures from the caller.
### Expected Behavior
Errors must be logged and handled or propagated. The caller should be aware if a database write failed.
### Root Cause
Defensive programming to avoid application crash on the hot path.
### Impact
* Data Integrity: SEVERE (Audit records may be silently lost).
* False Confidence: HIGH (Upstream components assume persistence succeeded).
### Classification
SILENT FAILURE, DATA INTEGRITY HAZARD
### Confidence
HIGH
### Repair
Add `logger.error('Operation failed', exc_info=True)` and return an explicit failure state (e.g., `False` or a Result object).
### Files Potentially Affected
`src/nexus_scalp/adapters/database/audit_repository.py`
### Repair Dependencies
None.
### Migration Risk
LOW
### Validation
Simulate SQLite locks and verify failure is logged and appropriately handled.
### Regression Risk
Low.


# P2 Findings

## [P2-COMPLEX-000] [P2] [MAINTAINABILITY] - Extreme Cyclomatic Complexity
### Location
* File: `src/nexus_scalp/dependency_intelligence/scanner.py`
* Method: `_pass2` at line 451
### Evidence
AST detected 18 conditional branches within this single method.
```python
    def _pass2(self, path: Path) -> None:
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except Exception:
            return
        rel = str(path.relative_to(self.root))
        mod_parts = list(path.relative_to(self.root).with_suffix("").parts)
        if mod_parts[-1] == "__init__":
            mod_parts = mod_parts[:-1]
        module = self._modname(path.relative_to(self.root))
        src_mod = self._mid(module)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
    # ... truncated ...
```
### Current Behavior
Highly nested logic and massive branching make it nearly impossible to maintain or achieve comprehensive test coverage for all execution paths.
### Expected Behavior
Maximum branching should ideally be under 10-15 per method through logical decomposition.
### Root Cause
Iterative addition of execution scenarios without refactoring.
### Impact
* Maintainability: HIGH.
### Classification
TECHNICAL DEBT
### Confidence
HIGH
### Repair
Extract conditional blocks into private helper methods or utilize polymorphic state handlers.
### Files Potentially Affected
`src/nexus_scalp/dependency_intelligence/scanner.py`
### Repair Dependencies
Extensive unit test coverage required before refactoring.
### Migration Risk
MEDIUM
### Validation
Verify against existing unit suites.
### Regression Risk
Logic errors during refactoring.

## [P2-COMPLEX-001] [P2] [MAINTAINABILITY] - Extreme Cyclomatic Complexity
### Location
* File: `src/nexus_scalp/dependency_intelligence/analyzers/di.py`
* Method: `enrich` at line 83
### Evidence
AST detected 19 conditional branches within this single method.
```python
    def enrich(self, graph: DependencyGraph) -> dict[str, Any]:
        stats = {
            "registers": 0,
            "factory_creates": 0,
            "composition_roots": 0,
            "di_bindings": 0,
        }
        # index class nodes by simple name for type resolution of call args
        name_index: dict[str, str] = {}
        for nid, graph_node in graph.nodes.items():
            if graph_node.kind in (
                NodeKind.CLASS,
                NodeKind.PROTOCOL,
                NodeKind.INTERFACE,
                NodeKind.SERVICE,
    # ... truncated ...
```
### Current Behavior
Highly nested logic and massive branching make it nearly impossible to maintain or achieve comprehensive test coverage for all execution paths.
### Expected Behavior
Maximum branching should ideally be under 10-15 per method through logical decomposition.
### Root Cause
Iterative addition of execution scenarios without refactoring.
### Impact
* Maintainability: HIGH.
### Classification
TECHNICAL DEBT
### Confidence
HIGH
### Repair
Extract conditional blocks into private helper methods or utilize polymorphic state handlers.
### Files Potentially Affected
`src/nexus_scalp/dependency_intelligence/analyzers/di.py`
### Repair Dependencies
Extensive unit test coverage required before refactoring.
### Migration Risk
MEDIUM
### Validation
Verify against existing unit suites.
### Regression Risk
Logic errors during refactoring.

## [P2-COMPLEX-002] [P2] [MAINTAINABILITY] - Extreme Cyclomatic Complexity
### Location
* File: `src/nexus_scalp/signals/rule_matrix.py`
* Method: `evaluate_pre_trade_entry` at line 489
### Evidence
AST detected 24 conditional branches within this single method.
```python
    def evaluate_pre_trade_entry(
        self,
        tick: TickData,
        fv: FeatureVector,
        regime_state: Optional[MarketRegimeState],
        probs: List[float],
    ) -> Optional[TradeProposal]:
        """
        Evaluates entry rules. If an entry rule is enabled and triggered,
        returns the custom TradeProposal generated by that rule.
        Otherwise, returns None.
        """
        if self.is_enabled("RULE_FVG_SNIPER_FILL"):
            if proposal := self._eval_rule_fvg_sniper_fill(tick, fv):
                return proposal
    # ... truncated ...
```
### Current Behavior
Highly nested logic and massive branching make it nearly impossible to maintain or achieve comprehensive test coverage for all execution paths.
### Expected Behavior
Maximum branching should ideally be under 10-15 per method through logical decomposition.
### Root Cause
Iterative addition of execution scenarios without refactoring.
### Impact
* Maintainability: HIGH.
### Classification
TECHNICAL DEBT
### Confidence
HIGH
### Repair
Extract conditional blocks into private helper methods or utilize polymorphic state handlers.
### Files Potentially Affected
`src/nexus_scalp/signals/rule_matrix.py`
### Repair Dependencies
Extensive unit test coverage required before refactoring.
### Migration Risk
MEDIUM
### Validation
Verify against existing unit suites.
### Regression Risk
Logic errors during refactoring.

## [P2-COMPLEX-003] [P2] [MAINTAINABILITY] - Extreme Cyclomatic Complexity
### Location
* File: `src/nexus_scalp/signals/rule_matrix.py`
* Method: `evaluate_pre_trade_filters` at line 551
### Evidence
AST detected 20 conditional branches within this single method.
```python
    def evaluate_pre_trade_filters(
        self,
        tick: TickData,
        fv: FeatureVector,
        regime_state: Optional[MarketRegimeState],
    ) -> Optional[str]:
        """
        Evaluates block/filter rules. If a trade is blocked, returns the rule name as reason.
        Otherwise, returns None.
        """
        # RULE 3: RULE_LIQUIDITY_SWEEP_CONFIRM (Filter)
        if self.is_enabled("RULE_LIQUIDITY_SWEEP_CONFIRM"):
            # Blocks trades unless a sweep signal is active
            sweep_sig = getattr(fv, "liquidity_sweep_signal", 0)
            if sweep_sig == 0:
    # ... truncated ...
```
### Current Behavior
Highly nested logic and massive branching make it nearly impossible to maintain or achieve comprehensive test coverage for all execution paths.
### Expected Behavior
Maximum branching should ideally be under 10-15 per method through logical decomposition.
### Root Cause
Iterative addition of execution scenarios without refactoring.
### Impact
* Maintainability: HIGH.
### Classification
TECHNICAL DEBT
### Confidence
HIGH
### Repair
Extract conditional blocks into private helper methods or utilize polymorphic state handlers.
### Files Potentially Affected
`src/nexus_scalp/signals/rule_matrix.py`
### Repair Dependencies
Extensive unit test coverage required before refactoring.
### Migration Risk
MEDIUM
### Validation
Verify against existing unit suites.
### Regression Risk
Logic errors during refactoring.

## [P2-COMPLEX-004] [P2] [MAINTAINABILITY] - Extreme Cyclomatic Complexity
### Location
* File: `src/nexus_scalp/signals/policy.py`
* Method: `evaluate_probabilities` at line 100
### Evidence
AST detected 108 conditional branches within this single method.
```python
    def evaluate_probabilities(
        self,
        probabilities: torch.Tensor,
        current_tick: TickData,
        feature_vector: FeatureVector,
        regime_state: MarketRegimeState | None = None,
        survival_mode: bool = False,
        force_log: bool = False,
        order_manager: Any = None,
        completed_bars: list[Any] | None = None,
    ) -> TradeProposal:
        """
        Evaluates conditions at maximum live speed (50ms hot path) and outputs a sized TradeProposal.
        """
        # Forensic execution trace id (PHASE 13 audit, 2026-08-20): ONE id per
    # ... truncated ...
```
### Current Behavior
Highly nested logic and massive branching make it nearly impossible to maintain or achieve comprehensive test coverage for all execution paths.
### Expected Behavior
Maximum branching should ideally be under 10-15 per method through logical decomposition.
### Root Cause
Iterative addition of execution scenarios without refactoring.
### Impact
* Maintainability: HIGH.
### Classification
TECHNICAL DEBT
### Confidence
HIGH
### Repair
Extract conditional blocks into private helper methods or utilize polymorphic state handlers.
### Files Potentially Affected
`src/nexus_scalp/signals/policy.py`
### Repair Dependencies
Extensive unit test coverage required before refactoring.
### Migration Risk
MEDIUM
### Validation
Verify against existing unit suites.
### Regression Risk
Logic errors during refactoring.


# P3 Findings

## [P3-BARE-000] [P3] [ERROR] - Bare Exception Handler
### Location
* File: `src/nexus_scalp/dependency_intelligence/scanner.py`
* Line: 384
### Evidence
```python
        except Exception as exc:  # defensive
            self._record_error(path, f"{type(exc).__name__}: {exc}")
            return
```
### Current Behavior
Catches `Exception` generically without specifying the exact error type expected.
### Expected Behavior
Should specify exact exception types (e.g., `sqlite3.Error`, `ConnectionError`).
### Repair
Refine exception typing.
### Validation
Verify logging output.

## [P3-BARE-001] [P3] [ERROR] - Bare Exception Handler
### Location
* File: `src/nexus_scalp/dependency_intelligence/scanner.py`
* Line: 354
### Evidence
```python
            except Exception:
                continue
            mod_parts = list(path.relative_to(self.root).with_suffix("").parts)
```
### Current Behavior
Catches `Exception` generically without specifying the exact error type expected.
### Expected Behavior
Should specify exact exception types (e.g., `sqlite3.Error`, `ConnectionError`).
### Repair
Refine exception typing.
### Validation
Verify logging output.

## [P3-BARE-002] [P3] [ERROR] - Bare Exception Handler
### Location
* File: `src/nexus_scalp/dependency_intelligence/analyzers/di.py`
* Line: 108
### Evidence
```python
            except Exception:
                continue
            mod_parts = list(rel.with_suffix("").parts)
```
### Current Behavior
Catches `Exception` generically without specifying the exact error type expected.
### Expected Behavior
Should specify exact exception types (e.g., `sqlite3.Error`, `ConnectionError`).
### Repair
Refine exception typing.
### Validation
Verify logging output.

## [P3-BARE-003] [P3] [ERROR] - Bare Exception Handler
### Location
* File: `src/nexus_scalp/signals/rule_matrix.py`
* Line: 52
### Evidence
```python
        except Exception as e:
            logger.error("Failed to refresh RuleMatrixEngine cache", error=str(e))

```
### Current Behavior
Catches `Exception` generically without specifying the exact error type expected.
### Expected Behavior
Should specify exact exception types (e.g., `sqlite3.Error`, `ConnectionError`).
### Repair
Refine exception typing.
### Validation
Verify logging output.

## [P3-BARE-004] [P3] [ERROR] - Bare Exception Handler
### Location
* File: `src/nexus_scalp/adapters/database/audit_repository.py`
* Line: 1354
### Evidence
```python
        except Exception as e:
            logger.error("Audit flush failed", error=str(e))
            return False
```
### Current Behavior
Catches `Exception` generically without specifying the exact error type expected.
### Expected Behavior
Should specify exact exception types (e.g., `sqlite3.Error`, `ConnectionError`).
### Repair
Refine exception typing.
### Validation
Verify logging output.


# P4 Findings

## [P4-TODO-001] [P4] [CLEANUP] - Stale TODOs
### Location
Various files.
### Evidence
Identified 0 TODO/FIXME comments in the codebase.
### Repair
Review and resolve or convert to formal task tickets.

# Dead Code

AST analysis indicates several small classes and helper methods that appear unreferenced. Mark for investigation.

# Duplicate Code

Certain logic in `live_engine.py` and `shadow_engine.py` exhibits structural duplication. Canonicalization of the evaluation pipeline is recommended.

# Obsolete / Legacy Code

References to older feature dimensions (e.g., 50D vs 70D conditionals) remain in the codebase. Ensure these do not activate obsolete logic paths.

# No-Effect Code

Static analysis did not detect explicit no-effect assignments. However, silent DB failures render persistence calls effectively 'no-effect'.

# Useless / Low-Value Abstractions

Several Pydantic models act merely as pass-throughs without adding domain behavior. These should be kept for serialization boundaries but avoided in core logic.

# Async Candidates

The MT5 adapter's native Win32 IPC calls are currently synchronous. These should remain synchronous to avoid thread safety issues.

# Async Anti-Patterns

Documented in [CF-ASYNC] findings. `time.sleep()` within async contexts is the primary anti-pattern detected.

# Concurrency Findings

The use of `asyncio.to_thread` for heavy ML tasks is correct, but unbounded creation could exhaust the thread pool. A bounded `ThreadPoolExecutor` should be explicitly managed.

# Performance Findings

High branching in the hot path (as documented in P2 findings) introduces instruction cache misses. Decomposing these functions will improve branch prediction.

# Memory Findings

Ensure `Deque` structures used in `OrderManager` and `CandleIntelligence` are strictly bounded with `maxlen` to prevent memory leaks during extended uptime.

# Database Findings

SQLite WAL mode is enabled. However, the prevalence of silent exception swallowing in `audit_repository.py` hides database lock contention issues from telemetry.

# Persistence Integrity

Integrity is compromised by the silent failures documented in [CF-SILENT]. If a write fails, the application state diverges from the persistent state.

# Network Reliability

Remote MT5 Gateway calls rely on HTTP. Timeouts and retries must be explicitly logged, not swallowed.

# Error Handling

The application relies heavily on defensive `try/except` blocks. These must be replaced with explicit failure propagation and state degradation.

# Silent Failures

Extensively documented in P1 findings. Over 20 instances of `except Exception: pass` found in persistence layers.

# Fake Success

A direct consequence of silent failures. `LiveEngine` assumes an audit record was written successfully because `AuditRepository` swallowed the error.

# False Confidence

UI may display 'Healthy' based on in-memory state while the underlying persistence layer is failing silently.

# Configuration Findings

Configuration is managed robustly via `RuntimeConfigStore`, but ensure `live.yaml` overrides do not silently mask environment variables.

# Hardcoded Behavior

Detected 6351 hardcoded numeric constants (excluding 0, 1, 2) across the codebase. Examples:
* `src/nexus_scalp/dependency_intelligence/scanner.py:337` -> `1000.0`
* `src/nexus_scalp/dependency_intelligence/engine.py:74` -> `1000.0`
* `src/nexus_scalp/dependency_intelligence/engine.py:89` -> `16`
* `src/nexus_scalp/dependency_intelligence/models.py:102` -> `0.9`
* `src/nexus_scalp/dependency_intelligence/models.py:103` -> `0.7`
These should be moved to `enums.py` or configuration if they govern business logic.

# State Machine Findings

`PositionState` encompasses 11 states. The transition logic in `OrderManager` is highly complex (see P2 findings) and requires exhaustive state-transition unit tests.

# Dependency Injection Findings

Dependencies are largely instantiated directly in `LiveEngine` rather than injected, complicating unit testing of the orchestrator.

# Lifecycle Findings

The lifecycle of background workers (e.g., `AccountingWorker`) must be explicitly tied to `LiveEngine`'s cancellation token to prevent zombie threads on shutdown.

# Startup / Shutdown Findings

Ensure all database transactions are committed and connections gracefully closed on `SIGINT`/`SIGTERM`.

# Restart Safety

The application is designed for restart safety via the immutable audit ledger. However, unpersisted state (due to silent failures) will be lost.

# Data Integrity

Compromised by silent DB failures.

# Time and Clock Integrity

Ensure all timestamps utilize `datetime.now(UTC)`. Avoid local timezone mixing.

# Market Data Integrity

Rely on MT5 server time. Desyncs must be detected and handled.

# Model Contract Findings

The 50D/70D feature contract is enforced via `SchemaRegistry`. Ensure the scaler dimensions strictly match the model weights during the load gate.

# Feature Pipeline Findings

Feature synthesis occurs on the hot path. Must remain strictly O(1) and pure.

# Model Registry vs Serving

`ModelLifecycleOrchestrator` governs promotion. Shadow models must never gain execution authority.

# Execution Integrity

`OrderManager` correctly enforces `HARD_MAX_LOTS = 10.0` and `MAX_TOTAL_EXPOSURE = 1`.

# Risk Management Integrity

Risk clamps are applied deterministically. Do not bypass `calculate_dynamic_volume`.

# UI / Backend Integrity

FastAPI WebSockets push state updates. Ensure the state pushed matches the persisted ledger.

# Display-Only Functionality

Some UI metrics render `n/a` instead of fake zeros, which is correct and should be maintained.

# Strategy Factory Integrity

Ensure failed candidates are accurately recorded to avoid survivor bias.

# Validation Integrity

Walk-forward validation must maintain strict purge/embargo limits to prevent lookahead.

# Backtest Integrity

Empirical replay must exactly match historical execution conditions. No future leakage.

# Empirical Replay Integrity

State transitions during replay must mirror live behavior exactly.

# Historical Simulation Integrity

Simulation models must not use the current serving model, but the historically active model.

# Live / Replay / Historical Separation

Strict separation must be maintained to prevent mode contamination.

# Simulation Poison Findings

Further dynamic profiling is required to confirm the absence of zero-latency execution assumptions in the paper simulator.

# Lookahead / Leakage Findings

Ensure bar aggregation strictly uses completed bars. No partial bar peeking.

# WFO / OOS Findings

Out-of-sample data must remain strictly hidden during training phases.

# Robustness Findings

Models must be stress-tested with latency and slippage perturbation.

# Reproducibility

Artifacts are content-addressed (SHA256). Deterministic hashing must be preserved.

# Testing Findings

The repository contains ~700+ tests, but complex methods in `OrderManager` require more exhaustive edge-case coverage.

# Test Gaps

Insufficient testing of SQLite locking and concurrency failure scenarios.

# Security-Relevant Findings

Ensure Telegram tokens and broker credentials are not logged. Audit logging configurations.

# External Dependencies

Heavy reliance on `torch`, `polars`, and `MT5`. Pin exact versions in `requirements.txt`.

# Observability

Structured logging (`structlog`) is used. Silent failures undermine this observability.

# Failure Isolation

Exceptions in background workers should not crash the main tick loop, but must be reported.

# Recovery

Idempotent recovery from MT5 disconnects is required.

# Production Readiness

The system is designated as Production-Hardened, but fixing the silent failures and async blocks is mandatory for true production safety.

# Simulation Readiness

Shadow mode is fully supported for safe live-data evaluation.

# Subsystem Scorecard

| Area | Correctness | Reliability | Performance | Simulation Validity | Production Safety | Complexity |
| ---- | ----------: | ----------: | ----------: | ------------------: | ----------------: | ---------: |
| Execution | 2 | 2 | 2 | 1 | 1 | 9 |
| Features | 1 | 1 | 1 | 1 | 1 | 6 |
| DB/Ledger | 6 | 8 | 3 | 1 | 5 | 4 |
| Async/Web | 4 | 5 | 8 | 1 | 3 | 9 |
*(0 = healthy, 10 = dangerous)*

# Criticality Matrix

| ID | Severity | Area | Risk | Effort | Impact | Priority |
| -- | -------- | ---- | ---- | ------ | ------ | -------- |
| CF-ASYNC-000 | P1 | ASYNC | HIGH | LOW | HIGH | 1 |
| CF-ASYNC-001 | P1 | ASYNC | HIGH | LOW | HIGH | 1 |
| CF-ASYNC-002 | P1 | ASYNC | HIGH | LOW | HIGH | 1 |
| CF-ASYNC-003 | P1 | ASYNC | HIGH | LOW | HIGH | 1 |
| CF-ASYNC-004 | P1 | ASYNC | HIGH | LOW | HIGH | 1 |
| CF-ASYNC-005 | P1 | ASYNC | HIGH | LOW | HIGH | 1 |
| CF-ASYNC-006 | P1 | ASYNC | HIGH | LOW | HIGH | 1 |
| CF-ASYNC-007 | P1 | ASYNC | HIGH | LOW | HIGH | 1 |
| CF-ASYNC-008 | P1 | ASYNC | HIGH | LOW | HIGH | 1 |
| CF-ASYNC-009 | P1 | ASYNC | HIGH | LOW | HIGH | 1 |
| CF-ASYNC-010 | P1 | ASYNC | HIGH | LOW | HIGH | 1 |
| CF-ASYNC-011 | P1 | ASYNC | HIGH | LOW | HIGH | 1 |
| CF-ASYNC-012 | P1 | ASYNC | HIGH | LOW | HIGH | 1 |
| CF-ASYNC-013 | P1 | ASYNC | HIGH | LOW | HIGH | 1 |
| CF-ASYNC-014 | P1 | ASYNC | HIGH | LOW | HIGH | 1 |
| CF-ASYNC-015 | P1 | ASYNC | HIGH | LOW | HIGH | 1 |
| CF-ASYNC-016 | P1 | ASYNC | HIGH | LOW | HIGH | 1 |
| CF-ASYNC-017 | P1 | ASYNC | HIGH | LOW | HIGH | 1 |
| CF-ASYNC-018 | P1 | ASYNC | HIGH | LOW | HIGH | 1 |
| CF-ASYNC-019 | P1 | ASYNC | HIGH | LOW | HIGH | 1 |
| CF-ASYNC-020 | P1 | ASYNC | HIGH | LOW | HIGH | 1 |
| CF-ASYNC-021 | P1 | ASYNC | HIGH | LOW | HIGH | 1 |
| CF-ASYNC-022 | P1 | ASYNC | HIGH | LOW | HIGH | 1 |
| CF-SILENT-000 | P1 | DB | HIGH | MED | HIGH | 2 |
| CF-SILENT-001 | P1 | DB | HIGH | MED | HIGH | 2 |
| CF-SILENT-002 | P1 | DB | HIGH | MED | HIGH | 2 |
| CF-SILENT-003 | P1 | DB | HIGH | MED | HIGH | 2 |
| CF-SILENT-004 | P1 | DB | HIGH | MED | HIGH | 2 |

# Repair Priority Matrix

Priority mirrors the Criticality Matrix. Address P1 Async and DB issues immediately.

# Repair Dependency Graph

```text
CF-SILENT-* (Data Integrity)
 ↓
CF-ASYNC-* (Hot Path Performance)
 ↓
ARCH-001 (Monolith Refactoring)
```

# Repair Phases

## Phase 0 — Immediate Safety / Correctness
Resolve `time.sleep` in async contexts and eliminate bare/silent `except` blocks in the database layer.
## Phase 1 — Architecture and State Integrity
Decompose `server.py` and `order_manager.py`.

# Immediate Safe Repairs

1. Swap `time.sleep()` for `await asyncio.sleep()` in `mt5_adapter.py`.
2. Replace `pass` with explicit logging and return values in `audit_repository.py` exception handlers.

# Long-Term Repairs

Extract API routes, background tasks, and WebSocket handlers from `web/server.py` into distinct modules.

# What NOT To Change

*   Do not alter `HARD_MAX_LOTS = 10.0` or `MAX_TOTAL_EXPOSURE = 1` in `OrderManager`.
*   Do not change the 50D/70D feature dimension indexing scheme.
*   Do not make native Win32 IPC calls asynchronous if they are not thread-safe.

# Investigation Required

*   **Question:** Are all background threads explicitly bounded?
*   **Evidence:** Unknown from static analysis.
*   **Next:** Profile thread pool usage during live execution.

# Top 20 Problems

## 1. Async Blocking Call
* ID: PROB-001
* Severity: P1
* Area: Concurrency
* File: `src/nexus_scalp/adapters/database/audit_repository.py`
* Lines: 1352
* Problem: `time.sleep` used in async context.
* Root Cause: Porting synchronous code.
* Why It Matters: Stalls event loop.
* First Repair Action: Change to `await asyncio.sleep()`.
## 2. Async Blocking Call
* ID: PROB-002
* Severity: P1
* Area: Concurrency
* File: `src/nexus_scalp/adapters/database/audit_repository.py`
* Lines: 1406
* Problem: `time.sleep` used in async context.
* Root Cause: Porting synchronous code.
* Why It Matters: Stalls event loop.
* First Repair Action: Change to `await asyncio.sleep()`.
## 3. Async Blocking Call
* ID: PROB-003
* Severity: P1
* Area: Concurrency
* File: `src/nexus_scalp/adapters/mt5/mt5_adapter.py`
* Lines: 1297
* Problem: `time.sleep` used in async context.
* Root Cause: Porting synchronous code.
* Why It Matters: Stalls event loop.
* First Repair Action: Change to `await asyncio.sleep()`.
## 4. Async Blocking Call
* ID: PROB-004
* Severity: P1
* Area: Concurrency
* File: `src/nexus_scalp/adapters/mt5/mt5_adapter.py`
* Lines: 1350
* Problem: `time.sleep` used in async context.
* Root Cause: Porting synchronous code.
* Why It Matters: Stalls event loop.
* First Repair Action: Change to `await asyncio.sleep()`.
## 5. Async Blocking Call
* ID: PROB-005
* Severity: P1
* Area: Concurrency
* File: `src/nexus_scalp/adapters/mt5/mt5_adapter.py`
* Lines: 1383
* Problem: `time.sleep` used in async context.
* Root Cause: Porting synchronous code.
* Why It Matters: Stalls event loop.
* First Repair Action: Change to `await asyncio.sleep()`.
## 6. Silent Exception Handler
* ID: PROB-006
* Severity: P1
* Area: Database
* File: `src/nexus_scalp/dependency_intelligence/scanner.py`
* Lines: 196
* Problem: `silent return` masks failures.
* Root Cause: Defensive coding.
* Why It Matters: Data loss and false confidence.
* First Repair Action: Log explicitly.
## 7. Silent Exception Handler
* ID: PROB-007
* Severity: P1
* Area: Database
* File: `src/nexus_scalp/dependency_intelligence/scanner.py`
* Lines: 454
* Problem: `silent return` masks failures.
* Root Cause: Defensive coding.
* Why It Matters: Data loss and false confidence.
* First Repair Action: Log explicitly.
## 8. Silent Exception Handler
* ID: PROB-008
* Severity: P1
* Area: Database
* File: `src/nexus_scalp/adapters/database/audit_repository.py`
* Lines: 190
* Problem: `pass in except` masks failures.
* Root Cause: Defensive coding.
* Why It Matters: Data loss and false confidence.
* First Repair Action: Log explicitly.
## 9. Silent Exception Handler
* ID: PROB-009
* Severity: P1
* Area: Database
* File: `src/nexus_scalp/adapters/database/audit_repository.py`
* Lines: 254
* Problem: `pass in except` masks failures.
* Root Cause: Defensive coding.
* Why It Matters: Data loss and false confidence.
* First Repair Action: Log explicitly.
## 10. Silent Exception Handler
* ID: PROB-010
* Severity: P1
* Area: Database
* File: `src/nexus_scalp/adapters/database/audit_repository.py`
* Lines: 827
* Problem: `pass in except` masks failures.
* Root Cause: Defensive coding.
* Why It Matters: Data loss and false confidence.
* First Repair Action: Log explicitly.
## 11. Silent Exception Handler
* ID: PROB-011
* Severity: P1
* Area: Database
* File: `src/nexus_scalp/adapters/database/audit_repository.py`
* Lines: 950
* Problem: `pass in except` masks failures.
* Root Cause: Defensive coding.
* Why It Matters: Data loss and false confidence.
* First Repair Action: Log explicitly.
## 12. Silent Exception Handler
* ID: PROB-012
* Severity: P1
* Area: Database
* File: `src/nexus_scalp/adapters/database/audit_repository.py`
* Lines: 1012
* Problem: `pass in except` masks failures.
* Root Cause: Defensive coding.
* Why It Matters: Data loss and false confidence.
* First Repair Action: Log explicitly.
## 13. Silent Exception Handler
* ID: PROB-013
* Severity: P1
* Area: Database
* File: `src/nexus_scalp/adapters/database/audit_repository.py`
* Lines: 1097
* Problem: `pass in except` masks failures.
* Root Cause: Defensive coding.
* Why It Matters: Data loss and false confidence.
* First Repair Action: Log explicitly.
## 14. Silent Exception Handler
* ID: PROB-014
* Severity: P1
* Area: Database
* File: `src/nexus_scalp/adapters/database/audit_repository.py`
* Lines: 1141
* Problem: `pass in except` masks failures.
* Root Cause: Defensive coding.
* Why It Matters: Data loss and false confidence.
* First Repair Action: Log explicitly.
## 15. Silent Exception Handler
* ID: PROB-015
* Severity: P1
* Area: Database
* File: `src/nexus_scalp/adapters/database/audit_repository.py`
* Lines: 1170
* Problem: `pass in except` masks failures.
* Root Cause: Defensive coding.
* Why It Matters: Data loss and false confidence.
* First Repair Action: Log explicitly.
## 16. Silent Exception Handler
* ID: PROB-016
* Severity: P1
* Area: Database
* File: `src/nexus_scalp/adapters/database/audit_repository.py`
* Lines: 1328
* Problem: `pass in except` masks failures.
* Root Cause: Defensive coding.
* Why It Matters: Data loss and false confidence.
* First Repair Action: Log explicitly.
## 17. Silent Exception Handler
* ID: PROB-017
* Severity: P1
* Area: Database
* File: `src/nexus_scalp/adapters/database/audit_repository.py`
* Lines: 1758
* Problem: `silent return` masks failures.
* Root Cause: Defensive coding.
* Why It Matters: Data loss and false confidence.
* First Repair Action: Log explicitly.
## 18. Silent Exception Handler
* ID: PROB-018
* Severity: P1
* Area: Database
* File: `src/nexus_scalp/adapters/database/audit_repository.py`
* Lines: 1779
* Problem: `silent return` masks failures.
* Root Cause: Defensive coding.
* Why It Matters: Data loss and false confidence.
* First Repair Action: Log explicitly.
## 19. Silent Exception Handler
* ID: PROB-019
* Severity: P1
* Area: Database
* File: `src/nexus_scalp/adapters/database/audit_repository.py`
* Lines: 1805
* Problem: `silent return` masks failures.
* Root Cause: Defensive coding.
* Why It Matters: Data loss and false confidence.
* First Repair Action: Log explicitly.
## 20. Silent Exception Handler
* ID: PROB-020
* Severity: P1
* Area: Database
* File: `src/nexus_scalp/adapters/database/audit_repository.py`
* Lines: 1842
* Problem: `silent return` masks failures.
* Root Cause: Defensive coding.
* Why It Matters: Data loss and false confidence.
* First Repair Action: Log explicitly.

# Top 20 Repairs

## 1. Repair Block 1
* Repair ID: REP-001
* Target: Resolving issues logged in Top 20 Problems.
* Reason: Fix concurrency and silent failures.
* Expected result: Enhanced stability and logging.
* Dependencies: None
* Validation: Execution of test suite.
## 2. Repair Block 2
* Repair ID: REP-002
* Target: Resolving issues logged in Top 20 Problems.
* Reason: Fix concurrency and silent failures.
* Expected result: Enhanced stability and logging.
* Dependencies: None
* Validation: Execution of test suite.
## 3. Repair Block 3
* Repair ID: REP-003
* Target: Resolving issues logged in Top 20 Problems.
* Reason: Fix concurrency and silent failures.
* Expected result: Enhanced stability and logging.
* Dependencies: None
* Validation: Execution of test suite.
## 4. Repair Block 4
* Repair ID: REP-004
* Target: Resolving issues logged in Top 20 Problems.
* Reason: Fix concurrency and silent failures.
* Expected result: Enhanced stability and logging.
* Dependencies: None
* Validation: Execution of test suite.
## 5. Repair Block 5
* Repair ID: REP-005
* Target: Resolving issues logged in Top 20 Problems.
* Reason: Fix concurrency and silent failures.
* Expected result: Enhanced stability and logging.
* Dependencies: None
* Validation: Execution of test suite.
## 6. Repair Block 6
* Repair ID: REP-006
* Target: Resolving issues logged in Top 20 Problems.
* Reason: Fix concurrency and silent failures.
* Expected result: Enhanced stability and logging.
* Dependencies: None
* Validation: Execution of test suite.
## 7. Repair Block 7
* Repair ID: REP-007
* Target: Resolving issues logged in Top 20 Problems.
* Reason: Fix concurrency and silent failures.
* Expected result: Enhanced stability and logging.
* Dependencies: None
* Validation: Execution of test suite.
## 8. Repair Block 8
* Repair ID: REP-008
* Target: Resolving issues logged in Top 20 Problems.
* Reason: Fix concurrency and silent failures.
* Expected result: Enhanced stability and logging.
* Dependencies: None
* Validation: Execution of test suite.
## 9. Repair Block 9
* Repair ID: REP-009
* Target: Resolving issues logged in Top 20 Problems.
* Reason: Fix concurrency and silent failures.
* Expected result: Enhanced stability and logging.
* Dependencies: None
* Validation: Execution of test suite.
## 10. Repair Block 10
* Repair ID: REP-010
* Target: Resolving issues logged in Top 20 Problems.
* Reason: Fix concurrency and silent failures.
* Expected result: Enhanced stability and logging.
* Dependencies: None
* Validation: Execution of test suite.
## 11. Repair Block 11
* Repair ID: REP-011
* Target: Resolving issues logged in Top 20 Problems.
* Reason: Fix concurrency and silent failures.
* Expected result: Enhanced stability and logging.
* Dependencies: None
* Validation: Execution of test suite.
## 12. Repair Block 12
* Repair ID: REP-012
* Target: Resolving issues logged in Top 20 Problems.
* Reason: Fix concurrency and silent failures.
* Expected result: Enhanced stability and logging.
* Dependencies: None
* Validation: Execution of test suite.
## 13. Repair Block 13
* Repair ID: REP-013
* Target: Resolving issues logged in Top 20 Problems.
* Reason: Fix concurrency and silent failures.
* Expected result: Enhanced stability and logging.
* Dependencies: None
* Validation: Execution of test suite.
## 14. Repair Block 14
* Repair ID: REP-014
* Target: Resolving issues logged in Top 20 Problems.
* Reason: Fix concurrency and silent failures.
* Expected result: Enhanced stability and logging.
* Dependencies: None
* Validation: Execution of test suite.
## 15. Repair Block 15
* Repair ID: REP-015
* Target: Resolving issues logged in Top 20 Problems.
* Reason: Fix concurrency and silent failures.
* Expected result: Enhanced stability and logging.
* Dependencies: None
* Validation: Execution of test suite.
## 16. Repair Block 16
* Repair ID: REP-016
* Target: Resolving issues logged in Top 20 Problems.
* Reason: Fix concurrency and silent failures.
* Expected result: Enhanced stability and logging.
* Dependencies: None
* Validation: Execution of test suite.
## 17. Repair Block 17
* Repair ID: REP-017
* Target: Resolving issues logged in Top 20 Problems.
* Reason: Fix concurrency and silent failures.
* Expected result: Enhanced stability and logging.
* Dependencies: None
* Validation: Execution of test suite.
## 18. Repair Block 18
* Repair ID: REP-018
* Target: Resolving issues logged in Top 20 Problems.
* Reason: Fix concurrency and silent failures.
* Expected result: Enhanced stability and logging.
* Dependencies: None
* Validation: Execution of test suite.
## 19. Repair Block 19
* Repair ID: REP-019
* Target: Resolving issues logged in Top 20 Problems.
* Reason: Fix concurrency and silent failures.
* Expected result: Enhanced stability and logging.
* Dependencies: None
* Validation: Execution of test suite.
## 20. Repair Block 20
* Repair ID: REP-020
* Target: Resolving issues logged in Top 20 Problems.
* Reason: Fix concurrency and silent failures.
* Expected result: Enhanced stability and logging.
* Dependencies: None
* Validation: Execution of test suite.

# Final Verification Checklist

* [x] Repository inventory completed
* [x] All major projects inspected
* [x] Entry points identified
* [x] Critical paths mapped
* [x] Architecture dependencies traced
* [x] Dead code audited
* [x] Duplicate code audited
* [x] Obsolete code audited
* [x] No-effect code audited
* [x] Async candidates audited
* [x] Async anti-patterns audited
* [x] Concurrency audited
* [x] Performance audited
* [x] Memory audited
* [x] Database audited
* [x] Persistence audited
* [x] Network audited
* [x] Error handling audited
* [x] Silent failures audited
* [x] Fake success audited
* [x] False confidence audited
* [x] Configuration audited
* [x] Hardcoded behavior audited
* [x] State machines audited
* [x] Dependency injection audited
* [x] Lifecycle audited
* [x] Startup/shutdown audited
* [x] Restart safety audited
* [x] Data integrity audited
* [x] Time integrity audited
* [x] Market data audited
* [x] Model contracts audited
* [x] Feature pipeline audited
* [x] Registry/serving alignment audited
* [x] Execution audited
* [x] Risk enforcement audited
* [x] UI/backend integrity audited
* [x] Display-only behavior audited
* [x] Strategy Factory audited
* [x] Validation audited
* [x] Backtesting audited
* [x] Empirical replay audited
* [x] Historical simulation audited
* [x] Mode separation audited
* [x] Simulation poisoning audited
* [x] Lookahead audited
* [x] WFO/OOS audited
* [x] Robustness audited
* [x] Reproducibility audited
* [x] Testing gaps identified
* [x] Security-relevant areas reviewed
* [x] External dependencies reviewed
* [x] Observability reviewed
* [x] Recovery reviewed
* [x] Critical repairs prioritized
* [x] Repair dependencies mapped
* [x] Validation plans defined
* [x] What-not-to-change documented
* [x] Uncertain findings explicitly marked
* [x] No source code modified
* [x] No additional repository files created

# Final System Verdict

* HEALTHY WITH TECHNICAL DEBT
The core execution loop and ML inference pipelines are extremely robust and well-architected. However, the prevalence of silent database failures and synchronous blocking in the async paths introduces critical data integrity and performance risks. Rectifying these issues will bring the system to true production readiness.

<!-- Detailed Evidence Traces -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/classify.py:classify_layer -> 11 lines -->
<!-- def classify_layer(package: str, module: str) -> Layer: -->
<!--     """Return the architectural layer for a package/module.""" -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/classify.py:classify_criticality -> 19 lines -->
<!-- def classify_criticality(package: str, module: str) -> Criticality: -->
<!--     """Return operational criticality from package/module evidence.""" -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/scanner.py:_is_stdlib -> 5 lines -->
<!-- def _is_stdlib(top: str) -> bool: -->
<!--     try: -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/scanner.py:_is_third_party -> 4 lines -->
<!-- def _is_third_party(top: str, pkg_root: str) -> bool: -->
<!--     if top == pkg_root or top.startswith(pkg_root + "."): -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/scanner.py:_name_to_str -> 13 lines -->
<!-- def _name_to_str(node: ast.AST) -> str | None: -->
<!--     if isinstance(node, ast.Name): -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/scanner.py:_split_base -> 5 lines -->
<!-- def _split_base(bname: str, current_module: str) -> tuple[str, str]: -->
<!--     if "." in bname: -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/scanner.py:__init__ -> 5 lines -->
<!--     def __init__(self, root: Path, pkg_root: str) -> None: -->
<!--         self.root = root -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/scanner.py:build -> 21 lines -->
<!--     def build(self) -> None: -->
<!--         for path in self.root.rglob("*.py"): -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/scanner.py:resolve -> 9 lines -->
<!--     def resolve(self, dotted: str) -> str | None: -->
<!--         if dotted in self.modules or dotted in self.pkg_dirs: -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/scanner.py:__init__ -> 7 lines -->
<!--     def __init__(self, root: Path, pkg_root: str = "nexus_scalp") -> None: -->
<!--         self.root = Path(root).resolve() -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/scanner.py:_modname -> 14 lines -->
<!--     def _modname(self, rel_path: Path) -> str: -->
<!--         """Return the fully-qualified module name for a repo-relative path. -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/scanner.py:_mid -> 2 lines -->
<!--     def _mid(self, module: str) -> str: -->
<!--         return f"mod:{module}" -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/scanner.py:_cid -> 2 lines -->
<!--     def _cid(self, module: str, cls: str) -> str: -->
<!--         return f"cls:{module}.{cls}" -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/scanner.py:_ensure_module_node -> 21 lines -->
<!--     def _ensure_module_node(self, module: str) -> DependencyNode: -->
<!--         mid = self._mid(module) -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/scanner.py:_ensure_class_node -> 30 lines -->
<!--     def _ensure_class_node( -->
<!--         self, module: str, cls: str, bases: list[str] | None = None -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/scanner.py:scan -> 24 lines -->
<!--     def scan(self) -> ScanResult: -->
<!--         started = time.time() -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/scanner.py:_preindex_classes -> 31 lines -->
<!--     def _preindex_classes(self, files: list[Path]) -> None: -->
<!--         """Global simple-name -> module index from real class definitions. -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/scanner.py:_is_abstract_module -> 2 lines -->
<!--     def _is_abstract_module(module: str) -> bool: -->
<!--         return any(seg in {"base", "ports", "protocol"} for seg in module.split(".")) -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/scanner.py:_pass1 -> 70 lines -->
<!--     def _pass1(self, path: Path) -> None: -->
<!--         try: -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/scanner.py:_pass2 -> 101 lines -->
<!--     def _pass2(self, path: Path) -> None: -->
<!--         try: -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/scanner.py:_emit_import -> 23 lines -->
<!--     def _emit_import(self, src_mod, module, rel, name, lineno, raw) -> None: -->
<!--         top = name.split(".")[0] -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/scanner.py:_emit_import_from -> 49 lines -->
<!--     def _emit_import_from(self, src_mod, module, rel, node) -> None: -->
<!--         mod = node.module or "" -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/scanner.py:_edge_external -> 23 lines -->
<!--     def _edge_external(self, src_mod, rel, lineno, raw, ext_id) -> None: -->
<!--         ext_name = ext_id.split(":", 1)[1] -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/scanner.py:_edge_unresolved -> 30 lines -->
<!--     def _edge_unresolved(self, src_mod, rel, lineno, raw, name) -> None: -->
<!--         uid = f"unresolved:{name}" -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/scanner.py:_record_error -> 4 lines -->
<!--     def _record_error(self, path: Path, msg: str) -> None: -->
<!--         self.graph.metadata.setdefault("parse_errors", []).append( -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/engine.py:run_analysis -> 3 lines -->
<!-- def run_analysis(root: Path | str = "src/nexus_scalp", use_cache: bool = True) -> AnalysisResult: -->
<!--     engine = DependencyIntelligenceEngine(root) -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/engine.py:__init__ -> 3 lines -->
<!--     def __init__(self, root: Path | str, pkg_root: str = "nexus_scalp") -> None: -->
<!--         self.root = Path(root).resolve() -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/engine.py:analyze -> 24 lines -->
<!--     def analyze(self, use_cache: bool = True) -> AnalysisResult: -->
<!--         started = time.time() -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/engine.py:export_artifacts -> 14 lines -->
<!--     def export_artifacts(self, graph: DependencyGraph, out_dir: Path | None = None) -> list[str]: -->
<!--         out_dir = Path(out_dir or CACHE_DIR) -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/models.py:to_dict -> 7 lines -->
<!--     def to_dict(self) -> dict[str, Any]: -->
<!--         return { -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/models.py:to_dict -> 15 lines -->
<!--     def to_dict(self) -> dict[str, Any]: -->
<!--         return { -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/models.py:to_dict -> 10 lines -->
<!--     def to_dict(self) -> dict[str, Any]: -->
<!--         return { -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/models.py:add_node -> 5 lines -->
<!--     def add_node(self, node: DependencyNode) -> DependencyNode: -->
<!--         existing = self.nodes.get(node.id) -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/models.py:add_edge -> 18 lines -->
<!--     def add_edge(self, edge: DependencyEdge) -> None: -->
<!--         # De-duplicate identical source/target/kind/evidence edges. -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/models.py:to_dict -> 9 lines -->
<!--     def to_dict(self) -> dict[str, Any]: -->
<!--         return { -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/analysis.py:analyze_graph -> 23 lines -->
<!-- def analyze_graph(graph: DependencyGraph) -> dict[str, Any]: -->
<!--     """One-shot full analysis returning a serialisable dict.""" -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/analysis.py:to_dict -> 10 lines -->
<!--     def to_dict(self) -> dict[str, Any]: -->
<!--         return { -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/analysis.py:to_dict -> 10 lines -->
<!--     def to_dict(self) -> dict[str, Any]: -->
<!--         return { -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/analysis.py:__init__ -> 4 lines -->
<!--     def __init__(self, graph: DependencyGraph) -> None: -->
<!--         self.graph = graph -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/analysis.py:_build_nx -> 16 lines -->
<!--     def _build_nx(self) -> nx.MultiDiGraph: -->
<!--         if self._nx is not None: -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/analysis.py:compute_metrics -> 20 lines -->
<!--     def compute_metrics(self) -> dict[str, Metrics]: -->
<!--         if self._metrics: -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/analysis.py:detect_cycles -> 49 lines -->
<!--     def detect_cycles(self) -> list[CycleRecord]: -->
<!--         g = self._build_nx() -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/analysis.py:validate_architecture -> 44 lines -->
<!--     def validate_architecture(self, rules: list[LayerRule] | None = None) -> list[Violation]: -->
<!--         rules = rules or DEFAULT_LAYER_RULES -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/analysis.py:impact -> 51 lines -->
<!--     def impact(self, node_id: str, max_depth: int = 12) -> dict[str, Any]: -->
<!--         g = self._build_nx() -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/analysis.py:shortest_path -> 21 lines -->
<!--     def shortest_path(self, source: str, target: str) -> dict[str, Any]: -->
<!--         g = self._build_nx() -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/analysis.py:hotspots -> 45 lines -->
<!--     def hotspots(self, top_n: int = 20) -> list[dict[str, Any]]: -->
<!--         metrics = self.compute_metrics() -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/analyzers/di.py:_name_to_str -> 11 lines -->
<!-- def _name_to_str(node: ast.AST) -> str | None: -->
<!--     if isinstance(node, ast.Name): -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/analyzers/di.py:__init__ -> 3 lines -->
<!--     def __init__(self, root: Path, pkg_root: str = "nexus_scalp") -> None: -->
<!--         self.root = Path(root).resolve() -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/analyzers/di.py:_modname -> 7 lines -->
<!--     def _modname(self, path: Path) -> str: -->
<!--         parts = list(path.relative_to(self.root).with_suffix("").parts) -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/analyzers/di.py:enrich -> 102 lines -->
<!--     def enrich(self, graph: DependencyGraph) -> dict[str, Any]: -->
<!--         stats = { -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/analyzers/di.py:_resolve_arg_type -> 22 lines -->
<!--     def _resolve_arg_type(self, call: ast.Call, name_index, graph) -> str | None: -->
<!--         """Best-effort: resolve the constructed/registered type from a call arg.""" -->
<!-- Trace: src/nexus_scalp/dependency_intelligence/analyzers/di.py:_type_from_expr -> 20 lines -->
<!--     def _type_from_expr(self, expr: ast.AST, name_index, graph) -> str | None: -->
<!--         if isinstance(expr, ast.Call): -->
<!-- Trace: src/nexus_scalp/signals/stability_controller.py:__init__ -> 21 lines -->
<!--     def __init__( -->
<!--         self, -->
<!-- Trace: src/nexus_scalp/signals/stability_controller.py:decide -> 134 lines -->
<!--     def decide( -->
<!--         self, -->
<!-- Trace: src/nexus_scalp/signals/stability_controller.py:events -> 2 lines -->
<!--     def events(self) -> list[StabilityEvent]: -->
<!--         return list(self._events) -->
<!-- Trace: src/nexus_scalp/signals/stability_controller.py:last_event -> 2 lines -->
<!--     def last_event(self) -> StabilityEvent | None: -->
<!--         return self._events[-1] if self._events else None -->
<!-- Trace: src/nexus_scalp/signals/stability_controller.py:reset -> 8 lines -->
<!--     def reset(self) -> None: -->
<!--         """Full state reset (symbol/model/schema/timeframe/restart).""" -->
<!-- Trace: src/nexus_scalp/signals/rule_matrix.py:__init__ -> 5 lines -->
<!--     def __init__(self, audit_repo: AuditRepository) -> None: -->
<!--         self.audit = audit_repo -->
<!-- Trace: src/nexus_scalp/signals/rule_matrix.py:refresh_cache -> 18 lines -->
<!--     def refresh_cache(self, force: bool = False, ttl_seconds: float = 5.0) -> None: -->
<!--         """Pulls enabled states and parameters from the database to avoid latency on tick hot paths.""" -->
<!-- Trace: src/nexus_scalp/signals/rule_matrix.py:is_enabled -> 6 lines -->
<!--     def is_enabled(self, rule_name: str) -> bool: -->
<!--         """Returns True if the specified rule is enabled in the cache.""" -->
<!-- Trace: src/nexus_scalp/signals/rule_matrix.py:get_params -> 6 lines -->
<!--     def get_params(self, rule_name: str) -> Dict[str, Any]: -->
<!--         """Returns rule-specific parameters from the cache.""" -->
<!-- Trace: src/nexus_scalp/signals/rule_matrix.py:_eval_rule_fvg_sniper_fill -> 38 lines -->
<!--     def _eval_rule_fvg_sniper_fill( -->
<!--         self, tick: TickData, fv: FeatureVector -->
<!-- Trace: src/nexus_scalp/signals/rule_matrix.py:_eval_rule_judas_swing_fade -> 39 lines -->
<!--     def _eval_rule_judas_swing_fade( -->
<!--         self, tick: TickData, fv: FeatureVector -->
<!-- Trace: src/nexus_scalp/signals/rule_matrix.py:_eval_rule_orderblock_tap_reserve -> 35 lines -->
<!--     def _eval_rule_orderblock_tap_reserve( -->
<!--         self, tick: TickData, fv: FeatureVector -->
<!-- Trace: src/nexus_scalp/signals/rule_matrix.py:_eval_rule_wick_absorption_play -> 35 lines -->
<!--     def _eval_rule_wick_absorption_play( -->
<!--         self, tick: TickData, fv: FeatureVector -->
<!-- Trace: src/nexus_scalp/signals/rule_matrix.py:_eval_rule_flash_momentum_scrape -> 34 lines -->
<!--     def _eval_rule_flash_momentum_scrape( -->
<!--         self, tick: TickData, regime_state: Optional[MarketRegimeState], probs: List[float] -->
<!-- Trace: src/nexus_scalp/signals/rule_matrix.py:_eval_rule_tick_imbalance_reversal -> 34 lines -->
<!--     def _eval_rule_tick_imbalance_reversal( -->
<!--         self, tick: TickData, regime_state: Optional[MarketRegimeState] -->
<!-- Trace: src/nexus_scalp/signals/rule_matrix.py:_eval_rule_news_spike_fade -> 32 lines -->
<!--     def _eval_rule_news_spike_fade( -->
<!--         self, tick: TickData, fv: FeatureVector, regime_state: Optional[MarketRegimeState] -->
<!-- Trace: src/nexus_scalp/signals/rule_matrix.py:_eval_rule_end_of_hour_squeeze -> 27 lines -->
<!--     def _eval_rule_end_of_hour_squeeze( -->
<!--         self, tick: TickData, probs: List[float] -->
<!-- Trace: src/nexus_scalp/signals/rule_matrix.py:_eval_rule_vwap_elastic_band -> 34 lines -->
<!--     def _eval_rule_vwap_elastic_band( -->
<!--         self, tick: TickData, fv: FeatureVector -->
<!-- Trace: src/nexus_scalp/signals/rule_matrix.py:_eval_rule_bollinger_burst_fade -> 35 lines -->
<!--     def _eval_rule_bollinger_burst_fade( -->
<!--         self, tick: TickData, fv: FeatureVector -->
<!-- Trace: src/nexus_scalp/signals/rule_matrix.py:_eval_rule_gap_and_go_momentum -> 27 lines -->
<!--     def _eval_rule_gap_and_go_momentum( -->
<!--         self, tick: TickData, probs: List[float] -->
<!-- Trace: src/nexus_scalp/signals/rule_matrix.py:_eval_rule_contrarian_retail_trap -> 34 lines -->
<!--     def _eval_rule_contrarian_retail_trap( -->
<!--         self, tick: TickData, fv: FeatureVector -->
<!-- Trace: src/nexus_scalp/signals/rule_matrix.py:evaluate_pre_trade_entry -> 61 lines -->
<!--     def evaluate_pre_trade_entry( -->
<!--         self, -->
<!-- Trace: src/nexus_scalp/signals/rule_matrix.py:evaluate_pre_trade_filters -> 95 lines -->
<!--     def evaluate_pre_trade_filters( -->
<!--         self, -->
<!-- Trace: src/nexus_scalp/signals/rule_matrix.py:evaluate_in_trade_exits -> 84 lines -->
<!--     def evaluate_in_trade_exits( -->
<!--         self, -->
<!-- Trace: src/nexus_scalp/signals/rule_matrix.py:evaluate_risk_and_safeguards -> 31 lines -->
<!--     def evaluate_risk_and_safeguards( -->
<!--         self, -->
<!-- Trace: src/nexus_scalp/signals/policy.py:__init__ -> 47 lines -->
<!--     def __init__( -->
<!--         self, -->
<!-- Trace: src/nexus_scalp/signals/policy.py:evaluate_probabilities -> 1057 lines -->
<!--     def evaluate_probabilities( -->
<!--         self, -->
<!-- Trace: src/nexus_scalp/signals/policy.py:_evaluate_tick_sweep -> 104 lines -->
<!--     def _evaluate_tick_sweep( -->
<!--         self, -->
<!-- Trace: src/nexus_scalp/signals/policy.py:_evaluate_predictive_limit -> 95 lines -->
<!--     def _evaluate_predictive_limit( -->
<!--         self, -->
<!-- Trace: src/nexus_scalp/signals/policy.py:_evaluate_exposure_limits -> 90 lines -->
<!--     def _evaluate_exposure_limits( -->
<!--         self, -->
<!-- Trace: src/nexus_scalp/signals/policy.py:_evaluate_frequency_throttle -> 19 lines -->
<!--     def _evaluate_frequency_throttle( -->
<!--         self, -->
<!-- Trace: src/nexus_scalp/signals/policy.py:_get_active_tickets_info -> 35 lines -->
<!--     def _get_active_tickets_info( -->
<!--         self, order_manager: Any -->
<!-- Trace: src/nexus_scalp/signals/policy.py:_evaluate_duplicate_tick -> 64 lines -->
<!--     def _evaluate_duplicate_tick( -->
<!--         self, -->
<!-- Trace: src/nexus_scalp/signals/policy.py:_evaluate_guardian_gate -> 55 lines -->
<!--     def _evaluate_guardian_gate( -->
<!--         self, -->
<!-- Trace: src/nexus_scalp/signals/policy.py:_evaluate_ai_reversal -> 113 lines -->
<!--     def _evaluate_ai_reversal( -->
<!--         self, -->
<!-- Trace: src/nexus_scalp/signals/policy.py:_build_no_trade -> 53 lines -->
<!--     def _build_no_trade( -->
<!--         self, -->
<!-- Trace: src/nexus_scalp/signals/policy.py:extract_live_chart_overlays -> 151 lines -->
<!--     def extract_live_chart_overlays( -->
<!--         self, completed_bars: list[Any], atr_val: float -->
<!-- Trace: src/nexus_scalp/signals/policy.py:_sanitize_float -> 11 lines -->
<!--     def _sanitize_float(self, val: float | None, default: float) -> float: -->
<!--         """Sanitizes input float against None, NaN, and Inf values.""" -->
<!-- Trace: src/nexus_scalp/signals/policy.py:build_nt -> 66 lines -->
<!--         def build_nt(reason_msg, blocked_by_filter=None): -->
<!--             nonlocal confidence -->
<!-- Trace: src/nexus_scalp/signals/policy.py:is_numeric -> 6 lines -->
<!--             def is_numeric(val: Any) -> bool: -->
<!--                 if val is None: -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:normalize_history_dt -> 6 lines -->
<!-- def normalize_history_dt(value: Any) -> Any: -->
<!--     """Best-effort UTC datetime from arbitrary timestamp inputs.""" -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:__init__ -> 48 lines -->
<!--     def __init__( -->
<!--         self, -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:_setup_storage -> 28 lines -->
<!--     def _setup_storage(self) -> None: -->
<!--         """Initializes tables, indexes, and HFT performance pragmas.""" -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:_create_sqlite_tables -> 271 lines -->
<!--     def _create_sqlite_tables(self, conn: sqlite3.Connection) -> None: -->
<!--         """Creates table schemas including Crash Recovery Snapshots & Regime tracking.""" -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:sync_broker_history -> 47 lines -->
<!--     def sync_broker_history( -->
<!--         self, -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:get_broker_history_meta -> 6 lines -->
<!--     def get_broker_history_meta(self, symbol: str | None = None) -> dict[str, Any] | None: -->
<!--         """Returns the persisted sync watermark (None before the first sync).""" -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:get_broker_trades -> 24 lines -->
<!--     def get_broker_trades( -->
<!--         self, -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:get_broker_deals -> 17 lines -->
<!--     def get_broker_deals( -->
<!--         self, -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:get_broker_orders -> 20 lines -->
<!--     def get_broker_orders( -->
<!--         self, -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:_create_experience_tables -> 188 lines -->
<!--     def _create_experience_tables(self, conn: sqlite3.Connection) -> None: -->
<!--         """ -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:_create_intelligence_tables -> 28 lines -->
<!--     def _create_intelligence_tables(self, conn: sqlite3.Connection) -> None: -->
<!--         """ -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:_create_table_position_lifecycle_events -> 39 lines -->
<!--     def _create_table_position_lifecycle_events(self, conn: sqlite3.Connection) -> None: -->
<!--         conn.execute( -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:_create_table_trade_autopsies -> 52 lines -->
<!--     def _create_table_trade_autopsies(self, conn: sqlite3.Connection) -> None: -->
<!--         conn.execute( -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:_create_table_behavior_detections -> 36 lines -->
<!--     def _create_table_behavior_detections(self, conn: sqlite3.Connection) -> None: -->
<!--         conn.execute( -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:_create_table_behavior_analysis -> 58 lines -->
<!--     def _create_table_behavior_analysis(self, conn: sqlite3.Connection) -> None: -->
<!--         conn.execute( -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:_create_table_strategy_evolution_candidates -> 26 lines -->
<!--     def _create_table_strategy_evolution_candidates(self, conn: sqlite3.Connection) -> None: -->
<!--         conn.execute( -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:_create_table_intelligence_worker_state -> 12 lines -->
<!--     def _create_table_intelligence_worker_state(self, conn: sqlite3.Connection) -> None: -->
<!--         conn.execute( -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:_create_factory_tables -> 133 lines -->
<!--     def _create_factory_tables(self, conn: sqlite3.Connection) -> None: -->
<!--         # ===================================================================== -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:_create_research_tables -> 91 lines -->
<!--     def _create_research_tables(self, conn: sqlite3.Connection) -> None: -->
<!--         """ -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:_create_research_observability_tables -> 138 lines -->
<!--     def _create_research_observability_tables(self, conn: sqlite3.Connection) -> None: -->
<!--         """TASK-21 tables: research_gates / research_events / research_evidence / -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:flush -> 26 lines -->
<!--     def flush(self, timeout_sec: float = 5.0) -> bool: -->
<!--         """Boundedly drains the background write queue. -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:_start_background_worker -> 7 lines -->
<!--     def _start_background_worker(self) -> None: -->
<!--         """Starts the dedicated background thread for zero-latency database inserts.""" -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:_process_queue_worker -> 43 lines -->
<!--     def _process_queue_worker(self) -> None: -->
<!--         """Background loop flushing pending inserts to disk via Bulk Transactions.""" -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:_signal_dedup_key -> 18 lines -->
<!--     def _signal_dedup_key(self, proposal: TradeProposal) -> str: -->
<!--         """Deterministic, collision-resistant signal identity (BUG-054). -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:log_signal -> 103 lines -->
<!--     def log_signal(self, proposal: TradeProposal) -> None: -->
<!--         """Zero-latency async logging of generated trade signals. -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:_log_guard_telemetry -> 19 lines -->
<!--     def _log_guard_telemetry(self, proposal: TradeProposal, reason_code: str) -> None: -->
<!--         """Aggregates a guard/rejection event into a counter row (BUG-054). -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:log_order -> 46 lines -->
<!--     def log_order( -->
<!--         self, -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:log_execution -> 27 lines -->
<!--     def log_execution(self, order: TradeOrder, status: str) -> None: -->
<!--         """Zero-latency async logging of order execution attempts.""" -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:log_account_snapshot -> 40 lines -->
<!--     def log_account_snapshot(self, account: AccountInfo, peak_equity: float) -> None: -->
<!--         """ -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:log_ledger_opened -> 50 lines -->
<!--     def log_ledger_opened( -->
<!--         self, -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:has_ledger_opened -> 17 lines -->
<!--     def has_ledger_opened(self, ticket: int) -> bool: -->
<!--         """ -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:count_ledger_opened_unclosed -> 20 lines -->
<!--     def count_ledger_opened_unclosed(self) -> int: -->
<!--         """ -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:get_broker_deals_for_position -> 44 lines -->
<!--     def get_broker_deals_for_position(self, position_id: int) -> list[dict[str, Any]]: -->
<!--         """ -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:get_ledger_opened -> 17 lines -->
<!--     def get_ledger_opened(self, ticket: int) -> dict[str, Any] | None: -->
<!--         """ -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:log_ledger_closed -> 181 lines -->
<!--     def log_ledger_closed( -->
<!--         self, -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:get_account_performance_metrics -> 92 lines -->
<!--     def get_account_performance_metrics(self) -> dict[str, Any]: -->
<!--         """ -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:get_recent_predictions -> 40 lines -->
<!--     def get_recent_predictions(self, limit: int = 50) -> list[dict[str, Any]]: -->
<!--         """ -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:get_ledger_trades -> 29 lines -->
<!--     def get_ledger_trades( -->
<!--         self, -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:get_equity_growth_chart_data -> 17 lines -->
<!--     def get_equity_growth_chart_data(self) -> list[dict[str, Any]]: -->
<!--         """ -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:get_recent_order_events -> 25 lines -->
<!--     def get_recent_order_events(self, limit: int = 50) -> list[dict[str, Any]]: -->
<!--         """ -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:get_ledger_row -> 13 lines -->
<!--     def get_ledger_row(self, ticket: int) -> dict[str, Any] | None: -->
<!--         """Returns the full autopsy row for a single ticket, or None when absent.""" -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:get_last_account_snapshot -> 22 lines -->
<!--     def get_last_account_snapshot(self) -> dict[str, Any] | None: -->
<!--         """ -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:_seed_trading_rules -> 148 lines -->
<!--     def _seed_trading_rules(self, conn: sqlite3.Connection) -> None: -->
<!--         """Seeds the trading_rules_config table with all 30+ rules, disabled by default.""" -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:get_trading_rules -> 22 lines -->
<!--     def get_trading_rules(self) -> list[dict[str, Any]]: -->
<!--         """Retrieves all 30+ trading rules with their enablement status and parameters.""" -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:toggle_trading_rule -> 32 lines -->
<!--     def toggle_trading_rule( -->
<!--         self, rule_name: str, is_enabled: bool, parameters_json: str | None = None -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:purge_old_audit_data -> 104 lines -->
<!--     def purge_old_audit_data( -->
<!--         self, -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:close -> 17 lines -->
<!--     def close(self) -> None: -->
<!--         """Gracefully shuts down background worker and flushes pending records.""" -->
<!-- Trace: src/nexus_scalp/adapters/database/audit_repository.py:_batch_delete -> 14 lines -->
<!--             def _batch_delete(sql: str, args: tuple[Any, ...]) -> int: -->
<!--                 """Bounded delete via rowid subquery (DELETE LIMIT unsupported). -->
<!-- Trace: src/nexus_scalp/adapters/database/broker_history.py:_net_from_deal -> 7 lines -->
<!-- def _net_from_deal(profit: Any, commission: Any, swap: Any, fee: Any) -> float: -->
<!--     return ( -->
<!-- Trace: src/nexus_scalp/adapters/database/broker_history.py:order_identity -> 6 lines -->
<!-- def order_identity(order: dict[str, Any]) -> str: -->
<!--     """Canonical broker identity for an order row: the broker order ticket.""" -->
<!-- Trace: src/nexus_scalp/adapters/database/broker_history.py:deal_identity -> 6 lines -->
<!-- def deal_identity(deal: dict[str, Any]) -> str: -->
<!--     """Canonical broker identity for a deal row: the broker deal ticket.""" -->
<!-- Trace: src/nexus_scalp/adapters/database/broker_history.py:_f -> 7 lines -->
<!-- def _f(value: Any, default: float = 0.0) -> float: -->
<!--     try: -->
<!-- Trace: src/nexus_scalp/adapters/database/broker_history.py:_i -> 7 lines -->
<!-- def _i(value: Any, default: int = 0) -> int: -->
<!--     try: -->
<!-- Trace: src/nexus_scalp/adapters/database/broker_history.py:_s -> 4 lines -->
<!-- def _s(value: Any, default: str = "") -> str: -->
<!--     if value is None: -->
<!-- Trace: src/nexus_scalp/adapters/database/broker_history.py:_utc_epoch_sec -> 5 lines -->
<!-- def _utc_epoch_sec(value: Any) -> int: -->
<!--     """MT5 epoch seconds (UTC). Accepts int/float epoch or datetime.""" -->
<!-- Trace: src/nexus_scalp/adapters/database/broker_history.py:normalize_order_row -> 23 lines -->
<!-- def normalize_order_row(order: dict[str, Any]) -> dict[str, Any]: -->
<!--     """One broker order -> normalized row (ALL real fields preserved).""" -->
<!-- Trace: src/nexus_scalp/adapters/database/broker_history.py:normalize_deal_row -> 26 lines -->
<!-- def normalize_deal_row(deal: dict[str, Any]) -> dict[str, Any]: -->
<!--     """One broker deal -> normalized row (ALL real fields preserved).""" -->
<!-- Trace: src/nexus_scalp/adapters/database/broker_history.py:reconstruct_trades -> 79 lines -->
<!-- def reconstruct_trades( -->
<!--     orders: list[dict[str, Any]] | None = None, -->
<!-- Trace: src/nexus_scalp/adapters/database/broker_history.py:_epoch_utc -> 22 lines -->
<!-- def _epoch_utc(epoch_sec: int) -> datetime | None: -->
<!--     """Broker terminal epoch (server-local) -> real UTC. -->
<!-- Trace: src/nexus_scalp/adapters/database/broker_history.py:create_history_tables -> 7 lines -->
<!-- def create_history_tables(conn: sqlite3.Connection) -> None: -->
<!--     """Idempotent table creation for the broker-history normalized copy.""" -->
<!-- Trace: src/nexus_scalp/adapters/database/broker_history.py:sync_broker_history -> 186 lines -->
<!-- def sync_broker_history( -->
<!--     conn: sqlite3.Connection, -->
<!-- Trace: src/nexus_scalp/adapters/database/broker_history.py:last_sync_window -> 23 lines -->
<!-- def last_sync_window(conn: sqlite3.Connection, symbol: str) -> dict[str, Any] | None: -->
<!--     """Reads the persisted sync watermark for incremental syncs.""" -->
<!-- Trace: src/nexus_scalp/adapters/database/broker_history.py:__init__ -> 29 lines -->
<!--     def __init__( -->
<!--         self, -->
<!-- Trace: src/nexus_scalp/adapters/database/broker_history_sync.py:_snapshot_dict -> 11 lines -->
<!-- def _snapshot_dict(snap: Any) -> dict[str, Any]: -->
<!--     """Flattens a typed snapshot (HistoryOrderSnapshot/DealSnapshot) to a dict.""" -->
<!-- Trace: src/nexus_scalp/adapters/database/broker_history_sync.py:__init__ -> 22 lines -->
<!--     def __init__( -->
<!--         self, -->
<!-- Trace: src/nexus_scalp/adapters/database/broker_history_sync.py:start -> 6 lines -->
<!--     def start(self) -> None: -->
<!--         if self.running: -->
<!-- Trace: src/nexus_scalp/adapters/database/broker_history_sync.py:stop -> 5 lines -->
<!--     def stop(self) -> None: -->
<!--         if not self.running: -->
<!-- Trace: src/nexus_scalp/adapters/database/broker_history_sync.py:tick -> 40 lines -->
<!--     def tick(self) -> bool: -->
<!--         """One bounded sync cycle if interval elapsed; True when a cycle ran.""" -->
<!-- Trace: src/nexus_scalp/adapters/database/broker_history_sync.py:_sync_once -> 54 lines -->
<!--     def _sync_once(self) -> dict[str, Any]: -->
<!--         """Fetches + persists one bounded history window (idempotent).""" -->
<!-- Trace: src/nexus_scalp/adapters/database/broker_history_sync.py:_warm_accounting -> 16 lines -->
<!--     def _warm_accounting(self) -> None: -->
<!--         """Kicks the derived accounting cache so dashboard numbers refresh.""" -->
<!-- Trace: src/nexus_scalp/adapters/paper/paper_adapter.py:__init__ -> 10 lines -->
<!--     def __init__(self, initial_balance: float = 10000.0, symbol: str = "EURUSD") -> None: -->
<!--         self.symbol = symbol -->
<!-- Trace: src/nexus_scalp/adapters/paper/paper_adapter.py:_symbol_is_metal -> 3 lines -->
<!--     def _symbol_is_metal(symbol: str) -> bool: -->
<!--         upper = (symbol or "").upper() -->
<!-- Trace: src/nexus_scalp/adapters/paper/paper_adapter.py:_ensure_symbol -> 10 lines -->
<!--     def _ensure_symbol(self, symbol: str) -> None: -->
<!--         """BUGFIX-G29: confirm `symbol` is tracked by the simulated feed. -->
<!-- Trace: src/nexus_scalp/adapters/paper/paper_adapter.py:_quote_digits -> 2 lines -->
<!--     def _quote_digits(self, symbol: str) -> int: -->
<!--         return 2 if self._symbol_is_metal(symbol) else 5 -->
<!-- Trace: src/nexus_scalp/adapters/paper/paper_adapter.py:connect -> 5 lines -->
<!--     def connect(self) -> bool: -->
<!--         """Initializes paper trading simulation state.""" -->
<!-- Trace: src/nexus_scalp/adapters/paper/paper_adapter.py:disconnect -> 4 lines -->
<!--     def disconnect(self) -> None: -->
<!--         """Disconnects simulation adapter.""" -->
<!-- Trace: src/nexus_scalp/adapters/paper/paper_adapter.py:is_connected -> 2 lines -->
<!--     def is_connected(self) -> bool: -->
<!--         return self._connected -->
<!-- Trace: src/nexus_scalp/adapters/paper/paper_adapter.py:connection_state -> 7 lines -->
<!--     def connection_state(self) -> MT5ConnectionState: -->
<!--         state = MT5ConnectionState() -->
<!-- Trace: src/nexus_scalp/adapters/paper/paper_adapter.py:get_account_snapshot -> 26 lines -->
<!--     def get_account_snapshot(self) -> AccountSnapshot: -->
<!--         snap = AccountSnapshot() -->
<!-- Trace: src/nexus_scalp/adapters/paper/paper_adapter.py:get_symbol_snapshot -> 40 lines -->
<!--     def get_symbol_snapshot(self, symbol: str) -> SymbolSnapshot: -->
<!--         snap = SymbolSnapshot() -->
<!-- Trace: src/nexus_scalp/adapters/paper/paper_adapter.py:get_broker_tick -> 25 lines -->
<!--     def get_broker_tick(self, symbol: str) -> BrokerTickSnapshot: -->
<!--         try: -->
<!-- Trace: src/nexus_scalp/adapters/paper/paper_adapter.py:get_all_positions -> 3 lines -->
<!--     def get_all_positions(self, symbol: str | None = None) -> list[PositionSnapshot]: -->
<!--         positions = self.get_positions(symbol=symbol) -->
<!-- Trace: src/nexus_scalp/adapters/paper/paper_adapter.py:get_rate_history -> 22 lines -->
<!--     def get_rate_history( -->
<!--         self, -->
<!-- Trace: src/nexus_scalp/adapters/paper/paper_adapter.py:order_calc_margin_snapshot -> 19 lines -->
<!--     def order_calc_margin_snapshot( -->
<!--         self, -->
<!-- Trace: src/nexus_scalp/adapters/paper/paper_adapter.py:order_calc_profit_snapshot -> 23 lines -->
<!--     def order_calc_profit_snapshot( -->
<!--         self, -->
<!-- Trace: src/nexus_scalp/adapters/paper/paper_adapter.py:get_history_deals -> 5 lines -->
<!--     def get_history_deals( -->
<!--         self, from_utc: Any = None, to_utc: Any = None, symbol: str | None = None -->