# Control Center (bounded context)

Operator console over the READ-ONLY /api/operator/* evidence surface
(CHG-0043): overview (runtime truth + release identity + backend warnings),
decision observatory with per-decision inspector (payload + correlated
orders + disclosed correlation method), terminal funnel, NO_TRADE forensics,
orders/latency, calibration monitor.

- Truth rule honoured in every cell: a value the backend did not supply
  renders NOT RECORDED — never 0, never invented; unparseable payloads are
  kept and flagged, inspect disabled.
- Kill-switch style actions (engine stop/start, mode switch incl. typed LIVE
  guard) hit the canonical engine routes, are confirm-guarded, and re-read
  the authoritative snapshot — no local state faking.
- Evidence: operator_routes.py, calibration_monitor.py, Web/control_center.js.
