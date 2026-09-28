# Phase 2B/2C/2D — Database Integrity + Reconciliation

generated: 2026-09-27T23:42:05.146469+00:00

Read-only audit. No production database was mutated.

## Provider routing (runtime evidence)

- configured provider: **postgresql**
- settings store: `C:\Users\Capsizer\AppData\Local\NexusScalpEngine\databases\app_settings.db`
- target database (config): `nexusdb`

## Integrity findings

- [warning] multiple_champion_rows — `experience_model_registry`: primary_scalp_scalp_v3_70d/v1.0 has 9 CHAMPION rows
- [info] champion_row_count — `experience_model_registry`: 9 CHAMPION rows across all identities (governed supersession keeps history; verify the latest is served)

## Reconciliation by stage

### DATA

| table | PG rows | SQLite rows | PG latest | SQLite latest | class | evidence |
| --- | --- | --- | --- | --- | --- | --- |
| `audit_experiences` | 5239 | 5239 | 2026-09-23T22:50:57 | 2026-09-23T22:50:57 | **EXPECTED** | identical row count |
| `audit_experience_outcomes` | 1715 | 1715 | 2026-09-23T16:43:21 | 2026-09-23T16:43:21 | **EXPECTED** | identical row count |

### FEATURES

| table | PG rows | SQLite rows | PG latest | SQLite latest | class | evidence |
| --- | --- | --- | --- | --- | --- | --- |
| `audit_signals` | 11302 | 11266 | 2026-09-27T03:12:43 | 2026-09-24T18:40:40 | **STALE** | row gap +36; postgresql has the newer latest timestamp (pg=2026-09-27T03:12:43.964524+00:00 sqlite=2026-09-24T18:40:40+0 |
| `research_run_snapshots` | 4357 | 4357 | 2026-09-22T00:33:28 | 2026-09-22T00:33:28 | **EXPECTED** | identical row count |

### MODEL

| table | PG rows | SQLite rows | PG latest | SQLite latest | class | evidence |
| --- | --- | --- | --- | --- | --- | --- |
| `experience_model_registry` | 26 | 26 | 2026-09-24T18:44:54 | 2026-09-24T22:52:29 | **EXPECTED** | identical row count |
| `training_runs` | 6 | 6 | 2026-09-07T05:40:05 | 2026-09-07T05:40:05 | **EXPECTED** | identical row count |
| `model_comparisons` | 4 | 4 | 2026-09-07T05:40:05 | 2026-09-07T05:40:05 | **EXPECTED** | identical row count |
| `model_load_history` | — | — | None | None | **EXPECTED** | table exists only on postgresql |
| `model_runtime_health` | 1940 | 1935 | 2026-09-27T03:12:49 | 2026-09-24T18:40:55 | **STALE** | row gap +5; postgresql has the newer latest timestamp (pg=2026-09-27T03:12:49.741613+00:00 sqlite=2026-09-24T18:40:55.90 |

### STRATEGY

| table | PG rows | SQLite rows | PG latest | SQLite latest | class | evidence |
| --- | --- | --- | --- | --- | --- | --- |
| `strategy_registry` | 4109 | 4109 | 2026-09-24T00:15:27 | 2026-09-24T18:40:55 | **EXPECTED** | identical row count |
| `strategy_intelligence_registry` | 373 | 373 | 2026-09-24T18:13:49 | 2026-09-24T18:40:35 | **EXPECTED** | identical row count |
| `strategy_evolution_candidates` | 1 | 1 | 2026-09-07T16:58:01 | 2026-09-07T16:58:01 | **EXPECTED** | identical row count |

### BACKTEST

| table | PG rows | SQLite rows | PG latest | SQLite latest | class | evidence |
| --- | --- | --- | --- | --- | --- | --- |
| `strategy_registry` | 4109 | 4109 | 2026-09-24T00:15:27 | 2026-09-24T18:40:55 | **EXPECTED** | identical row count |
| `research_runs` | 4357 | 4357 | 2026-09-22T00:33:28 | 2026-09-22T00:33:28 | **EXPECTED** | identical row count |
| `research_evidence` | 18316 | 18316 | 2026-09-22T00:33:28 | 2026-09-22T00:33:28 | **EXPECTED** | identical row count |

### WALK-FORWARD

| table | PG rows | SQLite rows | PG latest | SQLite latest | class | evidence |
| --- | --- | --- | --- | --- | --- | --- |
| `strategy_registry` | 4109 | 4109 | 2026-09-24T00:15:27 | 2026-09-24T18:40:55 | **EXPECTED** | identical row count |
| `research_gates` | 26140 | 26140 | 2026-09-22T00:33:28 | 2026-09-22T00:33:28 | **EXPECTED** | identical row count |

### OOS

| table | PG rows | SQLite rows | PG latest | SQLite latest | class | evidence |
| --- | --- | --- | --- | --- | --- | --- |
| `strategy_registry` | 4109 | 4109 | 2026-09-24T00:15:27 | 2026-09-24T18:40:55 | **EXPECTED** | identical row count |
| `research_gates` | 26140 | 26140 | 2026-09-22T00:33:28 | 2026-09-22T00:33:28 | **EXPECTED** | identical row count |
| `research_runs` | 4357 | 4357 | 2026-09-22T00:33:28 | 2026-09-22T00:33:28 | **EXPECTED** | identical row count |

### ROBUSTNESS

| table | PG rows | SQLite rows | PG latest | SQLite latest | class | evidence |
| --- | --- | --- | --- | --- | --- | --- |
| `strategy_registry` | 4109 | 4109 | 2026-09-24T00:15:27 | 2026-09-24T18:40:55 | **EXPECTED** | identical row count |
| `research_gates` | 26140 | 26140 | 2026-09-22T00:33:28 | 2026-09-22T00:33:28 | **EXPECTED** | identical row count |

### COUNTERFACTUAL

| table | PG rows | SQLite rows | PG latest | SQLite latest | class | evidence |
| --- | --- | --- | --- | --- | --- | --- |
| `research_evidence` | 18316 | 18316 | 2026-09-22T00:33:28 | 2026-09-22T00:33:28 | **EXPECTED** | identical row count |
| `research_events` | 82726 | 82726 | 2026-09-22T00:33:28 | 2026-09-22T00:33:28 | **EXPECTED** | identical row count |

### REPLAY

| table | PG rows | SQLite rows | PG latest | SQLite latest | class | evidence |
| --- | --- | --- | --- | --- | --- | --- |
| `audit_broker_orders` | 10648 | 10647 | 2026-09-27T12:44:33 | 2026-09-23T22:50:48 | **STALE** | row gap +1; postgresql has the newer latest timestamp (pg=2026-09-27T12:44:33.868661+00:00 sqlite=2026-09-23T22:50:48.92 |
| `audit_broker_deals` | 8484 | 8482 | 2026-09-27T12:44:33 | 2026-09-23T22:50:48 | **STALE** | row gap +2; postgresql has the newer latest timestamp (pg=2026-09-27T12:44:33.868661+00:00 sqlite=2026-09-23T22:50:48.92 |
| `audit_broker_trades` | 4116 | 4115 | 2026-09-27T12:44:33 | 2026-09-23T22:50:48 | **STALE** | row gap +1; postgresql has the newer latest timestamp (pg=2026-09-27T12:44:33.868661+00:00 sqlite=2026-09-23T22:50:48.92 |
| `position_lifecycle_events` | 3888 | 3817 | 2026-09-23T16:43:21 | 2026-09-23T16:43:21 | **STALE** | row gap +71; sqlite has the newer latest timestamp (pg=2026-09-23T16:43:21+00:00 sqlite=2026-09-23T16:43:21+00:00) |

### VALIDATION

| table | PG rows | SQLite rows | PG latest | SQLite latest | class | evidence |
| --- | --- | --- | --- | --- | --- | --- |
| `research_runs` | 4357 | 4357 | 2026-09-22T00:33:28 | 2026-09-22T00:33:28 | **EXPECTED** | identical row count |
| `research_gates` | 26140 | 26140 | 2026-09-22T00:33:28 | 2026-09-22T00:33:28 | **EXPECTED** | identical row count |
| `research_evidence` | 18316 | 18316 | 2026-09-22T00:33:28 | 2026-09-22T00:33:28 | **EXPECTED** | identical row count |
| `model_governance_events` | 59169 | 59164 | 2026-09-27T23:37:53 | 2026-09-24T22:52:29 | **STALE** | row gap +5; postgresql has the newer latest timestamp (pg=2026-09-27T23:37:53.971578+00:00 sqlite=2026-09-24T22:52:29.36 |

### PROMOTION

| table | PG rows | SQLite rows | PG latest | SQLite latest | class | evidence |
| --- | --- | --- | --- | --- | --- | --- |
| `model_promotion_audit` | 0 | 0 | None | None | **EXPECTED** | identical row count |
| `model_rollback_audit` | 0 | 0 | None | None | **EXPECTED** | identical row count |
| `model_governance_events` | 59169 | 59164 | 2026-09-27T23:37:53 | 2026-09-24T22:52:29 | **STALE** | row gap +5; postgresql has the newer latest timestamp (pg=2026-09-27T23:37:53.971578+00:00 sqlite=2026-09-24T22:52:29.36 |
| `shadow_promotions` | 0 | 0 | None | None | **EXPECTED** | identical row count |

