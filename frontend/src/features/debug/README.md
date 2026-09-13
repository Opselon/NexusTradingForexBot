# debug — bounded context

The full debug hub (legacy tab-debug): canonical /api/debug/state viewer,
subsystem health, 70D feature-contract grid + anomaly filter, frozen-state
freshness diagnostic, MT5 IPC telemetry, snapshot ring (capture/detail),
two-snapshot compare (server diff + key-level added/changed/removed diff),
validated instant model-test (dimension/bounds from the live contract —
invalid vectors are blocked before the POST), execution-id forensic trace
timeline, and read-only research forensics panels. Polling is operator-
controlled per tab; every UNAVAILABLE section shows its backend reason +
correlation id. EDD: debug_research_routes.py:81-817,1379-1558,
debug_snapshot.py (store/diff), config/validation.ts (vector gate).
