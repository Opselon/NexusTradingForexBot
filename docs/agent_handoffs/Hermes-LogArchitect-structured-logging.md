# AGENT HANDOFF — Hermes-LogArchitect: Master Structured Logging & Organized Log Storage

- **Agent**: Hermes-LogArchitect
- **Role**: Observability / Structured Logging Architecture Engineer
- **Task**: Master implementation brief — structured, categorized, timestamped, searchable, physically organized logs
- **Starting HEAD**: 98d866e (context bootstrap) → working HEAD at handoff: `96d7c82`
- **Branch**: main

## What was delivered

### 1. Centralized logging engine — `src/nexus_scalp/observability/logging.py` (rewritten)

Severity-first, date-organized layout (one convention, brief §43):

```
logs/
  info/     YYYY/MM/YYYY-MM-DD[.part-NNN].log
  warning/  YYYY/MM/YYYY-MM-DD[.part-NNN].log
  error/    YYYY/MM/YYYY-MM-DD[.part-NNN].log
  critical/ YYYY/MM/YYYY-MM-DD[.part-NNN].log
```

- **Timestamps**: ISO-8601 with explicit project timezone — `2026-08-20T02:25:14.392+03:30` (never bare HH:MM, never ambiguous — brief §3).
- **Structured fields**: `level`, `event` (stable name), `component`, `category`, `error_code`, `correlation_id` / `run_id` / `generation_id` / `strategy_id` / `experiment_id` / `trade_id` / `order_id` / `position_id` / `symbol` / `timeframe` when bound (brief §4/§14).
- **Event names**: `EVENT_CATEGORIES` map (42 events, brief §16) auto-attaches `category`; `ERROR_CODES` (9 stable codes NEXUS-*-001, brief §18) auto-attached for error/critical.
- **Rotation**: daily + size cap (10 MB default, brief §24). Files split to `YYYY-MM-DD.part-NNN.log` — verified ZERO-LOSS over 2000 records/46 parts.
- **Retention**: per-severity `retention_days` (info/warning 30, error 90, critical 365; configurable via `configure_logging`), hourly prune; `logs/archive` and unknown buckets NEVER auto-deleted (brief §25/§26).
- **Redaction**: key-based fragments + high-entropy catch-all (BUG-121 discipline) with trusted-structural-key exemption (event names never scrubbed) (brief §30).
- **Multi-process safety**: all appends under a process-wide `RLock`, files opened append-mode (brief §2).
- **Console**: colored, human-readable, same `pad_event=28` style (brief §28); files are plain-text (ANSI-stripped) with full rich-formatted stack traces preserved (brief §11).
- **Public API unchanged**: `configure_logging(log_level, json_format, log_to_file, log_file_path, retention_days)` (log_file_path now means BASE dir) + `get_logger(name)`. New: `log_event(...)`, `bind_correlation_id(...)`, `timestamp_now()`, `reset_prune_throttle()`.

### 2. Call sites routed (brief §38/§39)

- `NexusTradingForexBot.py` launcher → `log_file_path=Path("logs")`
- `LiveEngine.start()` → same
- `src/cli/train_model.py` → same
- Removed dead `json_format=(mode=="LIVE" and False)` expression.

### 3. Tests — `tests/unit/test_logging.py` (14 tests, all PASS)

Severity routing (INFO/WARNING/ERROR/CRITICAL each to exactly their file), stack trace + exception message in plain-text error file, secrets redacted on disk, structured fields incl. correlation_id, ISO-8601+03:30 timestamps, `_LevelMatchFilter` exact-severity, rotation zero-loss, retention/archive protection, dated path convention.

## Verification evidence (brief §44)

- Acceptance run in temp dir: INFO → info/2026/08/2026-08-20.log (3 events), WARNING → warning (LOW_TRADE_COUNT), ERROR → error (BACKTEST_FAILED + full `ZeroDivisionError` stack), CRITICAL → critical (GLOBAL_KILL_SWITCH_ACTIVATED + NEXUS-RISK-001).
- Rotation: 2000 records → 46 part files, 2000/2000 lines preserved.
- Retention: old info pruned; archive kept.
- `py_compile` clean; ruff check/format clean; mypy clean on logging.py.

## Commits (local, NOT yet pushed)

- `7c19a34` engine rewrite
- `208eebe` call-site routing + test rewrite
- `df5c1e2` test parser fix
- `efa2afa` ruff/mypy hygiene
- `96d7c82` rotation/retention tests + prune seam

## Registries

- `agents/change_control.md`: CHG-0028 (VERIFIED)
- `agents/taskboard.md`: TASK-LOGGING-ARCH row (VERIFIED)

## Known limitations / risks

1. Old `artifacts/logs/nse_live.log` mixed file is legacy content — new runs write only the severity tree under `logs/`. No migration of historical rows (intentional: read-only archaeology).
2. Logging remains synchronous (per-process lock) — same as before; the only hot-path per-event logger is the throttled champion verification (BUG-118). Async queue deliberately NOT used (ProcessorFormatter must run in the calling thread for contextvars; verified).
3. `logs/` is already gitignored (`logs/` line exists) — confirmed.
4. Packaged EXE: `log_file_path` default remains repo-relative `logs/`; EXE still writes absolute `%LOCALAPPDATA%\NexusScalpEngine\logs` via release paths only if a caller passes it — follow-up: pass `release.paths.get_logs_dir()` from the packaged bootstrap.

## EXACT NEXT-AGENT INSTRUCTIONS

1. Push local commits: `git push origin main` (branch is 11+ ahead).
2. Optional: pass `log_file_path=release.paths.get_logs_dir()` in `packaged_main.py`/`cli/main.py` start path for EXE runs.
3. Optional (brief §37): add `/api/logs/errors` monitor endpoint reading the structured error tree (errors today/warnings today/last error/counts by component).
4. If a parallel agent broke `test_log_autopsy_fixes.py` collection (RuleMatrixEngine rename), coordinate with the rule-matrix owner — not this task's scope.