# src/nexus_scalp/adapters/mt5/diagnostics.py

- **PURPOSE:** MT5 call instrumentation: `run_mt5_call` wrapper (records
  every IPC call's duration/retcode/error into `MT5CallDiagnostic`),
  `retcode_label` (human mapping of MT5 retcodes incl. the "0 is NOT a
  trade retcode" truth), `MT5ConnectionState` (REAL connection state
  machine with terminal/version/account context), `MT5OperationError`
  (typed exception carrying the diagnostic).
- **ARCHITECTURE LAYER:** Adapters (observability of the broker boundary).
- **RESPONSIBILITY:** Make every broker interaction measurable and
  attributable: operation name, start/end ns, retcode + label, error
  taxonomy, and a structured log line — feeding the IPC telemetry
  endpoint and reconnect diagnostics.
- **DEPENDENCIES:** stdlib time, logging.
- **CONNECTS TO:** DirectMT5Adapter (every call), `/api/debug/ipc-telemetry`,
  `_emit` log consumers, tests.
- **KEY CONCEPTS:**
  - `run_mt5_call(fn, operation, logger_name, ...)` — wraps a callable,
    measures duration, extracts retcode from return value when possible
    (MT5 returns tuples with retcode as last element), converts to a
    diagnostic + log line; optional error handling policy.
  - `retcode_label(code)` — the retcode semantics reference (DONE=10009,
    REJECTED=10004, ..., None/non-int → "UNKNOWN"); 0 is documented as
    NOT a trade-server retcode — a 0 return usually means the call itself
    returned nothing (the phantom-cancel forensics).
  - `MT5ConnectionState` — state machine: set_state/record_success/
    record_failure/mark_degraded/set_terminal/set_versions/set_account;
    `connected()` derives from real transitions, never config.
- **EDGE CASES & PITFALLS:** The wrapper must never swallow exceptions
  silently (re-raises + records); failure records must carry the detail
  (terminal not running vs timeout vs auth) so the doctor/UI can
  differentiate; MT5OperationError carries the full diagnostic for
  error-path audits.