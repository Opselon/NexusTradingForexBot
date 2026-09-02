# POST-70D MONITORING ACTIVATION — performance measurement (TASK-12 §36)

> AGENT-12, 2026-08-19. Read-only measurement on the live-safe environment.

## Health scan cost (full 34-check matrix, 5 runs)

| Metric | Value |
| :--- | :--- |
| p50 | 2.48 s |
| p95 | 2.70 s |
| max | 9.69 s (DB contention outlier) |
| avg | 3.85 s |
| checks | 34 |

## Slowest checks

| Check | ms | Cause |
| :--- | :--- | :--- |
| CHECK-MIG-01 | 1486 | migration state: 3 x _integrity_for (each = PRAGMA integrity_check) |
| CHECK-INT-01 | 531 | integrity_check over 3 domains |
| CHECK-TRC-01 | 207 | worker-state table scans |
| CHECK-UI-02 | 58 | web bundle file reads |
| CHECK-API-01 | 53 | server source scan |

## Runtime impact assessment (§36)

- The health scan runs ONLY via CLI (`nexus forensic`), API
  (`/api/forensics/health`, `/api/forensics/deploy-gate`) or the periodic
  report worker — NEVER on the tick hot path (INV-001 intact; checks are
  not imported by live_engine).
- Deploy gate (pre-push) cost ≈ 2.5 s once per push — acceptable.
- Periodic report interval (default 6h) amortizes the cost to ~0.4 ms/h.
- No prediction/execution latency impact: checks never touch the model
  inference path or order manager.

## Conclusion

Monitoring overhead is bounded and off-hot-path. p50 2.5 s is dominated by
honest integrity verification (PRAGMA integrity_check on 3 WAL databases,
~50 MB total) — the cost of truth, not of a defect.