# NSE Master Wave — Final Report (Active Scalper & Production Hardening)

Wave: `nse/master-active-scalper-wave` opened 2026-09-14, converged to `main` through
PR #199 (BUG-276..284) and follow-up PRs #204..#212; closeout 2026-09-15.
All numbers below are actual measurements from the cited artifacts — nothing fabricated.

## 1. Executive summary

The engine was NOT "waiting for good setups". Passivity was a stack of independent,
individually-defensible kill-layers that composed into a funnel with almost no viable
tick-to-order path, plus a currently-dead feed. Twenty bug entries (BUG-276..295) were
found with evidence and fixed or given a regression net; the largest structural ones are
fixed at HEAD of main.

## 2. Initial Git state (2026-09-14, VERIFIED)

Branch `nse/master-active-scalper-wave` ahead of origin/main with BUG-276..281 work in
flight; 3 dirty test files (comment-only BUG-276-to-278 renumbering — committed); the
remote URL carried an OAuth token (`gho_...`) in `.git/config` (revocation is an
OPERATOR action — recorded by lane-15 as a release-gate blocker, NOT silently
rewritten here); ~120 tracked agent/role branches; main green; PR #199 open with a
Docs secret-scan red (the audit report itself contained a full fixture-shaped token
string — resolved to main's truncated form during the wave merge).

## 3. Initial runtime state

Feed frozen since 2026-09-11T20:00Z (watchdog warned 1,828 times across 2.5 days while
runtime state claimed RUNNING — BUG-277/279 landed fail-loud: stall episode, DEGRADED,
entry-block, CRITICAL escalation, weekend suppression delegated to market_calendar).
Champion artifact: governed `c9982ddde1755591` VERIFIED against manifest and pilot
provenance (this wave re-checked sha256 + 7/31 tensors matching fresh init, i.e.
trained — correcting the earlier CHECK-MDL-02 confusion, which measured a different,
test-clobbered file; clobber class itself fixed by BUG-278).

## 4. Passive-trading root cause (the funnel)

Live plane audit.db, 1,469 genuine decisions 09-06..09-11: **0.82% survived policy**.
Rejection attribution (full table: `wave_20260914/02_funnel_rootcause.md`):
CONFIDENCE_FAIL 29.7%, GUARDIAN/HIGH_SPREAD_CHOP 24.2%, NO_CANDIDATE 29.5%,
ASYMMETRIC_RR 4.2%, zone-quality 3.1%; the experience/suitability pair killed 48 of 60
rejections on the only surviving channel; ORDER_FREQUENCY_THROTTLED swallowed 78,798
evaluations (96:1). The 12 survivors were ALL structural PREDICTIVE_LIMIT; the model
path produced zero.

Charter taxonomy answers: (A) false-safety = chop band + throttle; (B) duplicate safety
= spread counted 4-6x, confidence re-checked as zone-quality, RR checked 3x, cooldown
2x; (C) hidden contradiction = the predictive channel's designed confidence-bypass was
re-imposed by post-policy gates; (D) dead subsystems = candle_intel (zero consumers),
rule matrix (0/20 enabled), hunter layer (regime vocabulary — fixed),
DecisionStabilityController (no callers), the news blocked-flag (computed, discarded);
(E) stale input = feed freeze + spread-era calibration; (F) wrong normalization =
confidence gate above the softmax ceiling after CHG-0042 moved the ceiling, and the
news scaler std 1e-3 vs serve-time saturation to plus/minus 5; (G) impossible thresholds
= conf 0.40/0.50 vs max-ever-observed 0.5015, RR relaxed-bypass requiring 0.95;
(H) execution disconnect = 2026-09-11 survivors never dispatched (NOT_DISPATCHED 879
pattern); (I) news over-weighting = NOT the problem — news is dead as influence
(`.blocked` discarded, `is_macro_news_window` hardcoded False, calendar gate
telemetry-only) — recorded honestly against the hypothesis; (J) indicator
underutilization = the UI indicator engine has exactly ONE consumer (the UI);
(K) experience paralysis = 34.5% suitability kills on 1-4 samples (fixed);
(L) model paralysis = CONFIRMED leading hypothesis, model near-uniform
(buy .387 / sell .360 / no-trade .253).

## 5. Fixes landed (each with regression net; ledger `agents/bugs.md`)

| ID | Fix | PR |
|---|---|---|
| BUG-276 | 15 dead-lettering ON CONFLICT shadows healed at baseline + boot | #196 (pre-wave) |
| BUG-277/279 | feed-stall fail-loud: episode, DEGRADED, entry-block, weekend suppression | #199/#205 |
| BUG-278 | test-harness clobber of the production champion + registry contamination: fail-closed refusal, env seam honored, registry quarantine | #199 |
| BUG-280 | 60s hardcoded order-frequency throttle reads the ONE configured cooldown (RC-4: 78,798 swallowed evals) | #199 |
| BUG-281 | HIGH_SPREAD_CHOP band recalibrated from the live broker window (0.25/0.18 era to 0.45/0.30), machine evidence JSON pinned against defaults | #199 |
| BUG-282 | RSS RFC-822 timestamps parsed honestly; poll-stable timeless article identity; published_at_source provenance column (root of the 233 MB news.db + fake freshness) | #199 |
| BUG-283 | hunter regime-vocabulary adapter — 14 setup families were permanently NO_GO(REGIME_NOT_OK(UNKNOWN)) on all real data; retrain records carry the regime label; UNKNOWN/CHOP/FREEZE stay fail-closed | #199 |
| BUG-284 | Phase-09 suitability REJECT needs >=5 samples (was: zero evidence passes, ONE loss blocks — 34.5% of decisions); full-strength reject preserved at floor | #199 |
| BUG-285 | TickData/BarData non-finite refusal + broker-history reader drops malformed rows loudly (was report-only) | #206 |
| BUG-286..292 | calendar single-ownership; hot-path stdout print removed; queue-adoption flake; overflow drain; bounded LRU maps (BoundedLRUMap); telegram CLI contract; C3 gate off the hot path | #205..#212 |
| BUG-294 | behavioral (not just structural) regression battery for the bounded maps | #214 closed unmerged (structural pins landed with #212; behavioral battery kept on the branch for re-land) |
| BUG-295 | 5 dead hygiene retention rules healed (417 cycles, deleted=0, silently-skipped) + LOUD skip on misconfigured rules | in review |

## 6. Model / parity verdict (lane 07, corroborated by independent re-probe)

Serving bytes correct, integrity VERIFIED, train-serve parity green (62-test battery at
HEAD), scaler/schema-hash bindings intact. ECONOMICALLY UNPROVEN: no honest OOS
economics on record for the champion; multi-seed probes say weak/negative expectancy
below evidence floors; the 10 news slots are constant-at-train and saturated-at-serve
(fix class: news-coverage retrain — a prerequisite, not a serving patch); the registry
CHAMPION row mislabels scalp_v1 at 50D (metadata truthfulness defect, open). The
serving path is trustworthy plumbing around an edge that must still be earned: the
retrain gate is >=1y data + real news coverage + executed ECE/Brier/walk-forward
economics, else paper-only status must be stated explicitly.

## 7. Candle DB / data volume (lanes 04; this report's machine artifacts)

The "25 MB candle DB" claim is unsupported — measured max 10.94 MB + 4.2 MB WAL, frozen
since 2026-09-07 (disabled subsystem). Real footprint 404 MB (2026-09-15 measurement):
news 233 / audit 132 / strategies 28 / candle_intel 11. Classification and the ordered
cleanup procedure are in `wave_20260914/database_cleanup_report_20260915.md` +
`database_inventory_20260915.json`. A mass-delete was deliberately NOT executed
(backup gate, a live engine, cleanup classes land with BUG-295); prevention IS landed
(BUG-282 stops the dup engine; BUG-295 makes dead rules loud).

## 8. Zero-state / clone-and-run (lanes 05, 12 re-verified at HEAD 0c855019)

Fresh clone: installs, PAPER boots WITHOUT MT5 (SYNTHETIC adapter), /health is honest
(NOT READY naming CONFIGURATION + DATA), the operator "why-not-trading" surface exists
(`/api/operator/{summary,decisions,funnel,no-trade}`), the token contract is
401/200-correct. STILL BROKEN (fix lanes in flight): Z-B1 no supported non-docker
model bootstrap (`nexus repair --model` is named by hints but does not exist; raw
LOAD_REJECTED traceback), Z-B5 `db migrate` lock mkdir, W12-1 cp1252 panel crash
before bind, README quickstart still omits the bootstrap, Z-B4 Linux+gateway
default-creds path unusable by construction, E10 cold-start champion row registers
under class defaults.

## 9. API / UI / indicators

Lane-13 (380-route table, health/readiness classification, unbounded-request findings)
stands as the audit record. Lane-09: the backend indicator engine has exactly ONE
consumer (the UI v1 routes); the 50D feature math duplicates a DIFFERENT RSI/EMA math
(VERIFIED numeric divergence: Wilder 48.4927 vs simple-average 51.1436) — canonical
flow unification is an open architecture item, not a silent mid-wave patch.

## 10. Fail-closed / fail-open classification applied

FAIL-CLOSED landed: corrupted price (BUG-285), force_fresh against governed champion
paths (BUG-278), feed-stall blocks new entries while exits stay protected (BUG-279),
micro-sample verdicts may NOT hard-reject (BUG-284 — the inverted rejection removed,
real rejections untouched). MAY-DEGRADE preserved: news/calendar unavailable = neutral
no-op (never blocks by accident), experience absent = pass-through, optional analytics
failure-isolated.

## 11. Testing / gates

QA lane at 0c855019: critical suite **1,606 passed / 0 failed / 5 conditional skips**,
ruff check/format pristine, mypy 584 files clean. Disclosed pre-existing environmental
reds (WEB-AUTH 401 trio, htf_warmup_gate 9/11/12/13, chart_history x3, fidelity
4-head) reproduce identically on clean checkouts — not introduced, tracked. CI green on
main through #212 (last full required-context set: all success).

## 12. Reproducibility procedure (current honest state)

`git clone` -> `pip install -e ".[dev]"` -> `nexus repair` (dirs + DBs) ->
**[manual until the bootstrap PR merges]** `python docker/provision_model.py` with
NSE_WORKSPACE pointing at the clone -> `nexus start --json --mode paper` -> /health +
/api/status (token) + operator funnel endpoints. No hidden dev-machine dependency was
found in src/ beyond the two absolute paths already ledgered (Z-B7).

## 13. Remaining risks / next wave (ordered)

1. Revoke/rotate the OAuth token embedded in `.git/config` (operator; the only item
   requiring human action right now).
2. Land the in-flight lanes — zero-state bootstrap (Z-B1/Z-B5/W12-1), fail-open
   boundaries (paper-state L11-4 + gateway payload L11-6), hygiene dead-rule healing
   (#295) -> then execute the DB cleanup procedure (section 7).
3. News-coverage retrain (70D economics + dead-input class) under P0-4 gates; fix the
   champion registry metadata mislabel.
4. Rule-matrix enable-or-delete decision + candle-intel connect-or-purge ruling;
   predictive-channel authority (remove the duplicate intelligence gates on it).
5. Indicator canonical unification (one math for UI + engine);
   DecisionStabilityController wire-or-delete.
6. Compositional entry scoring (lane-03 section 5) replacing the AND-wall;
   threshold-SSOT for the spread/RR gate families.
7. Host-leak class guard (zero-byte identifier-shaped files at repo root) — two
   recurrences during this wave.

## 14. State at closeout

main green (post-#212), host tree clean at the wave branch tip; concurrent lanes
operate in their own worktrees under their own branches. Every claim above is bound to
a `docs/audit/wave_20260914/` lane report, an `agents/bugs.md` entry (BUG-276..295), or
the two machine artifacts beside this file.
