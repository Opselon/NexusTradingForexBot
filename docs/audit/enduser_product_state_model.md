# PRODUCT STATE MODEL — ENDUSER-OPERABILITY-HARDENING (EU-09)

## Why one model

The product previously had three half-answers to "what is my program doing":

* `engine_running: bool` (`web/server.py`)
* `runtime_mode: str` + `data_source: str` (same payload)
* install-time readiness in `health.py`

Nothing stopped a consumer from reading `engine_running=true` as "ready to
trade", or `runtime_mode=LIVE` as "live trading is possible". The state
taxonomy for the wider system lives in `release/state_taxonomy.py`; this model
is the **end-user product** view: four orthogonal axes, one summary sentence,
one owner.

## Owner

`src/nexus_scalp/release/product_state.py`
→ `derive_product_state(status: dict | None, reachable: bool) -> ProductState`

It only *reads* observed state (never mutates anything, never network), so it
is safe to call from the CLI, the API, tests, and support tooling.

## The four axes (never conflated)

| Axis | Values | Meaning |
|---|---|---|
| `application` | `STOPPED`, `STARTING`, `READY`, `DEGRADED`, `UNKNOWN` | Is the program itself up? |
| `engine` | `STOPPED`, `RUNNING`, `UNKNOWN` | Is the trading loop running? |
| `execution_mode` | `PAPER`, `SHADOW`, `LIVE`, `REPLAY`, `UNKNOWN` | Which mode is configured? |
| `trading` | `NOT_ARMED`, `PAPER_ONLY`, `SHADOW_ONLY`, `ARMED_LIVE`, `BLOCKED`, `UNKNOWN` | Could an order be sent? |

### Invariants (asserted by tests)

1. `application == READY` **implies** `engine == RUNNING`.
2. `engine == RUNNING` **never implies** `trading == ARMED_LIVE`.
3. `execution_mode == LIVE` with a non-live `data_source` → `application == DEGRADED`
   (mode/data disagreement), `trading` stays non-arming.
4. Not reachable → `application == STOPPED`, `engine == STOPPED`, `trading == NOT_ARMED`.
5. Every axis carries a `reasons[...]` string in product language.

## Canonical legal values

`ProductState["axes"]` advertises the legal values per axis so machine readers
do not hard-code string literals.

## Surfaces

* `nexus status` (human) — panel with `Product state` / `Why` / `Trading safety` / `Dashboard`.
* `nexus status --json` — the four axes at payload **root** (flat contract)
  plus `runtime.product_state` (with `axes`).
* `nexus dashboard --json` — `product_state` for a single address probe.
* `/api/status` — the raw engine facts (`engine_running`, `runtime_mode`,
  `data_source`) remain the source; `product_state` is a pure derivation of them.

## Transitions (observed)

```
STOPPED  --HTTP answers, engine_running=false-->  STOPPED(app) + engine STOPPED
STOPPED  --HTTP answers, engine_running=true --->  READY
READY    --runtime_mode == DEGRADED ------------>  DEGRADED
READY    --mode != data_source ------------------>  DEGRADED (reason: disagreement)
any      --no HTTP answer ---------------------->  STOPPED (reason: no response)
```

## What this deliberately does NOT do

It does not replace `release/state_taxonomy.py` (system/operator states) nor
`release/state_truth.py` (RECONCILED / INDEPENDENT surfaces). It is the
end-user projection of the running product, and it must stay a pure function.
