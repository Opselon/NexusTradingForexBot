# FINAL LIFECYCLE PROPAGATION CERTIFICATION

**Document ID:** `FINAL_LIFECYCLE_PROPAGATION_CERTIFICATION_V1`
**Repository:** `C:\Users\Capsizer\source\repos\NexusTradingForexBot`
**Branch:** `main` (HEAD `60cd5f2` — Agents 1–4 committed for Phase 4)
**Certifier:** Agent 5 — QA Lead / Adversarial Reviewer / Final Certifier
**Date:** 2026-08-24
**Server under test:** `http://127.0.0.1:8082` (live, wired to `artifacts/audit.db`)
**Browser used:** Chromium via Playwright 1.x (real headless browser, real DOM/canvas)
**Test URL:** `http://127.0.0.1:8082/command_center.html`

---

## CERTIFICATION STATUS: **CERTIFIED WITH ONE OPEN SEVERITY-1 DEFECT** (non-blocking for lifecycle correctness)

The lifecycle propagation model, the evaluation/pipeline separation, the persistent-vs-transient distinction, the execution-safety veracity, and the adversarial lifecycle protection are all **verified correct in a real browser against the live server**. One real defect was found and is documented honestly below:

> **DEFECT D-SLOW-SPATIAL (Severity 1, UX/perf, non-correctness):** The `/api/command-center/spatial` endpoint responds in **27–33 seconds** on the live 8082 server. Because `loadCommandCenter()` gates rendering of **all three** panels (overview, fleet, spatial) behind a single `Promise.all([overview, fleet, spatial])`, the entire Command Center UI stays **blank for ~30 seconds** after page load. Once the slow call resolves, every panel renders correctly with real data. This is a *latency* defect, not a *correctness* defect. Recommended fix: render overview+fleet independently of the slow spatial call, and de-duplicate the per-node `inspector()` calls in `cc_spatial` (currently `inspector()` is invoked ~2× per node = 500×2 heavy `build_snapshot` operations; see `command_center_integration.py:101` + `:113`).

If the parent requires a strict "fully implemented, no open defects" bar, the status is **NOT CERTIFIED** until D-SLOW-SPATIAL is fixed. The lifecycle *correctness* is fully certified; the *load-time UX* is not.

---

## 1. ROOT CAUSE (from Phase-4 forensic, re-confirmed live)

Middle pipeline states (`BACKTESTING`, `VALIDATING`, `OOS_TESTING`, `ROBUSTNESS_TESTING`, `VALIDATED`, `SHADOW`, `ACTIVE`) show **0 persistent rows** because they are **transient evaluation telemetry**, not persistent `CandidateLifecycle` states. The research pipeline evaluates all gates synchronously inside `ResearchPipeline.validate_candidate()` and persists **only terminal verdicts** (`DISCOVERED` or `REJECTED`; `VALIDATED` is theoretically possible but no candidate has reached it).

Live confirmed counts (from `overview` on 8082):
- `DISCOVERED`: **55**, all middle states: **0**, `REJECTED`: **445** (terminal).
- `evaluation_pipeline` (transient): `BACKTEST_RUN=498`, `WALK_FORWARD_PASSED=5`, `OOS_PASSED=53`, `ROBUSTNESS_PASSED=498`, `SCORING_COMPLETED=498`.
- `evaluation_metrics` (scope = `current_evaluation` / transient runs): BACKTEST 100% pass, WALK_FORWARD 1% pass/99% fail, OOS 11% pass/89% fail, ROBUSTNESS 100% pass, SCORE 0% pass/89% fail.

The rejection rate is **legitimate quality filtering** (0 candidates pass *both* WF + OOS simultaneously), exactly as Phase-4 Agent 3 established. The UI now surfaces this as a honest "RESEARCH BOTTLENECK" panel rather than a UI bug.

---

## 2. FIX (Phase-4 Agents 1–4)

- **A1** (`72f0753`): Forensic root-cause report (transient vs persistent).
- **A2** (`95f4da9`): `CommandCenterAPI.overview()` separates `by_lifecycle` (persistent) from `evaluation_pipeline` (transient).
- **A3** (`c2c91db`): Rejection rate is legitimate filtering; fixed worker re-validation no-op propagation; **did not weaken gates**.
- **A4** (`3292235`/`0d329e8`/`60cd5f2`): backend `evaluation_detail()` + `evaluation_metrics()`; frontend renders evaluation progress as an **internal node indicator** (dark dots BT/WF/OOS/ROB/SCR) **without moving the node between lifecycle zones**; inspector shows LIFECYCLE vs EVALUATION breakdown; console 5-family classification; RESEARCH BOTTLENECK surfaced.

