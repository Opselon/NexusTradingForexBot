# TASK-RESEARCH-PLAYBOOK-UI — Research page playbook + animation layer

- Owner: Hermes agent (research UI lane)
- Branch: `agent/feature/research-playbook-ui` (worktree
  `C:/Users/Capsizer/source/repos/nse-research-ui`, cut from `origin/main`)
- Status at writing: local gates green (see Verification); PR opened from the
  branch; merge waits on full CI green per repo contract.

## What changed

1. `frontend/src/features/research/handbook/` (NEW, 19 files, ~4.4k lines)
   Static strategy documentation compiled from backend source:
   - `types.ts` — content model (type-only module)
   - `gates/` — chain overview + one entry per gate
     (STATIC_VALIDATION, BACKTEST, WALK_FORWARD, OOS, ROBUSTNESS, SCORING)
   - `lifecycle.ts` — 16-state machine + transitions/approval/eligibility,
     with `STATE_IDS` mirroring `models.py::CandidateLifecycle`
   - `discovery.ts` (floors 8/20/+0.10R, fingerprint, tiers, sample_ids),
     `scoring.ts` (10-dim weights, verdict precedence, DSR/CI doctrine),
     `economics.ts` (E1 guard, 0.25R line, OOS floors, purge/embargo,
     stress battery), `evidence.ts` (hashes, snapshots v2, lineage),
     `operations.ts` (commands, availability, worker health, queue,
     retention) + `pipeline`
   - `registry.ts` (cache discipline, versions, upsert), `experiments.ts`
     (CHG-0035 FORWARD_TEST freeze + streaming replay), `glossary.ts` (A–Z),
     `uiguide.ts` (tab-by-tab reading guide), `faq.ts` (master FAQ)
   - `index.ts` — assembly, unique-id guard, AND-term search, lookups
2. `ui/StrategyPlaybook.tsx` (NEW) — searchable accordion handbook with TOC,
   verbatim-constants tables, FAQ cards, see-also cross-links, deep-link
   focus/scroll; docs clearly labeled as documentation, zero live data.
3. `ui/research.css` (NEW, `.rs-*` namespace) — animation layer: hero
   gradient + sweep, staggered entrances, count-up KPIs, lifecycle-rail
   share fills, travelling pipeline pulses, tab-pane fade, accordion
   expand, drawer chain rail, reduced-motion guard.
4. `ui/ResearchPage.tsx` — hero (pipeline map, worker live-dot, freshness),
   animated KPI row, census rail with % fills (filter preserved), new
   Playbook tab; all queries/availability/tab logic unchanged.
5. `ui/StrategyDrawer.tsx` — chain-position rail colored strictly from the
   strategy's own gate rows; new Playbook tab (deep-linked to gate overview).
6. `tests/js/research_handbook.test.js` (NEW, 10 tests) — pins handbook to
   backend source: GATE_CHAIN/required-gates/terminal statuses/failure
   classes (evidence.py), 16 states (models.py), floors (discovery.py),
   weight table + sample logistic + DSR (scoring.py), degradation/OOS
   floors/purge/embargo/fold rules (robustness/oos/splitting/walkforward),
   stress scenario names, doc ref file names, id uniqueness, search
   semantics. Registers a `registerHooks` resolver so Node loads
   bundler-style specifiers; leaves stay `import type`-linked (erasable).
7. `frontend/src/features/research/README.md` — playbook section added.
8. `agents/taskboard.md` — this task's prose entry appended.

## Verification (actually executed, this worktree)

- `npm run build` (tsc -b && vite build) → PASS (exit 0; ResearchPage chunk
  134.86 kB incl. handbook).
- `node tests/js/research_handbook.test.js` → 10/10 pass.
- Full `tests/js/*.test.js` sweep → pass (3 files print a custom
  "All N ... passed" harness instead of the node:test summary — verified
  passing on both this branch and main; not failures).
- `scripts/git/preflight.py` → SAFE_TO_MUTATE: YES at branch creation.

## Review notes / known limits

