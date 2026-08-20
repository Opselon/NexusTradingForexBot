# AGENT HANDOFF — TASK-22-DB-HYGIENE-RUNTIME (Hermes-DBHygiene, 2026-08-20)

## Agent / Role / Task
- Agent: Hermes-DBHygiene
- Role: Database Hygiene / Runtime Data Integrity Engineer
- Task: TASK-22 — Runtime Database Hygiene Engine (master brief):
  continuous cleanup, quarantine, consistency rules, index health,
  retention policy, Telegram reporting, CLI + UI, dry-run safety.

## Starting / Ending HEAD
- Starting HEAD: 7ce7198 (before my commits)
- Ending HEAD: 898f749 (after INV renumber fix)
- Branch: main (local); my commits: 2afc40b, 07b6249, 07ef07e, 3bf669f,
  77b094b, 1e9a552, 9e0bb46, f440426, 898f749
- NOTE: parallel swarm absorbed my registry entries (taskboard TASK-22 row,
  CHG-0029, INV-022) into 9576043 (Hermes-LiquidityCompat); I renumbered
  my invariant to INV-023 in 898f749. Registry entries live in HEAD:
  `agents/taskboard.md` (TASK-22-DB-HYGIENE-RUNTIME),
  `agents/change_control.md` (CHG-0029), `agents/runtime_invariants.md`
  (INV-023).

## Commits (mine, all pushed-ready on main)
| SHA | Content |
| :--- | :--- |
| 2afc40b | config skeleton: database_hygiene section + DatabaseHygieneConfig |
| 07b6249 | hygiene engine core: quarantine/consistency/index_health/report + RuntimeCleanupScheduler |
| 07ef07e | LiveEngine wiring: scheduler replaces bare worker, Telegram reports |
| 3bf669f | CLI: db hygiene health / cleanup --dry-run --deep / quarantine |
| 77b094b | /api/db/hygiene runtime + quarantine |
| 1e9a552 | Web Database Health Panel + loadHealthPanel() (fixed dead control) |
| 9e0bb46 | TEST-HYG-37..48 regression guards |
| f440426 | ruff+format+mypy clean |
| 898f749 | INV-023 renumber |

## Files Created
- `src/nexus_scalp/hygiene/quarantine.py` — DataQuarantine store
  (MOVE->MARK->REPORT; restore/resolve; provenance who/when/what/why;
  dedupe per (database,table,row_id); separate SQLite under
  artifacts/archive/_quarantine/).
- `src/nexus_scalp/hygiene/consistency.py` — ConsistencyRuleEngine
  (TRADE-001..003, LEDGER-001, UNREAL-001, DATASET-001..004, NEWS-001;
  read-only; NOT_APPLICABLE on missing schema; findings_summary).
- `src/nexus_scalp/hygiene/index_health.py` — IndexHealthMonitor
  (missing/duplicate/unused index advisory via PRAGMA; polling_mode
  skips unused-index advice; never creates/drops schema).
- `src/nexus_scalp/hygiene/report.py` — DATABASE_HYGIENE_INITIAL_REPORT,
  cycle telemetry (spec 15), QUERY_HEALTH_REPORT, Telegram report text
  builders (spec 16 shape).
- `src/nexus_scalp/hygiene/hygiene_runtime.py` — RuntimeCleanupScheduler
  conductor: settings, cadence, first-run audit, deep maintenance,
  quarantine_rows, telegram cooldown, status().

## Files Modified
- `src/nexus_scalp/configuration/config.py` — DatabaseHygieneConfig +
  RetentionsConfig (telemetry_days/cache_hours/failed_jobs_days/audit_days).
- `configs/base.yaml` — database_hygiene section (LF preserved).
- `src/nexus_scalp/application/live_engine.py` — tick-loop hygiene block
  replaced: scheduler constructed lazily, run via asyncio.to_thread,
  cooldown-gated Telegram REPORT. (NOTE: parallel agents keep touching this
  file — re-check before further edits.)
- `src/nexus_scalp/cli/db_commands.py` — new subcommands health/cleanup/quarantine.
- `src/nexus_scalp/web/server.py` — /api/db/hygiene now returns runtime +
  quarantine sections. (Parallel agent removed my redundant QuarantineStore
  import — their cleanup, endpoint identical.)
- `Web/index.html` + `Web/app.js` — Database Health Panel in tab-health;
  loadHealthPanel() implemented (was a dead onclick reference = BUG-119
  pattern fix). div-balance verified via strict HTMLParser.
- `tests/unit/test_database_hygiene_task11.py` — TEST-HYG-37..48 appended.

## Contracts
- DATABASE_HYGIENE v2 (was v1): + runtime scheduler, quarantine store,
  consistency rules, index health, cycle telemetry, Telegram reports.
- RETENTION_POLICY v1 unchanged.
- INV-023: runtime hygiene continuous/config-driven/never-self-destructive
  (6 numbered invariants).

## Tests
- tests/unit/test_database_hygiene_task11.py: 49 tests PASS
  (37 pre-existing + TEST-HYG-37..48 new).
- tests/unit/test_frontend_assets_phase14.py: 41 PASS (div balance +
  tab nesting guards after HTML edit).
- tests/unit/test_runtime_config_hot_reload.py: 9 PASS.
- tests/unit/test_cli_db_phase18.py: 5 PASS.
- ruff check + ruff format + mypy src clean on all touched files.

## Runtime Verification
- Scheduler smoke on real DB copies: cycle PASS, initial audit persisted
  (61 tables, 3373 expected orphans, 1 duplicate, 1 violation detected,
  index advisory 18 missing / 2 duplicate), deep cycle produces
  QUERY_HEALTH_REPORT; CLI `nexus db hygiene health|cleanup --dry-run` work.
- Real audit.db consistency scan found 1 UNREAL-001 violation (abandoned
  pending/open state >14d) — REPORTED, not deleted (correct behavior).

## Known Risks / Notes
1. INV-022 taken by parallel agent (BUG-123 LiquidityCompat) — my invariant
   is INV-023. Check `grep ^## INV-` before writing invariants.
2. live_engine.py + server.py + Web/ are hotly contested by parallel
   agents; my committed versions are safe but the worktree may show their
   WIP. Re-verify with `git show <my-sha>:<file>` before editing.
3. Quarantine store + initial audit live under artifacts/archive/ — they
   are RUNTIME state, not repo files; gitignored paths should cover them.
4. The TASK-11 worker remains the mutation core (SAFE_CLEAN classes,
   archive-before-delete, verify-after-batch). TASK-22 only drives cadence
   + observation + quarantine + reporting.
5. Telegram hygiene reports default ON (telegram_report: true in base.yaml)
   but cooldown-gated (3600s) and only when the notifier is configured.
6. Index advisory SQL is emitted in reports; applying it requires TASK-10
   migrations (by design).

## EXACT NEXT-AGENT INSTRUCTIONS
- If extending: read agents/skill.md + agents/bugs.md first; re-grep
  `^## BUG-` tail and `CHG-`/`INV-`/TASK- IDs before writing registries;
  use byte-safe CRLF editing (never the patch tool on CRLF files).
- To run a manual deep cycle: `nexus db hygiene cleanup --deep --dry-run`
  (read-only) or `--apply` only in non-LIVE mode with SAFE_CLEAN.
- Push my 10 commits to origin/main (they are local-only currently) —
  but coordinate: the swarm may have pushed newer HEAD already.