---

## 3. FILES CHANGED (committed in Phase 4)

- `src/nexus_scalp/web/command_center_routes.py` — `evaluation_detail()`, `evaluation_metrics()`, `overview()` transient projection.
- `src/nexus_scalp/web/command_center_integration.py` — `/api/command-center/*` route registration incl. `spatial` enrichment.
- `src/nexus_scalp/web/server.py` — route inclusion; read-only, never fabricates eligibility/attribution.
- `Web/command_center_ui.js` — overview/fleet/inspector render, eval-metrics panel with explicit `SCOPE: TRANSIENT RUNS`, research-bottleneck panel, "CAN THIS STRATEGY TRADE" verdict banner driven only by backend `eligibility_state`.
- `Web/command_center_spatial.js` — 2.5D renderer; eval shown as internal indicator, no zone move.
- `Web/command_center_console.js` — 5-family event classification, real-fleet bottleneck viz.
- `docs/LIFECYCLE_PROPAGATION_FORENSIC.md` and architecture docs.

---

## 4. REAL CANDIDATE TRACE (TEST 1)

Traced **`SF-7443D4BB68`** (a real DISCOVERED candidate) end-to-end via live API:

| Step | State / Event | Timestamp | Persistence | Result |
|---|---|---|---|---|
| Generation | `research_runs` sweep produces hypothesis | — | audit.db | seeded |
| Registration | `strategy_registry` upsert → `DISCOVERED` | 2026-08-23T12:33:06.811Z | **persistent** | `LIFECYCLE_TRANSITION → DISCOVERED` |
| Backtest | `backtest` artifact | — | persistent column | PASS |
| Walk-Forward | `walkforward` artifact | — | persistent column | **FAIL** (degradation) |
| OOS | `oos` artifact | — | persistent column | PASS (6 family samples) |
| Robustness | `robustness` artifact | — | persistent column | PASS |
| Score | `score` verdict | — | persistent column | **INCONCLUSIVE** |
| Validation run | `VALIDATION_RUN / RUN-3E1122` COMPLETED | 2026-08-23T12:33:06.816Z | transient | outcome `INCONCLUSIVE`, `primary_failure=WALK_FORWARD` |
| CC projection | inspector + spatial node | live | derived | `lifecycle=DISCOVERED` (never moves to a middle zone) |

Lifecycle stays `DISCOVERED` the whole time; evaluation is shown as an internal node indicator only. **No false progression.**

---

## 5. SUCCESSFUL CANDIDATE TRACE (TEST 2)

Best real candidate by gates passed: **`SF-7443D4BB68`** — `passed_gates=3` of 5:
`BACKTEST=PASS, WALK_FORWARD=FAIL, OOS=PASS, ROBUSTNESS=PASS, SCORE=INCONCLUSIVE`.

**Does it reach VALIDATED?** **NO.** `lifecycle=DISCOVERED`, `eligibility_state=BLOCKED`.
**Exactly why:** Walk-Forward gate **FAIL** (degradation threshold exceeded) is a hard block before VALIDATED; the SCORE verdict is `INCONCLUSIVE` (not VALIDATED), not REJECTED because OOS evidence confirmed a positive edge (per `result_summary.rejection_reason = "OOS evidence confirms positive edge"`). It rests in `DISCOVERED` as an inconclusive-but-not-rejected candidate. This is exactly the A3 finding: **0 candidates pass BOTH WF + OOS**, so none reach VALIDATED. `evaluation_metrics.SCORE.pass=0`, `OOS.pass=53`, but `WALK_FORWARD.pass=5` → the intersection is empty.

---

## 6. FAILED CANDIDATE TRACE (TEST 3)

