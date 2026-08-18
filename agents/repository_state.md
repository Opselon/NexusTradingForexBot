# REPOSITORY STATE SNAPSHOT — Nexus Scalp Engine (NSE)

> Maintained per the MASTER MULTI-AGENT CONTRACT §6 (see `agents/multi-agent-git-contract.md`).
> This is the project's CURRENT MAP. Refresh after substantial work.
> Snapshot taken: 2026-08-18 (contract registry initialization).

## Git state
- Branch: `main`
- HEAD: `4d5ef5d` — feat(ui): Account Performance & Intelligence panel UX v2 — hero cards, grouped metrics, deep scenario analysis (2026-08-18 04:50 +0330)
- Working tree: DIRTY (188 entries at snapshot) — parallel sessions' uncommitted work present (UI v2, BUG-081 forensics, settings subsystem, news keywords, scratch probes). DO NOT overwrite/reset/stash (contract §1).

## Architecture
- Full inventory in `agents/skill.md` (2437 lines, 🟢 forensic badges). Layer map: Domain (frozen Pydantic) → Ports (IMT5Port) → Adapters (mt5/paper/database) → Features (50D) → Models (ScalpNet 4-class) → Training (walk-forward, triple-barrier) → Signals (policy + rule matrix) → Risk (dynamic volume) → Execution (order_manager) → Application (live_engine async) → Web/API.
- Active model: ScalpNet (PyTorch), input (Batch, 50), 4-logit head (NO_TRADE/BUY/SELL/WAIT), Champion/Challenger lifecycle (Phase 10-11).
- Feature schema: 50D master contract (float, finite, clipped [-3.0, +3.0]); 60D/350D variants schema-controlled (INV-009).

## Databases
| DB | Responsibility | Path |
| :--- | :--- | :--- |
| audit.db | trading ledger, experience outcomes, accounting, research registry, strategy lifecycle | artifacts/audit.db |
| news.db | news ingestion, analysis, consensus, impacts | artifacts/news.db |

Write path: AuditRepository queues writes to a background worker thread (no sync DB on tick path, INV-001). Shadow tables are LAZY-schema (ensure_schema on first save).

## Workers / runtime
- LiveEngine async event loop (tick pipeline, bar aggregation, state sync, retrain orchestrator).
- AuditRepository background writer thread.
- Research worker (seed builtin candidates → dataset → discovery each cycle).
- Training worker via asyncio.to_thread (auto_train_enabled=False).

## Subsystem state
- MT5 integration: broker-aware providers (Phase 14); MT5 epochs are SERVER-LOCAL (broker GMT+3, NOT UTC) — BUG-070.
- News: Phase 12 engine, keyword lexicon 189 keywords, /api/news/keywords pattern cache fixed.
- Experience: outcome recovery + broker reconstruction (Phase 14); BUG-073/081 related work in progress.
- Accounting: Phase 08 unified core + advanced metrics; retention module new.
- Research: registry + strategy lifecycle; BUG-075 fixed (null score).
- UI: Web dashboard, Account Performance & Intelligence panel v2 at HEAD.
- Release: health.py / verify.py present; RELEASE.md in docs/.

## Active bugs / blockers
- Bug ledger: `agents/bugs.md` reached BUG-081 (split-fill context leak + no-order-ID duplicates + exit-classifier falsehoods — in progress).
- Data Gate: own numbering (M5 dataset passed; BUG-060 OPEN).
- Known OPEN at snapshot: BUG-060 (Data Gate), BUG-081 follow-ups, retention/perf P3 items (see skill.md §19).

## Active agents
- Parallel sessions are actively working (evidence: 188-entry dirty tree, BUG-072..081 forensics from 2026-08-18). Check `agents/locks.yaml`, `agents/taskboard.md`, and git status before claiming any path.

## Refresh rule
Update this file additively after substantial work (new modules, model changes, DB changes, major bug fixes). Never delete historical state notes — append a new snapshot section.

## Refresh (2026-08-18, TASK-9)
- NEW `src/nexus_scalp/release/updater.py` (full update engine + orchestrator).
- `nexus update` now exposes check/status/history/rollback/doctor.
- release-manifest.json is embedded in the portable tree (CI + build script).
- EXIT_UPDATE=5 added (additive).
- REAL Windows experiment performed: v9.0.0 -> v9.1.0 over a local GitHub
  stub; user data fully preserved; app-tree data dirs preserved across swap.

## Snapshot 2026-08-18 (TASK-5 model intelligence)
- Feature schema: `scalp_v1` (50D) remains ACTIVE. `scalp_v2` (60D) is now
  PRODUCIBLE (features/schema_augment.py + model_generation/schema_v2.py) but
  candidate-only — no live switch.
- Model lifecycle: VALIDATION gates hardened (OOS macro-F1 floor 0.34,
  balanced-accuracy floor 0.34, ECE floor 0.15, min-evidence 100 rows).
  Benchmark matrix now 8 cells (50D/60D × news off/on × LEGACY/TCN).
- Training worker status truthful: DISABLED when auto_train_enabled=False.
- Champion frozen: `artifacts/models/scalp/XAUUSD/v1.0.0` (hash
  f0f70efb…) — untouched; snapshot `docs/task5_champion_baseline.json`.
- Controlled experiment (real M5, 99,946 rows): A/B/C/D all REJECTED;
  60D improved acc/ECE directionally but no candidate cleared the gates;
  news verdict NEWS_INCONCLUSIVE_NO_OVERLAP (news DB postdates dataset).
- Bug ledger: BUG-083 appended (60D had no producer). INV-015/016 added.
- Active bugs at snapshot: BUG-060 (Data Gate), BUG-081 follow-ups.
