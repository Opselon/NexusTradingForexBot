# Research (bounded context)

Candidate validation pipeline: discovery → backtest → walk-forward → OOS →
robustness → score → registry lifecycle (DISCOVERED…VALIDATED→SHADOW→ACTIVE).

- Invariants owned by the backend registry state machine; the UI only renders
  verdicts and asks for confirm-guarded commands (`api.ts` -> `@/api/client`).
- Availability is backend-decided: `{available:false, reason}` renders verbatim,
  never a fabricated zero (`model.ts::toStrategyVo`, `useCases.ts`).
- Promotion requires an explicit actor id (lineage auditability, RC4 rule).
- RESEARCH-class gate failures are never retryable — technical/data only.
- Evidence: `src/nexus_scalp/web/debug_research_routes.py` (routes),
  `Web/app.js` tab-research (behavior reference), `api_v1/research.py` (v1).
- Shared lane-5 UI helpers live in `ui/lane5Kit.tsx` (owned folder).

## Playbook (static handbook) — `handbook/` + `ui/StrategyPlaybook.tsx`

Deep strategy documentation shipped as a first-class tab: the gate chain +
one entry per gate, the 16-state lifecycle machine, discovery, scoring,
economics, evidence, operations, pipeline, registry, experiments, the
health/preflight/diagnostics lenses, an A-Z glossary, the tab-by-tab UI
guide and the master FAQ — 39 entries, ~4.7k lines of prose plus
verbatim-constants tables and operator Q&A. Layout: TOC groups (big
picture / gate chain / lifecycle states) beside a search box with
AND-term semantics; entries expand as accordion cards carrying sections,
a constants table (name/value/meaning/source-ref), an FAQ block, a
source citation chip and see-also cross-links that jump + expand the
target entry (clearing the search first if the target was filtered out).

- **Documentation, not data.** Handbook modules are static text compiled from
  backend source; live numbers only ever come from the queries in
  `useCases.ts`. The Playbook panel is labeled as docs so the two visual
  languages never blur.
- **Drift is a test failure, not a hope.** `tests/js/research_handbook.test.js`
  re-reads `evidence.py` (GATE_CHAIN, terminal statuses, required gates),
  `models.py` (16 CandidateLifecycle states, MIN_EVIDENCE_SAMPLES),
  `discovery.py` (floors), `scoring.py` (weight table, sample logistic, DSR
  floor), `robustness.py`/`oos.py`/`splitting.py`/`walkforward.py`
  (degradation/ floors / purge-embargo / fold rules) and fails the Frontend
  JS Unit Tests job when docs or code move. It also pins doc `ref`s to the
  right file — a right number under a wrong citation is still a bug.
- **Loading contract:** handbook leaves cross-link via `import type` only
  (erasable), so Node 24 type-stripping loads them without a bundler; the
  test registers a resolve hook for the app's bundler-style specifiers.
  Non-erasable syntax (`enum`, `namespace`, parameter properties) in
  `handbook/**` would break the test — keep leaves erasable.
- **Animation layer** lives in `ui/research.css` (`.rs-*` namespace): hero
  sweep, staggered entrances, count-up KPIs, lifecycle-rail share fills,
  travelling pipeline pulses, accordion expand. Everything freezes under
  `prefers-reduced-motion` (local guard + theme global guard).
- **Drawer chain rail** colors strictly from that strategy's own gate rows
  (PASSED/FAILED/RUNNING); a missing row stays neutral — the UI never
  infers a status the backend did not record.

Coverage map (entry → primary source):

| entry | source of truth |
| --- | --- |
| `topic/gates` + 6 `gate/*` | `research/evidence.py` (chain, statuses, classes) |
| `topic/lifecycle` + 16 `state/*` | `research/models.py` + `research/lifecycle.py` |
| `topic/discovery` | `research/discovery.py` (floors, fingerprint, tiers) |
| `topic/scoring` | `research/scoring.py` (weights, verdict chain, DSR) |
| `topic/economics` | `research/{robustness,oos,splitting}.py` |
| `topic/evidence` | `research/{evidence,run_snapshot,outcome_lineage}.py` |
| `topic/operations`, `topic/pipeline` | `research/{commands,availability,pipeline}.py`, `web/debug_research_routes.py` |
| `topic/registry` | `research/{models,pipeline}.py`, `experience/evaluator.py` |
| `topic/experiments` | `research/{forward_test,streaming_replay,replay_benchmark}.py` |
| `topic/health` | `API.md` health/preflight/diagnostics + page wiring |
| `topic/uiguide`, `topic/faq`, `topic/glossary` | this feature's UI + the sources each answer cites |

When a source file above changes a pinned value, `research_handbook.test.js`
fails in CI — update the handbook entry (and its `ref`) to match the code,
never the other way around.
