---
title: MT5 MCP Bridge & Decision Trace
description: The read-only MCP transport, causal trace emission with secret redaction, the replay identity gate and the bounded-memory fixes that landed on main after v9.0.17.
lang: en
---

# MT5 MCP Bridge & Decision Trace

This page documents the work merged in
[#676](https://github.com/Opselon/NexusTradingForexBot/pull/676) — after the
`v9.0.17` release, so **unreleased** until the next tag. Everything below is
enforcement in code with a regression test, not a roadmap item.

> [!IMPORTANT]
> The MCP transport is **read-only**. It cannot place, modify or close an
> order, and it is **off by default**. Enabling it does not change how the
> engine talks to MetaTrader 5 — only code that explicitly constructs
> `MCPMT5Adapter` uses it.

## Why a second transport exists

Before this wave the repository had **no MCP client at all**: a raw grep for
`22346 | MCPToolbox | mcp_client` across `src/`, `scripts/`, `tests/` and
`configs/` returned zero hits, while a 66-tool terminal bridge was running on
the same box. The engine's read surface was therefore unreachable from anything
but the native Win32 IPC adapter or the ZMQ gateway.

`adapters/mt5/mcp_adapter.py` closes that gap by implementing the **read**
surface of `IMT5Port` over the local MCP endpoint (HTTP JSON-RPC, protocol
`2025-06-18`), returning the *same* typed snapshots the native adapter
produces — `build_account_snapshot` / `build_symbol_snapshot` are reused
directly, so downstream consumers cannot tell which transport answered.

Tools consumed (verified live, all read-only):

```text
get_trading_account_info      get_marketwatch_symbols
get_chart_history             get_chart_ticks_history
get_trading_open_positions    get_trading_history_orders
get_trading_history_positions get_time_information
get_workspace_info            get_terminal_journal
```

## Read-only by construction

| Guard | Behaviour |
| :--- | :--- |
| `FORBIDDEN_TOOLS` frozenset | Blocks the whole `trade_*` surface at the **transport boundary** — a future caller cannot reach a mutating tool even by accident |
| Port write methods | `send_order`, `execute_market_order`, `place_pending_order`, `modify_position`, `close_position`, `modify_order`, `cancel_pending_order` raise `NotImplementedError` **without issuing any MCP call** |
| `connect()` | Raises on transport or auth failure — never returns `False` silently |
| Read failures | Surface as `available=False` + an `error_state` snapshot; values are never fabricated |
| History windows | Range-only: `datetime_from` / `datetime_to` are required, a future `datetime_to` is rejected by the bridge, so count-based port methods derive the range from the terminal clock and record the clamp in the snapshot `note` |
| Empty window | `ok:true` + `history:[]` is a legitimate empty result, logged at debug — not an error |

Credentials follow the same contract as the remote gateway: `NSE_MT5_MCP_URL`
and `NSE_MT5_MCP_API_KEY` from the environment (or the repo-root `.env`), sent
only as `X-API-Key` / `Authorization`. The key is never logged, never
stringified into an exception and never returned from a method.

## Enabling it

```yaml
# configs/base.yaml
mt5:
  mcp:
    enabled: false        # default: byte-identical behaviour for everyone else
    url: ""               # empty -> NSE_MT5_MCP_URL from env / .env
    read_only: true       # documents the intent; enforcement lives in the adapter
    timeout_s: 60.0
```

```bash
export NSE_MT5_MCP_URL="http://127.0.0.1:22346/mcp"
export NSE_MT5_MCP_API_KEY="…"   # environment only — never in a config file
```

## Causal trace emission

`observability/trace_mt5_emit.py` turns a **real** broker result object into the
frozen v2 causal field dict. It is duck-typed on purpose (no `isinstance` on
adapter classes, no imports of them), so it stays importable from any context:

- direct MT5 `order_send` / `order_check` → `retcode`, `order`, `deal`, `price`, `volume`, `comment`
- remote-gateway RPC response → `status`, `message`, `ticket`, `order_id`, `deal_id`
- paper adapter → `success` + the internal simulated ticket (`gateway="paper"`, `simulated=True`)

`trace_contract.sanitize_detail` redacts secrets at the boundary before an event
is stored, so an API key or an account credential cannot reach the ring, a log
line or an SSE frame.

### Trace routes

The observer is a **passive in-memory store**; the routes only read it. No
handler touches the trading engine, and an exception in a handler can never
reach the engine — every route is guarded and returns an honest error payload.

| Endpoint | Purpose |
| :--- | :--- |
| `GET /api/trace/observer` | Observer status: session, schema version, ring occupancy |
| `POST /api/trace/observer/start` · `/stop` | Start / stop collection |
| `GET /api/trace/topology` | Node + edge view of the decision path |
| `GET /api/trace/latency` | Per-stage latency samples |
| `GET /api/trace/integrity` | Chain/sequence integrity report |
| `GET /api/trace/decisions` | Decision events |
| `GET /api/trace/events` | Raw events (bounded batch) |
| `GET /api/trace/bundle/{key}` | Evidence bundle for one correlation key |
| `GET /api/trace/why/{event_id}` | The WHY chain for a single event |
| `GET /api/trace/stream` | SSE stream: `hello`, `batch`, `heartbeat`, `error` |

The stream resumes explicitly: the client sends `?last_seq=` on reconnect. If
the ring has already evicted that point the batch carries `gap: true`, and the
UI is required to render **TRACE GAP** instead of implying continuity.

## Replay identity gate

`POST /api/replay/session` requires a `dataset_fingerprint` (8–64 chars). The
records loader is the authority on dataset identity: if the requested id and
fingerprint do not describe the dataset it loaded, it raises
`DatasetIdentityError` and the route answers **422** with
`rejection: "dataset_identity"`.

That is a deliberate, fail-closed rejection — not a server fault. It is the
difference between "this run is not comparable" and a silent comparison across
two different datasets. Every other failure returns the generic error envelope
with no `str(e)` on the wire (the text can name an internal artifact path); the
full detail goes to the server log only.

## Honest `/system` diagnostics (F-22)

An adapter that cannot report diagnostics used to degrade to `mt5: null` —
indistinguishable from "no adapter attached", which the live-state route then
rendered as `available: true` with an empty diagnostics dict. A false green.

```json
{ "mt5": { "available": false, "reason": "DIAGNOSTICS_UNAVAILABLE",
           "detail": "adapter.diagnostics_summary() raised RuntimeError" } }
{ "mt5": { "available": false, "reason": "NO_ADAPTER_ATTACHED" } }
```

## Bounded runtime memory (H-04 / H-05)

Two structures used to grow with uptime; both are now capped, so a long
SHADOW/PAPER run cannot exhaust the process:

- **H-04** — the paper ledger is bounded (no unbounded fill history).
- **H-05** — the completed-bar ring is bounded (no unbounded bar retention).

## Verification

| Suite | What it pins |
| :--- | :--- |
| `tests/unit/test_mcp_adapter_contract.py` | Read-only invariants, forbidden-tool refusal, envelope mapping, redaction |
| `tests/unit/test_trace_mt5_emit.py` | Frozen v2 causal fields per result shape, secret redaction |
| `tests/unit/test_replay_routes_contract.py` | Identity gate (422), error envelopes, no internals on the wire |
| `tests/unit/test_mt5_memory_bounds.py` | H-04 / H-05 caps under sustained load |
| `tests/integration/test_mcp_adapter_live_readonly.py` | Live bridge, skipped unless the MCP env contract is present |

Merge gate for #676: `ruff` + `format` clean, `mypy src` with a byte-identical
error set versus the pre-merge baseline, dependency drift and migration safety
OK, and **168 passed / 31 skipped / 0 failed** across the recovered and
regression suites (every skip named an environment or data guard).

## Status

| Capability | Grade | Note |
| :--- | :--- | :--- |
| MCP read adapter | **IMPLEMENTED** | Off by default; read-only enforced at the transport boundary |
| MCP trade/write path | **NOT BUILT — by design** | `trade_*` tools are blocked, not proxied |
| Trace emission + observer | **IMPLEMENTED** | Bounded ring, redaction, SSE resume with explicit gaps |
| Replay identity gate | **IMPLEMENTED** | Fails closed on a mismatched dataset |
| Bounded memory (H-04/H-05) | **IMPLEMENTED** | Caps covered by regression tests |

## See also

- [Observability](observability.md) — the log/incident contracts this trace layer plugs into
- [Replay](../research/replay.md) — replay engines and the anti-leakage guarantee
- [Runtime](runtime.md) — where the adapters sit in the execution path