- Handbook `params` quote SHIPPED DEFAULTS; a deployment config may override
  them — the FAQ states config-wins explicitly (gate failure_reason quotes
  the effective value).
- Registry `by_lifecycle` vocabulary (progress labels) is documented as a
  layer above CandidateLifecycle; if the backend adds enum members the
  handbook test fails loudly (docs must be extended, never silently drift).
- Motion is signal-only (order/direction/share/freshness) and freezes under
  `prefers-reduced-motion` (local + theme guards).
- No backend, route, contract, CI-yml or branch-protection changes.

## File inventory (29 paths)

```
frontend/src/features/research/handbook/
  types.ts            content model (type-only)
  index.ts            assembly, unique-id guard, AND-term search, lookups
  lifecycle.ts        16 states + transitions/approval/eligibility
  discovery.ts        floors 8/20/+0.10R, fingerprint, tiers, sample_ids
  scoring.ts          10-dim weights, verdict precedence, DSR/CI doctrine
  economics.ts        E1 guard, 0.25R line, OOS floors, purge/embargo, stress
  evidence.ts         content_hash, snapshots v2, events, outcome lineage
  operations.ts       commands, availability, worker health, queue, retention
  registry.ts         cache discipline, versions, upsert semantics
  experiments.ts      CHG-0035 FORWARD_TEST freeze, streaming replay, benchmark
  health.ts           health / preflight / diagnostics "why" lenses
  glossary.ts         A-Z backend vocabulary with per-term citations
  uiguide.ts          tab-by-tab reading guide + workflows + a11y notes
  faq.ts              master operator FAQ
  gates/index.ts      chain overview, GATE_CHAIN/statuses/failure classes
  gates/{staticValidation,backtest,walkForward,oos,robustness,scoringGate}.ts
frontend/src/features/research/ui/
  StrategyPlaybook.tsx  searchable accordion handbook (NEW)
  research.css          .rs-* animation layer (NEW)
  ResearchPage.tsx      hero, animated KPIs, census rail, playbook tab
  StrategyDrawer.tsx    chain-position rail + playbook tab
frontend/src/features/research/README.md   playbook section
tests/js/research_handbook.test.js         10 source-pinning tests (NEW)
agents/taskboard.md                        task row (append-only)
docs/agent_handoffs/TASK-RESEARCH-PLAYBOOK-UI.md  this file (NEW)
```

## Local commands (reproduce the verification)

```bash
cd frontend && npm run build                # tsc -b && vite build  -> exit 0
cd .. && node tests/js/research_handbook.test.js   # 10/10 pass
for f in tests/js/*.test.js; do node "$f" >/dev/null 2>&1 || echo FAIL "$f"; done
python scripts/git/preflight.py             # SAFE_TO_MUTATE: YES (at branch cut)
```

Reviewer quick-targets: `handbook/gates/index.ts` (chain tuple) vs
`src/nexus_scalp/research/evidence.py:127`; `handbook/scoring.ts` weight
table vs `scoring.py::weights`; `handbook/lifecycle.ts STATE_IDS` vs
`models.py:48-63`. The test asserts all three — if CI is red there, docs
and code genuinely diverged and the docs must be updated to match code.

## Gaps / follow-ups (honest list)

- Playbook content is compiled from source AT WRITING TIME; the test pins
  ~40 constants/sets, but prose describing BEHAVIOR (not pinned numbers)
  can still age — review the playbook when research/*.py changes materially.
- `by_lifecycle` progress labels are rendered from the backend as-is; if a
  new label appears that no topic explains, the glossary/uiguide need a
  sentence (the state-machine test only pins CandidateLifecycle enums).
- Handbook search is client-side over all 39 entries (fast at this scale);
  if entries double again, consider prebuilt index tokens.
- No visual-regression/screenshot suite was added — motion and layout are
  covered by build+typecheck+manual DOM semantics only.

## Merge plan

Push branch → PR (TASK-RESEARCH-PLAYBOOK-UI-20260923 in body) → dispatch
`docs.yml` (Validate documentation context) → wait for ALL required checks
green → squash-merge via API → verify merged → report.