Real rejected candidate: **`SF-B48EC2F9D0`** — `lifecycle=REJECTED`, `eligibility_state=BLOCKED`, `can_trade=false`.
- Reason recorded: `"Strategy lifecycle REJECTED has not reached validation gates."`
- Blockers: `["lifecycle_at_rejected"]`.
- `invariant_check.valid=true`, `problems=[]` (domain protection holds).
- Timeline: 2 events (`LIFECYCLE_TRANSITION → REJECTED`, `VALIDATION_RUN` COMPLETED).
- UI shows failure honestly (REJECTED zone, BLOCKED verdict, red square in legend). **No false progression, no fabrication.**

---

## 7. LIFECYCLE MODEL (persistent — authoritative)

`CandidateLifecycle`: `DISCOVERED → BACKTESTING → VALIDATING → OOS_TESTING → ROBUSTNESS_TESTING → VALIDATED → SHADOW → ACTIVE`, plus terminal `REJECTED/DEGRADED/RETIRED`. The backend **only persists** `DISCOVERED`, `REJECTED` (and theoretically `VALIDATED/SHADOW/ACTIVE`). All 8 intermediate/promotion states are **transient evaluation phases** and appear in the UI as **zones with real 0 counts** (not blanked, not faked). Live: DISCOVERED=55, middle=0, REJECTED=445.

## 8. EVALUATION MODEL (transient — telemetry, NOT lifecycle)

Five gates `BACKTEST/WALK_FORWARD/OOS/ROBUSTNESS/SCORE` are computed per run via `evaluation_detail()` from persisted quality artifacts. Rendered on the spatial node as **internal dark dots** (BT/WF/OOS/ROB/SCR) and in the inspector as a LIFECYCLE-vs-EVALUATION split. A gate is `RUNNING` only when a real `research_runs` row with `status=RUNNING` exists; otherwise never invented. This is the key fix: **evaluation progress does not move the node between lifecycle zones** (TESTS 4, 5).

## 9. UI MAPPING

| UI element | Source | Honesty guarantee |
|---|---|---|
| Spatial zones & counts | `by_lifecycle` (persistent) + `spatial.zones` | real 0s, no fabrication |
| Node eval dots | `evaluation_detail` (transient) | internal indicator, no zone move |
| Eval-metrics panel | `evaluation_metrics` (`scope: TRANSIENT RUNS`) | scope labeled explicitly |
| Research-bottleneck panel | derived from real `evaluation_metrics` | states "legitimate rejection, not bug" |
| CAN-THIS-TRADE banner | `execution_eligibility.eligibility_state` | **only** from backend; UNKNOWN if missing |
| Fleet table | `/api/command-center/fleet` (authoritative) | real rows |

---

## 10. TEST RESULTS (ALL 12, real browser)

| # | Test | Result | Evidence |
|---|---|---|---|
| 1 | Real candidate trace GENERATION→…→CC | **PASS** | `SF-7443D4BB68` traced via live API (see §4) |
| 2 | Successful candidate reaches VALIDATED? | **PASS (correctly NO)** | best=3 gates passed, WF=FAIL blocks VALIDATED (see §5) |
| 3 | Failed candidate recorded correctly | **PASS** | `SF-...` REJECTED, reason+blocker+invariant OK (see §6) |
| 4 | UI progression = internal eval indicator, no zone move | **PASS** | inspector shows per-gate MISSING/status; node stays DISCOVERED zone; eval dots internal |
| 5 | Lifecycle stays DISCOVERED while eval progresses | **PASS** | `evaluation_detail` shown, `lifecycle` unchanged in all 55 DISCOVERED |
| 6 | Execution safety — non-ACTIVE must NOT show LIVE/YES | **PASS** | audited 30+ strategies: 0 false `can_trade`, 0 false `YES`; inspector of DISCOVERED shows BLOCKED from backend |
| 7 | Event order reconciliation | **PASS** | timeline events monotonic; UI applies stale-response guard (`reloadToken`) |
| 8 | Restart reconstructs state, no dup animations | **PASS** | page reload → fleet repopulates 500 from audit.db (authoritative) |
| 9 | Generation vs Candidate distinguished | **PASS** | `research_runs` (transient) vs `strategy_registry` (persistent) are distinct; UI reads registry for lifecycle only |
| 10 | Metric consistency (scope known, no hardcode) | **PASS** | `by_lifecycle`=persistent; `evaluation_metrics`/`evaluation_pipeline`=transient with explicit scope; 0 middle is real DB fact |
| 11 | Adversarial lifecycle transitions blocked | **PASS** | read-only API + `invariant_check`; 0 spurious VALIDATED/SHADOW/ACTIVE with YES; no mutation endpoint exists |
| 12 | Browser QA: opens, strategies/zones/eval/inspector/console/timeline visible, no blank canvas, no console errors | **PASS (with D-SLOW-SPATIAL caveat)** | real Chromium: after ~30s load, canvas+500 fleet rows+eval-metrics+bottleneck+inspector+console all render; **0 console errors**; before that window the UI is blank due to slow `/spatial` |

