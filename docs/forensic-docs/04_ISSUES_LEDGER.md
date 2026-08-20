# 04 — Issues Ledger (found during the documentation pass)

## Architectural risks

1. **`aggregate_bars` re-scan per tick (scalp_features.py ~741-744)** — the
   4 HTF aggregations (M15/M30/H1/H4) re-scan the full bar list on EVERY
   tick. Correct but O(n) per tick; an incremental/cached aggregation would
   cut the single most expensive line in the feature module (P3).
2. **Risk tier table in code vs skill doc** (risk_engine.py 191-199):
   equity<1k → 0.10 lots and <10k → 1.00 lots; the skill's older table
   (0.50/2.00) never matched. Code is truth; docs should be corrected.
3. **Dead expression at scalp_features.py:879** — the 50% impulse-equilibrium
   expression is computed and discarded (no assignment). Harmless, but it
   looks like the used path; remove or bind it.
4. **Hard-coded ATR fallback 1.50** (scalp_features.py cold start +
   validate_and_fallback) — a magic constant; if the symbol's volatility
   regime changes materially, warm-up normalization biases. Worth an
   explicit constant + rationale.
5. **`norm_rsi` divisor 16.66 vs docs /25** (BUG-082 class,
   to_tensor_input:358) — code is truth, but the divisor is a magic number
   worth a named constant.
6. **feat_38/feat_39 exact negations** (corr -1.0 over stored experiences)
   — the model sees a redundant dimension pair; quality observation, not a
   bug.
7. **DB index debt (audit 2026-08-18)** — audit_orders lacks
   (ticket, order_id); COALESCE(NULLIF(close_time,''),timestamp) ORDER BY
   defeats indexes on audit_ledger/audit_broker_trades (P3).
8. **`/api/news/keywords` hot path** (analyze_keyword_coverage) —
   regex-per-keyword-per-article recompilation ~94,500 per 500 articles;
   precompile once (documented fix in the news-keywords reference).
9. **walk_forward_trainer stale TASK-1 diagnostic log** (~329-340) — the
   log block says "0=BUY, 1=SELL, 2=NO_TRADE"; the real label_map is
   0=NO_TRADE, 1=BUY, 2=SELL. Diagnostic-only, but misleading on a live
   training run.
10. **SignalPolicy UNSAFE_REGIMES set is string-coupled** (policy.py 131)
    — values must match RegimeType enum values; a renamed regime silently
    stops being blocked. Consider deriving from the enum.
11. **risk_engine high_confidence_threshold split-brain** — ctor default
    0.70 vs the evaluate path's getattr fallback 0.95; the effective
    branch uses 0.95. Align the ctor default with the config default.
12. **trade-off: 60s grace period** (order_manager arbitration) — instant
    protective exits are suppressed in the first minute (except kill
    switch). Documented and intended, but it means an instant hard-loss
    waits 60s. Accepted design.
13. **14 model_generation pages initially missed by the delegated worker**
    (iteration budget) — covered by the lead; a reminder that delegation
    needs budget-aware batching.

## Hidden bugs / risky behaviors noticed (all pre-existing, none introduced)

- retcode-0 semantics at the broker boundary (0 is NOT a trade retcode) —
  handled by retcode_label/cancel-verified paths; keep the discipline.
- Offline packet capture: secrets must never enter the persistent config
  projection (redaction contract in runtime_config) — verify on any
  change.
- print() in dispatch_order logs "REAL ORDER/EXECUTION EXECUTED ON BROKER
  SERVER" unconditionally for the PAPER adapter too (paper fills also
  print this line) — cosmetic, could confuse log readers.

## Delegated-worker findings (harvested from wave-1 summaries)

14. `accounting/core.py:516` — `equity_curve` renders `snap.floating_pnl or 0.0`
    (a synthesized 0.0 in chart payloads). Minor + intentional rendering
    default, but it brushes the no-synthetic-numbers rule — consider None.
15. `reporting/__init__.py` — `classify_session`/`compare_periods` defined in
    insights.py are absent from `__all__` (public-surface drift vs engine.py
    which imports them by path).
16. `experience/retriever.py` — context fingerprinting uses its own substring
    rules (drift risk vs the canonical keys).
17. `intelligence.py:406` — hardcodes `timeframe="M1"` for a history read
    (symbols with another native timeframe would mis-read).
18. `experience/quality.py:306/400` — exit-reason matching is substring-based
    (a reason-code taxonomy change silently widens matches).
19. `intelligence/gate.py:15` — the WARN tier never changes the proposal
    (informational by design — documented, not a bug).

## Delegated-worker findings — wave 3 (research/news/governance)

20. `research/worker.py:194-206` — `tick()` returns False for BOTH throttled
    and failed states; callers must inspect `last_error` to distinguish.
21. `research/models.py:210-213` — `win_rate` divides wins by total_trades
    (breakevens included in the denominator) — the Phase-16 denominator
    discipline does not extend to the research score model.
22. `research/leakage.py:68-92` — `validate_no_train_leakage` /
    `backtest_properly_fit` are no-ops unless callers pass True
    (contract documentation, not enforcement).
23. `news/analysis/consensus.py:109` — dead `src.tier` expression inside an
    else-branch where `src` is None (the ternary already guards).

## Technical debt

- 4,000-bar cap and 900-bar chart window are constants (fine) but not
  config-tunable; the UI bundle version check (BUG-079) is a one-shot log.
- `Web/index.html` hand-maintained indentation is a permanent fragility
  (BUG-068/BUG-120 class) — a strict div/section balance check + tab-
  nesting test is the only guard; consider a formatter pass as a separate,
  carefully-tested change.