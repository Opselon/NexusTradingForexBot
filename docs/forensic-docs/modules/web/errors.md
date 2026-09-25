# src/nexus_scalp/web/errors.py

- **PURPOSE:** The centralized HTTP error helpers — the ONLY sanctioned
  way routes report failures. Prevents the `except Exception as e:
  return {"error": str(e)}` anti-pattern (CodeQL py/stack-trace-exposure;
  leaked paths/SQL/exception classes) and guarantees every response
  carries the X-Request-ID correlation envelope.
- **ARCHITECTURE LAYER:** Web (error middleware/helpers).
- **RESPONSIBILITY:** (a) safe error envelope construction (sanitized
  message, correlation id, machine-readable code); (b) middleware wiring
  so unhandled 500s become the safe envelope without touching route
  bodies; (c) SSE/WS sanitized error paths (never stream str(e)).
- **DEPENDENCIES:** starlette request/response types, logging.
- **CONNECTS TO:** every route in server.py, Web/api_client.js (reads the
  correlation header for log alignment), tests (test_web_security).
- **KEY CONCEPTS:** The envelope is stable and minimal: {error: {code,
  message (sanitized), request_id}} — the UI displays the code + safe
  message; the FULL trace goes to server logs only.
- **EDGE CASES & PITFALLS:** Error paths MUST NOT themselves raise (an
  error handler that crashes takes down the response path); secrets must
  never pass through message templates (redaction at the source).