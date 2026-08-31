# Repository Hygiene Audit — Nexus Scalp Engine

> **Date:** 2026-08-31 · **HEAD:** `1f60832` (main) · **Working tree:** UNCHANGED (0 modifications)
> **Mode:** READ-ONLY forensic librarian pass. Nothing was fixed, moved, deleted, or refactored.
> **Machine-readable twin:** `artifacts/forensics/repository-hygiene-audit.json`
> **2026-08-31 repair pass:** CLEANUP-001/002/003/004/005/006 repaired — networkx declared as runtime dep, psycopg declared via new `postgres` extra, requirements ranges reconciled to pyproject, skill.md runner path fixed, tick_storage.py removed, 32 e2e_cc files archived to `scratch/e2e_cc_archive/`, .pyproj.bak removed.
> **Scope guard:** excludes all active-agent work (BUG-166..174, anti-crash, decomposition, release).

## 1. What was analyzed

- 1,621 tracked files (724 py / 625 md / 80 json / 22 js / 16 mjs / 15 ps1 / 10 yml)
- 351 Python source files AST-parsed, 361 modules, 0 parse errors
- Full import graph (own `dependency_intelligence` engine): **1,209 nodes / 3,656 edges**
- 230 backend API endpoints cross-checked against 122 frontend call sites
- 9 CI workflows, 20 scripts, 149 unit test files, 7 JS test files
- Dependency manifests (pyproject, requirements.txt, venv reality), .gitignore/.dockerignore
- Secret-pattern scan over the tracked surface (redacted, read-only)

## 2. Entropy scorecard

| Axis | Score | Headline evidence |
|---|---|---|
| Dead code | 🟡 | 1 verified orphan module (`market_data/tick_storage.py`); 44 other fan-in-zero modules verified alive (inits/entrypoints/dynamic) |
| Duplicate logic | 🟡 | `serialize_enums` ×2 (divergent!), `utc_now` ×2 + 10 `_utc` variants, 14 hand-rolled sqlite `_connect` helpers |
| **Dependency drift** | 🔴 | requirements.txt ranges REJECT the currently-installed versions (structlog 26.1, rich 15.0); `networkx`/`psycopg` imported but undeclared |
| Config duplication | 🟡 | `model_artifact_path`/`confidence_threshold` dual-declared; SignalPolicy 0.20 default vs config 0.35 |
| **Documentation drift** | 🔴 | README says v9.0.3 (pyproject = 9.0.5); 25 dead path references incl. the critical-suite runner in `agents/skill.md`; 438-file doc tree frozen 2026-08-20 |
| Orphan tests | 🟡 | 23 committed one-shot e2e probe outputs/harnesses with zero consumers |
| Orphan scripts | 🟢 | all 20 scripts have consumers |
| Artifact pollution | 🟡 | clean status/ignore discipline; minor committed probe outputs in tests/ |
| Metadata drift | 🟡 | README version; stale local egg-info (untracked, harmless) |
| Feature flags | 🟢 | every flag has reader+writer, single source; no dead/duplicate flags |
| Import cycles | 🟡 | 10 HIGH cycles, 0 architecture violations |
| Security surface | 🟢 | 0 real secrets; 4 synthetic test credentials; scan gate wired (ci.yml:236) |

**Overall: 🔴 RED** — driven entirely by dependency drift + documentation drift; runtime code hygiene is sound.

## 3. Top findings (details + evidence in JSON `cleanup_queue`)

### P0 — act before next build/release (no code change done by this audit)
1. **CLEANUP-001 · Undeclared imports.** `networkx` (`dependency_intelligence/analysis.py:19`, used by the `/api/dependency` web routes AND `nexus dependency` CLI) and `psycopg` (`database/drivers/postgres_driver.py`) are imported but declared nowhere. Dev venv masks this; a clean install / Docker / PyInstaller build can fail at import. *Fix owner: Coder/DevOps.*
2. **CLEANUP-002 · Authoritative-doc dead paths.** `agents/skill.md` documents the critical-suite runner as `tests/helpers/run_critical.py` — which does not exist (actual: `tests/critical_suite.txt` consumed directly by pytest). `docs/ci-telegram-operations.md` cites two deleted test files. 25 dead refs total. *Fix owner: Docs.*

### P1 — could cause wrong behavior later
3. **CLEANUP-003 · Manifest conflict.** requirements.txt (`structlog<25`, `rich<14`, `ruff>=0.2`) vs pyproject (`<27`, `<16`, `==0.16.3`). The installed tree satisfies pyproject and violates requirements.txt. Docker caches on both files; CI installs from pyproject only.
4. **CLEANUP-004 · True orphan module.** `src/nexus_scalp/market_data/tick_storage.py` — zero importers, zero tests, zero dynamic references (all `__import__` sites checked).
5. **CLEANUP-005 · 23 orphan e2e files.** `tests/e2e_cc_phase4_*.{mjs,json}`, `e2e_cc_cert_playwright.js`, `e2e_cc_adversarial_harness.js`, 3 PNGs — one-shot probe outputs from a past certification; no CI/script/doc references. Archival candidates.
6. **CLEANUP-006 · `NexusTradingForexBot.pyproj.bak`** — the only `.bak` tracked in the repo. Zero references.

