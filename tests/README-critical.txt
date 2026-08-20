# ---------------------------------------------------------------------------
# CRITICAL TEST SUITE (fast default CI gate)
# ---------------------------------------------------------------------------
# This is the small, high-signal regression net. It runs on EVERY push and
# protects the trading system where failure actually matters:
#   MODEL * TRAINING * CHAMPION/SHADOW * ACCURACY * FEATURE/DATA CONTRACT *
#   ACCOUNTING * RISK * EXECUTION * BACKTEST * WALK-FORWARD * OOS *
#   STRATEGY FACTORY * WHOLE-APPLICATION CYCLE
# Full rationale: tests/unit/README-TEST-SUITE-REDUCTION.md
# The huge historical suite is GONE (deleted, not hidden). The Critical suite
# plus the surviving medium-value suites must stay green on every push.
#
# Run locally:
#   pytest $(cat tests/critical_suite.txt | tr '\n' ' ')
# or simply:  pytest -m critical   (after marker registration below)
#
# NOTE: pytest markers are registered in pyproject.toml ([tool.pytest.ini_options].
# markers). This file is the SUITE MANIFEST consumed by scripts/ci/make_ci_results.py
# and by the ci.yml 'quality' job to run exactly the critical set.
# ---------------------------------------------------------------------------