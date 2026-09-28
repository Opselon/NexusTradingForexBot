# Backtest report: scope and interpretation

## Where to view

In the legacy Web dashboard, validate a Research candidate or open a Strategy Factory benchmark. The detailed report appears alongside the existing walk-forward, OOS, robustness and score gates. Download JSON saves the report locally; it does not send data to another service.

New reports travel in `backtest.report`; Command Center validation responses expose `backtest_report`. Previously stored results without a report remain readable, but no detailed history is invented for them. Run validation again to produce a new report.

## What is actually tested

`NSE_EMPIRICAL_REPLAY` revalues recorded, closed experience trades selected by the research pipeline. It does **not** execute an MQ5 Expert Advisor in MT5 Strategy Tester and is not proof of broker real-tick quality. Candidate filtering, temporal splits and independent validation gates remain in force. The report does not approve or promote strategies.

With `BacktestEngine.run(use_split=True)`, report trades and metrics use only train plus validation observations, excluding purged/embargoed observations and OOS. Full-dataset diagnostic runs are labeled separately. Dataset source, supplied provenance, schema IDs, selected counts and configured split settings are retained.

## Units and economics

- R and USD are separate metric spaces. USD drawdown uses the closed-PnL USD curve, not the R curve.
- Recorded trade outcomes receive the configured additional spread/slippage stress. This is a model, not a reconstruction of broker fills. Added costs must never reduce the size of a losing USD trade.
- Economic assumptions describe the modeled account/friction/swap/sizing configuration. The separately labeled sized result is a counterfactual revaluation, not the observed account balance.
- Curves show cumulative outcomes; they are not mark-to-market equity curves. A recorded starting deposit must not be fabricated from a zero-based cumulative PnL curve.
- Profit factor with no losses and undefined statistics are unavailable rather than infinity. Report JSON must contain no NaN/Infinity values.
- Fields absent from the experience schema (including MT5 tick quality, native order records and floating equity drawdown) remain unavailable with explanations. Zero means a recorded or computed zero, not missing evidence.
- Trade-detail previews are capped and disclose total/returned counts. Summary metrics are calculated from all selected trades, not just the visible rows.

## Native MT5 boundary

The external `mt5-backtest` skill is a workflow reference for compilation, Strategy Tester configuration, real-tick mode, progress monitoring and native report inspection. It does not install MT5 or provide a tested NSE-to-MQ5 strategy translation. This change does not install or run its scripts, import broker credentials, terminate terminals or alter the RemoteMT5GatewayAdapter/client contract.

Native real-tick certification still requires an available MT5 terminal, a compatible EA/strategy implementation, downloaded broker history, explicit tester configuration and inspection of the produced tester report/logs. Linux report/unit tests cannot certify that path.

## Local verification

Invoke through the terminal from the repository root, using the project's test interpreter:

```text
python -m pytest tests/unit/test_backtest_report.py tests/unit/test_backtest_report_integration.py tests/unit/test_factory_backtest_report_projection.py tests/unit/test_backtest_report_asset.py --override-ini addopts=''
node tests/js/test_backtest_report_ui.cjs
```

Test observations are explicitly synthetic calculation fixtures, never profitability evidence.
