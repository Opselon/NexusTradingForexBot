# src/nexus_scalp/adapters/mt5/remote_gateway.py

- **PURPOSE:** The REMOTE MT5 gateway adapter — `RemoteMT5GatewayAdapter`
  implements BOTH `IMT5Port` (broker surface) and `IGatewayPort` (RPC
  surface) to drive an MT5 terminal on a remote Windows host over
  ZMQ/JSON with HMAC-authenticated requests. The Docker/container
  deployment path.
- **ARCHITECTURE LAYER:** Adapters (remote infrastructure boundary —
  adds a network failure domain the Direct adapter lacks).
- **RESPONSIBILITY:** (a) transport: connect (ZMQ), ping latency,
  `_send_request(action, payload)` (async-capable signature, HMAC
  signature verification `verify_request_signature`); (b) broker surface:
  every IMT5Port method translated to a gateway command (account/symbol/
  tick/positions/orders/history/orders); (c) honest failure: network/timeout
  → False/empty/UNAVAILABLE-provenance snapshots.
- **DEPENDENCIES:** zmq, `ports.mt5_port` + `ports.gateway_port`, HMAC
  helpers, domain models, logging.
- **CONNECTS TO:** LiveEngine (when configured for remote), docker-compose
  topology, remote gateway host, tests.
- **KEY CONCEPTS:** The adapter is a THIN CLIENT — the gateway host owns
  broker logic; the client maps domain calls to commands and back.
  `_sync_ping` provides the RTT baseline for health/latency monitoring
  (the "50ms hot path" is unachievable over remote links — latency is
  surfaced truthfully, never claimed).
- **EDGE CASES & PITFALLS:** ZMQ timeouts must map to UNAVAILABLE (not
  exceptions into the engine); request signatures must be verified on BOTH
  ends; the remote gateway is a single point of failure — reconnect logic
  + health state must recover without user intervention.