### P2 — maintenance burden (future consolidation queue)
7. **serialize_enums divergence** — `web/server.py:48` (Enum-only) vs `web/command_center_routes.py:35` (any `.value`-bearing object). Canonical = server.py.
8. **utc_now duplication** — `accounting/periods.py` (the BUG-153 monkeypatch target!) vs `strategies/factory/models.py`. Factory paths bypass time-freeze tests.
9. **14 private sqlite `_connect` helpers** — drifted WAL/timeout handling per module; plausible `database is locked` variance source.
10. **Dual-declared config** — `model_artifact_path` + `confidence_threshold` in both `configs/base.yaml` and `configuration/config.py` defaults (currently equal, can drift).
11. **Threshold semantic shadow** — `SignalPolicy` default 0.20 vs config 0.35; LiveEngine wires config, but a bare `SignalPolicy()` gets 0.20.
12. **README version drift** — v9.0.3 vs pyproject 9.0.5 (v9.0.4/v9.0.5 published per release-hardening ledger).
13. **Docs citing regenerable artifacts** — `bug106_*.json`, `70d_liquidity_parity.json` etc. referenced in docs but gitignored (only 4 artifact JSONs force-tracked).
14. **`src/cli/train_model.py` stale surface** — legacy 50D trainer CLI kept alive by its own test + historical bug ledger; canonical path is `nexus model-*`. Deprecate-or-pin decision needed.
15. **10 packages without `__init__.py`** — half the tree is implicit-namespace; 7 unit tests resolve only thanks to CPython leniency; frozen packaging (BUG-174 family) behaves differently.
16. **10 HIGH import cycles** — `web↔server`, `forensics↔engine`, `store↔audit_repository↔research.store`, etc. (0 layer violations). Already forcing lazy-import workarounds.

### P3/P4 — minor
17. `pip install pytest-cov` redundant after `-e ".[dev,web]"` (4 CI sites — already in dev extra).
18. `types-requests`/`types-setuptools` likely unnecessary (no `requests` import anywhere).
19. requirements.txt mixes runtime+dev deps and duplicates pyproject (second source of truth).
20. Dependency dashboard (`Web/dependency.html` + routes) is fully wired but reachable by NO link — URL-only discoverability.
21. Cache-bust `?v=` tags drifted per script in index.html; command_center.html has none.
22. `docs/forensic-docs/` (438 files = 70% of all markdown) is a point-in-time snapshot frozen 2026-08-20 — aging fast, consider a header disclaimer.

## 4. Verified-ALIVE list (do NOT let future agents delete these)

Fan-in-zero but confirmed consumed: `candle_intelligence.store_writes` (lazy `_attach_writes`), `release.diagnostics`/`exit_codes`/`verify`/`updater`/`update`/`evaluate` (CLI + release.yml + tests), `features.runtime70`/`temporal`/`liquidity_engine_opt`, `observability.ci_telegram_reporter` (telegram_notify.py), `research.leakage`/`context_analysis`, `signals.stability_controller`, `accounting.retention`, `main.py` (documented redirector), `pyarrow` (release/packaging role). Also: every SSE event name has both emitter and consumer; all flags single-source; all 20 scripts referenced; 0 CI paths missing; 0 frontend calls to nonexistent endpoints.

## 5. Safe-to-clean vs domain-owner-required

| Bucket | Items |
|---|---|
| **Mechanical, low-risk (docs/metadata only)** | README version bump, `agents/skill.md` runner path, dead doc-path sweep, `.pyproj.bak` removal, archive 23 e2e probe outputs, CI pytest-cov line |
| **Requires domain owner** | requirements.txt reconciliation (DevOps), networkx/psycopg declaration (Coder), tick_storage removal (Coder), serialize_enums/_utc/_connect consolidation (Coder, post-safety-pass), SignalPolicy threshold SSOT (trading owner), namespace `__init__` files (Coder, zero-behavior-change), cycle refactors (deferred to decomposition program), dependency.html discoverability (web owner) |

## 6. Method notes & limitations

- Orphan verdicts require evidence from ALL of: static imports (AST), dynamic `__import__`/`importlib` sites, string greps across tests/scripts/Web/.github/docs, CLI registrations, and route wiring — "no direct import" alone was never treated as proof of death.
- scratch/ (310 tracked files) retained per repo convention (historical probes; never removed en masse).
- Endpoint no-client candidates (12) are classified POSSIBLY_DYNAMIC (console/external consumers possible) — owner confirmation required before any removal.
- This audit modified exactly two files: this document and `artifacts/forensics/repository-hygiene-audit.json`. Zero commits made; zero working-tree changes outside deliverables.
