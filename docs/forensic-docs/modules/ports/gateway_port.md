# src/nexus_scalp/ports/gateway_port.py

- **PURPOSE:** RPC contract for talking to a REMOTE MT5 gateway — the
  container/host-separated deployment where MT5 runs on a Windows box and the
  engine lives elsewhere (Docker).
- **ARCHITECTURE LAYER:** Ports.
- **RESPONSIBILITY:** Declare the minimal async surface a gateway client must
  expose: latency ping + authenticated command dispatch. Note how thin it is
  compared to `IMT5Port` — all broker semantics arrive via the generic
  `execute_remote_command(action, payload)` envelope, letting the gateway host
  own the domain logic while the client stays a dumb transport.
- **DEPENDENCIES:** stdlib only (`abc`, `typing`).
- **CONNECTS TO:** `RemoteMT5GatewayAdapter` (the adapter implements `IMT5Port`
  over this port via ZMQ/JSON), Docker deployments, `docker-compose.yml` topology.
- **KEY CONCEPTS:**
  - `ping() -> float` — RTT in ms; used for health checks and latency
    observability (the "50ms hot path" claim is PARTIALLY VERIFIED in skill §10 —
    remote-gateway round trips are a real latency contributor).
  - `execute_remote_command(action, payload)` — the payload is documented as
    HMAC-authenticated; the async signature means network I/O never blocks the
    tick loop.
- **EDGE CASES & PITFALLS:** The contract is intentionally minimal; anything
  beyond `action`/`payload` (e.g. typed bid/ask methods) would leak transport
  concerns. A remote gateway adds an extra failure domain (host down, ZMQ
  timeout) that the DirectMT5Adapter does not have — the adapter layer must
  translate those into the same UNAVAILABLE-provenance snapshots.