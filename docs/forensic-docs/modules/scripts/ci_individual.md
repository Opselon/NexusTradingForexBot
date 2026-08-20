# scripts/* diagnostic + CI tools (individual pages)

## scripts/ci/make_ci_results.py
- **PURPOSE:** Per-check CI result aggregation — reads the workflow's
  per-check status and writes `ci-results/run-info/*.json` (the files the
  final "fail job if any check failed" step consumes). The CI
  truth-aggregation point.
- **ARCHITECTURE LAYER:** CI tooling.
- **RESPONSIBILITY:** translate check outcomes (ruff/mypy/pytest/...) into
  the machine-readable status artifacts; NO fake green: a failing check
  must produce a failing status file.

## scripts/ci/telegram_notify.py
- **PURPOSE:** The CI→Telegram notifier (HTML, tag-safe split, secret
  redaction, NEXUS-CI-<run>-<sha4> correlation, sendDocument diagnostics).
  EXIT 0 ALWAYS — CI never fails on Telegram.
- **ARCHITECTURE LAYER:** CI tooling.
- **RESPONSIBILITY:** deliver start/finish/failure notifications with the
  diagnostic bundle; failures in the notifier itself are logged and
  swallowed (observability, not gatekeeping).

## scripts/build/update_helpers.py
- **PURPOSE:** ps1-safe build helpers (token-guard / scan-tree / manifest /
  sbom actions) invoked from build_release.ps1 — the fix for the
  unparseable-inline-python rule (never inline multi-line python with
  quotes in .ps1).
- **ARCHITECTURE LAYER:** Release tooling.

## scripts/gen_70d_parity_report.py / gen_governance_golden.py /
## inference_latency_benchmark.py / news_readiness_report.py
- **PURPOSE:** One-off report generators (70D parity report, governance
  golden baseline, inference latency benchmark, news readiness report) —
  deterministic generators of the tracked golden baselines under
  docs/ (e.g. LIQUIDITY_70D_GOLDEN_BASELINE.json) used by the regression
  suites.
- **ARCHITECTURE LAYER:** Tooling (deterministic, standalone).
- **RESPONSIBILITY:** produce reproducible JSON/markdown reports from the
  codebase state so CI regression tests have stable comparison inputs.
- **EDGE CASES & PITFALLS:** reports must be deterministic (stable sort,
  fixed precision, UTC); regenerating a golden file changes the regression
  baseline BY DESIGN (commit together with the change).