# Command Center (bounded context)

Strategy fleet command view: overview KPI strip, risk-first fleet grid
(blocked/unknown/stale float up), inspector drawer (execution-safety
CAN-THIS-TRADE card + transient evaluation + debug intelligence + AI
attribution + evidence completeness + timeline), per-strategy decision
timeline, and the time machine (bounds + debounced lazy frame slider).

- Invariants honored in presentation: evaluation pipeline counts are
  transient telemetry and labeled as such (never mixed with lifecycle
  counts); "can_trade" only ever comes from the domain authority endpoint;
  frames are fetched lazily per settled slider instant.
- `{available:false, RESEARCH_ENGINE_UNAVAILABLE}` renders verbatim.
- Evidence: command_center_integration.py, command_center_routes.py,
  time_machine.py, spatial_layout.py; Web/command_center_*.js + cc_*.js.