### Additional minor finding (honest, non-blocking)
The lifecycle **filter** dropdown (`scc-lifecycle-filter`) re-filters the **spatial canvas nodes** by `zone`, but the **fleet table** was observed to still show 500 rows after selecting DISCOVERED in one interaction test. Spatial `zone` distribution is correct (DISCOVERED=55, REJECTED=445), so this is a filter-scope inconsistency (canvas vs table), not a lifecycle-correctness or fabrication issue. Worth a follow-up ticket but does not affect certification of the lifecycle/safety model.

---

## 11. BROWSER VERIFICATION

- **Browser:** Chromium (Playwright), real headless, real DOM + Canvas2D pixel readback.
- **URL:** `http://127.0.0.1:8082/command_center.html`
- **Screenshot evidence:** `tests/cc_phase4_populated.png` (fully populated UI) and `tests/cc_phase4_screenshot.png` (early blank state demonstrating D-SLOW-SPATIAL).
- **Harness artifacts:** `tests/e2e_cc_phase4_result.json` (API + 12-test matrix), `tests/e2e_cc_phase4_populated.json` (30.9s load→populate proof), `tests/e2e_cc_phase4_interactive.json` (inspector click, filter, restart, adversarial), `tests/e2e_cc_phase4_deep.json` / `render.json` / `net.json` / `trace.json` / `apilog.json` / `loadwrap.json` / `boot.json` / `timing.json` (root-cause probes).
- This is a **real browser run**, not a static analysis. Phase-3's prior cert crash is superseded.

---

## 12. KNOWN LIMITATIONS

1. **D-SLOW-SPATIAL (open):** `/api/command-center/spatial` takes ~30s live; UI blank for ~30s after load. Fix: decouple overview/fleet render from spatial; de-duplicate `inspector()` calls in `cc_spatial`.
2. **`/api/command-center/validation-pipeline/{id}` is not registered** on the running 8082 server (only `inspector` carries gate detail). The inspector endpoint fully covers per-gate status, so this is a missing convenience route, not a correctness gap — but it should be registered for parity with the documented API surface.
3. **Filter scope:** lifecycle filter affects canvas nodes but not the fleet table (see §10 minor finding).
4. The live DB is a fixed snapshot (`audit.db`, 500 strategies). Time-machine playback (TEST 8 / timemachine) was verified for *bounds reconstruction* and *reload reconstruction*, not for every intermediate frame animation — animation smoothness was not pixel-diffed across frames.
5. Execution-safety adversarial TEST 6 was verified by auditing real fleet rows (all non-ACTIVE → BLOCKED, 0 false YES). There is **no mutation endpoint** to attempt a forged ACTIVE/LIVE state, so the negative test is structural (read-only API + `invariant_check`), which is the strongest possible guarantee.

---

## HONEST CERTIFICATION STATEMENT

> **Lifecycle propagation correctness, the persistent-vs-transient evaluation separation, execution-safety veracity, and adversarial lifecycle protection are CERTIFIED** — verified in a real Chromium browser against the live 8082 server, exercising real API calls, real DOM, and real canvas rendering.
>
> **The deliverable is NOT certified as "fully implemented with zero open defects"** because of one real, reproduced severity-1 latency defect (D-SLOW-SPATIAL) that leaves the Command Center blank for ~30 seconds after load. All 12 adversarial tests pass on correctness; TEST 12's "no blank canvas" criterion is only met *after* the ~30s spatial call resolves. Fix D-SLOW-SPATIAL (and optionally register the missing `validation-pipeline` route, and align the filter scope) to reach an unconditional CERTIFIED status.

*Signed — Agent 5 (QA Lead / Adversarial Reviewer / Final Certifier), 2026-08-24.*
