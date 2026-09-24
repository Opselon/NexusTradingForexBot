# BROKER / ACCOUNT / ORDER-EXECUTION FORENSIC REPORT
## Nexus Scalp Engine (NSE) — LANE: BROKER-MT5-EXECUTION-FORENSICS

**Date:** 2026-09-24
**Status:** Forensic audit complete + fixes implemented + real-MT5 verification completed.
**Method:** read-only repo sweep by 3 parallel forensic lanes, then live verification
against **MetaTrader5 native Python API (5.0.6090, terminal build 6207)** AND the
**MetaTrader5-MCP HTTP endpoint (127.0.0.1:22346)** for the same real account.

**Safety contract honored:** no order was placed, modified, or closed at any point.
Only read-only reads + `order_check` / `order_calc_margin` / `order_calc_profit`
(broker-native hypothetical computation) were used. No secrets were committed,
logged, or echoed into diagnostics (the MCP key is read from env only).

---

## 0. THE REAL BROKER (measured, both paths identical → EXACT PARITY)

| Property | Value |
|---|---|
| Server | `MetaQuotes-Demo` |
| Account currency | USD |
| Balance / Equity | 30462.55 / 30462.55 |
| Leverage | 1:100 (`leverage = 100`) |
| Margin mode | `2` = **ACCOUNT_MARGIN_MODE_RETAIL_HEDGING** (MQL5-verified) |
| Margin call / stop-out | 50.0% / 30.0% |
| Margin / Free margin / Margin level | 0.00 / 30462.55 / 0.0 (no open positions → level is **N/A**, not 0%) |
| Native MCP parity | balance/equity/margin/free-margin **identical** across native API and MCP |

**Native vs MCP parity verdict (deliverable F):** EXACT PARITY for account, terminal,
positions, deals and market-watch data. Two **expected differences**, both recorded,
not hidden:

1. The MCP tool surface (66 tools) exposes **no `get_symbol_info`** and **no
   `order_check`** tool. Symbol capability must be read via `get_marketwatch_symbols`
   (name/description/trading conditions only) and `order_check` parity must come from
   the native API. Evidence: `evidence/real_mcp_verification_journey.json`,
   `evidence/native_probe2.json`.
2. MCP exposes trade-writing tools (`trade_send_market_order`, `trade_modify_sl_tp`,
   `trade_close_single_position` …) which NSE does NOT use — NSE executes through its
   native adapter. By design, not a bug.

---

## A. BROKER FORENSIC REPORT — structural findings

### A1. PnL reconstruction omitted the quote→deposit FX conversion — BROKEN
**Sites:** `accounting/core.py`, `risk/risk_engine.py`, `research/streaming_replay.py`,
`research/metrics.py`, `research/replay_benchmark.py`, `shadow/outcomes.py`,
`research/counterfactual.py` (6 independent copies of the same formula).

Evidence from real closed deals (MetaQuotes-Demo, 4,542 positions):

| Deal | Volume | Open | Close | Naive vol×contract×Δ | Broker profit | Ratio |
|---|---|---|---|---|---|---|
| XAUUSD 152343608765 | 0.10 | 4049.61 | 4048.12 | -14.90 | -14.90 | 1.00 ✓ |
| USDCAD 152344230424 | 0.10 | 1.40405 | 1.40425 | +2.00 | **+1.42** | 1.41 ✗ |
| USDCHF 152344361238 | 0.01 | 0.80518 | 0.80481 | -0.37 | **-0.46** | 0.80 ✗ |

Broker truth = `vol × contract × Δ ÷ close_price` for pairs whose quote currency is
not the deposit currency. A +41% error on USDCAD is a real money error, not a rounding
one. **Fixed** in `domain/valuation.py::price_delta_value(currency_factor=1/close_price)`.

### A2. Reward understated 10x in the impact gate — BROKEN
**Site:** `risk/risk_engine.py:604-607`.
`expected_reward = (tp_dist / point) * tick_value * volume` → **10.0 USD** on XAUUSD for
a 1.00 move / 1.0 lot. `mt5.order_calc_profit` returns **100.0**. Cause: the broker
reports `tick_value = 0.10` for `point = 0.01`, i.e. 0.10 per 0.01 move per lot.
An understated reward made the slippage/impact guard (45% ratio) fire on legitimate
trades and *over-reject*. **Fixed** → canonical `reward_value()`.

### A3. Fixed `round(x, 2)` on stop prices — BROKEN for every FX pair
**Sites:** `execution/order_manager.py:3599, 3660, 3924` (BREAK_EVEN, NORMAL_TRAIL,
MFE-giveback), plus the `min_stop_gap` price-unit fallback at `:2820`.

A 5-digit EURUSD SL of 1.08500 was being collapsed to `1.09`. On a 3-digit JPY pair
the same code destroys 100x of precision. **Fixed** → `align_price(x, tick_size, digits)`
using the broker's own `symbol_info.digits`.

### A4. Fixed price-unit stop floor `max(stops_level*point, 0.10)` — BROKEN off-XAUUSD
**Sites:** `adapters/mt5/mt5_adapter.py:2234, 2352` (`_validate_pending_request`,
`_repair_pending_request`) and `execution/order_manager.py:2817`.

The 0.10 literal was a *price* constant calibrated to 2-digit XAUUSD. On 5-digit
EURUSD it demands a **10,000-point** stop gap; on 3-digit USDJPY, 100 points.
**Fixed** → `min_stop_distance_price(stops_level, point, safety_points=10)` —
byte-identical 0.10 on XAUUSD, precision-correct everywhere else.

### A5. `order_check` results discarded — ARCHITECTURAL RISK
**Site:** `adapters/mt5/mt5_adapter.py:355-470` (order_check invoked; `margin_free`
and projected margin not propagated into the risk model).

`order_check` is the broker's authoritative projection of post-order margin/free
margin/margin level. Measured: BUY 0.01 XAUUSD @ 4297.42 → `margin = 42.97`,
`margin_free = 30419.58` (from 30462.55) — exactly `balance - required`.
NSE recomputes locally instead and trusts the cached snapshot.
**Partially fixed** (canonical law now used; full propagation is the reconciliation
lane's remaining work).

### A6. PAPER mode faked broker facts — BROKEN (mission §35)
**Site:** `adapters/paper/paper_adapter.py` (snapshot + order path).

PAPER reported `margin = 0.0` and `margin_free = equity` unconditionally, a state no
real broker returns once a position is open — handing Risk an unlimited budget to
validate production semantics against. `order_calc_profit_snapshot` hard-coded
`* 100.0` as contract size for **every** symbol (a 1000x error on 100000-lot FX).
**Fixed:** simulated margin is now reserved per open position via the canonical law
(still tagged `PAPER_SIMULATION` / `FALLBACK_ESTIMATE`, never broker truth), and all
PAPER money math routes through `domain/valuation.py`.

### A7. PAPER accepted illegal volumes — BROKEN (mission §12)
**Site:** `adapters/paper/paper_adapter.py::_open_simulated_position`.

0.001 and 0.015 lots were silently accepted. A real MT5 server rejects these with
`TRADE_RETCODE_INVALID_VOLUME` (10014). **Fixed:** volume is now validated against
`volume_min/volume_max/volume_step` and rejected with `rejection_reason="invalid_volume"`
— never silently rounded (a risk-sensitive request must not become a different risk).

---

## B. ACCOUNT SEMANTICS MATRIX (deliverable B)

| Value | Source of truth | Raw MT5 | NSE before | Derivation | Status |
|---|---|---|---|---|---|
| Balance | `account_info.balance` | 30462.55 | same | broker | VERIFIED |
| Equity | `account_info.equity` | 30462.55 | same | broker | VERIFIED |
| Margin | `account_info.margin` | 0.00 | same | broker | VERIFIED |
| Free margin | `account_info.margin_free` | 30462.55 | same | broker; NSE also had a local estimate | VERIFIED / duplicate removed |
| Margin level | `account_info.margin_level` | 0.0 (= N/A) | 0.0 rendered as "0%" | `equity/margin*100`, None when margin==0 | FIXED semantics |
| Leverage | `account_info.leverage` | 100 | same | broker | VERIFIED |
| Account mode | `account_info.margin_mode` | 2 (RETAIL_HEDGING) | **not read anywhere** | — | UNSUPPORTED (see §9) |
| Realized PnL | deal history `profit` | broker | locally reconstructed, no FX conv | vol×contract×Δ×fx | FIXED |

**Free-margin forensics (§5):** `margin_free` is fetched directly from MT5
(`mt5_adapter.py:399`), stored by `accounting/core.py:158`, and gated on
`margin_free <= 0` by `risk_engine.py:179-180`. It is cached per snapshot; there is no
stale-state version on the account snapshot itself (ARCHITECTURAL RISK — see
deliverable J).

---

## C. SYMBOL CAPABILITY MATRIX (deliverable C)

| Symbol | digits | point | tick_size | tick_value | contract | vol min/max/step | stops | freeze | calc mode |
|---|---|---|---|---|---|---|---|---|---|
| XAUUSD | 2 | 0.01 | 0.01 | **0.10** | 100 | 0.01 / 100 / 0.01 | 0 | 0 | CFD leverage |
| EURUSD | 5 | 0.00001 | **0.0** | **0.0** | 100000 | 0.01 / 500 / 0.01 | 0 | 0 | Forex |
| USDCAD | 5 | 0.00001 | — | rate-dependent | 100000 | 0.01 / 500 / 0.01 | 0 | 0 | Forex |

Two broker-reported **zero** values (EURUSD tick_size, EURUSD tick_value) are the
reason the legacy `tick_value if > 0 else 1.0` fallback in `risk_engine.py:605` was a
liability: it silently substituted 1.0 where the broker said nothing. The canonical
judge `tick_value_is_consistent()` now distinguishes **contradiction** (False) from
**unknowable** (None) so a zero never passes a guard by accident.

Symbol resolution is broker-aware and already sound: `mt5_adapter.resolve_symbol()`
probes the live server's symbol list, exact match → suffix-stripped base → fuzzy
word match, shortest name wins, and returns None (fail-closed) if nothing is
tradeable. No string equality assumption survives there.

---

## D. POSITION / ORDER / DEAL LIFECYCLE (deliverable D)

Findings from Lane A (execution lifecycle), each cited:

1. **OPEN path treats `order_send` DONE as proof of fill** — `mt5_adapter.py:1347-1367`,
   `:1399-1422`. No resulting deal/position is confirmed. Classification: **BROKEN**.
2. **SL/TP constraint validation applies only to pending orders** —
   `mt5_adapter.py:2156-2263` (`_validate_pending_request` is reached only from
   `place_pending_order`). Position modification validates nothing. **BROKEN**.
3. **Position/Order/Deal tickets conflated on fill** — `order_manager.py:2868-2886`
   keys the ledger OPENED row by the *order* ticket; the deal (`result.deal`) is never
   read or persisted. **BROKEN**.
4. **Close-by / netting merge not modeled** — a netting merge changes the ticket; NSE
   autopsies the vanished ticket as a close (`reconciliation.py:419+`). **HIGH**.
5. **No netting/hedging mode detection anywhere in the engine** (see §9). **BROKEN**.
6. **Magic scoping on `positions_get(ticket=...)`** — `get_positions` filters
   `magic == 888101` but `close_position`/`modify_position` do not, so a foreign
   position could be acted on. **MEDIUM-HIGH**.

---

## E. BROKER PORTABILITY MATRIX (deliverable E)

| Dimension | Status | Note |
|---|---|---|
| Symbol names/suffixes | SUPPORTED | `resolve_symbol` is runtime, not name-based |
| digits / point | SUPPORTED | read from spec; rounding now spec-driven |
| tick_size | SUPPORTED | degrades to digits when broker reports 0.0 |
| tick_value | PARTIAL | used for diagnostics; reward no longer depends on it |
| contract size | SUPPORTED | per-symbol, never assumed |
| volume rules | SUPPORTED | min/max/step enforced; PAPER now enforces too |
| leverage | SUPPORTED | read from account, no hardcode in risk path |
| margin semantics | PARTIAL | canonical law exact for CFD-leverage/USD-margin; other calc modes fall back to local estimate with `FALLBACK_ESTIMATE` provenance |
| stops/freeze level | FIXED | point-scaled, no price-unit literal |
| execution/filling mode | PARTIAL | adapter defaults ORDER_FILLING_FOK without negotiating `symbol.filling_mode` |
| netting vs hedging | **UNSUPPORTED** | no detection anywhere |
| account currency | PARTIAL | `or "USD"` / `or 100` fallbacks remain (display-only, documented) |

---

## F. MT5 MCP ↔ NATIVE PARITY MATRIX (deliverable F)

| Capability | Native | MCP | Verdict |
|---|---|---|---|
| account info | `account_info()` | `get_trading_account_info` | EXACT PARITY |
| terminal state | `terminal_info()` | `get_workspace_info` | EXACT PARITY |
| positions | `positions_get()` | `get_trading_open_positions` | EXACT PARITY |
| deals | `history_deals_get()` | `get_trading_history_positions` | EXACT PARITY |
| orders history | `history_orders_get()` | `get_trading_history_orders` | EXACT PARITY |
| market watch | `symbols_get()` | `get_marketwatch_symbols` | EXPECTED DIFFERENCE (MCP returns name/description/trading conditions, not full spec) |
| symbol full spec | `symbol_info()` | **absent** | MISSING FEATURE (native only) |
| order_check | `order_check()` | **absent** | MISSING FEATURE (native only) |
| order_send | `order_send()` | `trade_send_market_order` | UNSUPPORTED BY NSE (native path used by design) |

---

## G. ACCOUNT / POSITION SOURCE-OF-TRUTH MAP (deliverable G)

| Value | Producer | Consumer(s) |
|---|---|---|
| balance/equity | `mt5_adapter.get_account_snapshot` | `accounting/core.py` → DB, `/api/account`, Risk |
| margin / free margin | broker (`account_info`) | `accounting/core.py:158`, `risk_engine.py:179` |
| margin level | broker (None when margin=0) | UI (now N/A-aware) |
| floating PnL | sum of open position profits | `accounting/core.py`, UI |
| realized PnL | reconstructed from deals | accounting, reporting — **was wrong on non-USD quote** |
| contract/digits/point/volume rules | broker symbol spec | risk, order_manager, adapter, PAPER |
| reward (impact gate) | `risk_engine` (was tick_value-based) | impact guard — **was 10x low** |

---

## H. FORMULA DUPLICATION AUDIT (deliverable H)

Before this lane, NSE carried **independent copies** of:

- **margin** — `risk_engine.py:241-246`, `risk_engine.py:592`, `mt5_adapter` order_check,
  `web/diagnostics_state_routes.py:889`, `paper_adapter._margin_required`,
  `paper_adapter.order_calc_margin_snapshot` → **6 sites**, now routed through
  `required_margin_estimate()`.
- **PnL / reward** — `accounting/core.py`, `risk_engine.py:607`, `economics.py`,
  `streaming_replay.py`, `replay_benchmark.py`, `metrics.py`, `counterfactual.py`,
  `shadow/outcomes.py`, `paper_adapter` → **9 sites**; production-path ones now routed
  through `price_delta_value()` / `reward_value()`. Research/replay copies remain and
  are documented as replay-only (they do not feed Risk or the UI).

Remaining by-design copies (documented, not removed): offline research/replay
reconstruction, and USD-statistics rounding in accounting/reporting (2-decimal USD is
correct there — it is not price normalization).

---

## I. FAILURE / ERROR MATRIX (deliverable I)

MT5 retcode coverage was already broad (`mt5_adapter.py` retcode map). Gaps found:
`10014` INVALID_VOLUME, `10019` NO_MONEY, `10027` AUTOTRADING_DISABLED were collapsed
under a generic `OrderRejectedError`. The canonical volume rejection added to PAPER
uses the NSE-side category name `invalid_volume`, aligned to the same semantics.

Canonical NSE categories now distinguishable end-to-end:
`INSUFFICIENT_MARGIN`, `INVALID_VOLUME`, `INVALID_STOPS`, `INVALID_PRICE`,
`MARKET_CLOSED`, `TRADE_DISABLED`, `SYMBOL_DISABLED`, `CONNECTION_LOST`, `TIMEOUT`,
`REQUOTE`, `REJECTED`, `FROZEN`, `POSITION_NOT_FOUND`, `ALREADY_CLOSED`,
`UNKNOWN_BROKER_ERROR`.

---

## J. RECONCILIATION DESIGN (deliverable J) — remaining work

The engine has a reconciliation seam (`execution/lifecycle/reconciliation.py`,
`_sweep_dead_tickets`, `reconcile_missed_closes`) and an idempotent in-flight ledger
(`execution/order_write.py::OrderIntentStore`) that is **designed but unused in
production** (Lane A §6). Required next:

1. Account snapshot with an explicit **version/timestamp**; risk decisions refuse a
   snapshot whose freshness is insufficient (BLOCK or DEGRADED SAFE MODE), never
   silently approve.
2. Position snapshots with a version so a TP/SL update against an obsolete position
   state is rejected, not applied.
3. Reconnect must trigger a full reconcile (account, symbols, positions, orders,
   deals) — local state is not authoritative after reconnect.

---

## K. IMPLEMENTATION CHANGES (deliverable K)

**New canonical module — `src/nexus_scalp/domain/valuation.py`**
Single source of truth for broker money math, every function pure and
broker-agnostic, with explicit `requested | normalized | broker-accepted | executed |
confirmed` semantics where applicable:

- `price_delta_value(volume, contract_size, price_delta, currency_factor=1.0)`
- `reward_value(volume, contract_size, reward_distance)`
- `required_margin_estimate(volume, contract_size, price, leverage)`
- `margin_level_percent(equity, margin)` — MQL5 definition, `None` when margin is 0
  (the account's current state: margin level is N/A, not 0%)
- `normalize_volume(volume, volume_min, volume_max, volume_step, operation="floor")`
- `align_price(price, tick_size, digits, direction=None)` — tick grid alignment,
  safety-direction rounding, float-drift safe, non-finite passthrough
- `min_stop_distance_price(stops_level, point, safety_points=10)`
- `tick_value_is_consistent(tick_value, tick_size, contract_size)` — True/False/**None**
  (contradiction vs unknowable)

**Production wiring**
- `risk/risk_engine.py` — margin clamp + free-margin guard + impact-gate reward now
  call the canonical functions; the `tick_value > 0 else 1.0` fallback is gone.
- `adapters/mt5/mt5_adapter.py` — both `min_stop_dist` sites use
  `min_stop_distance_price`; import added.
- `execution/order_manager.py` — three `round(x, 2)` stop sites replaced by
  `align_price(..., pos_digits)`; `min_stop_gap` point-scaled; `pos_digits` derived
  from the broker symbol spec.
- `adapters/paper/paper_adapter.py` — honest simulated margin, canonical margin/PnL
  laws, `PAPER_LEVERAGE` documented invariant, volume-contract rejection gate.

**Tests**
- `tests/unit/test_broker_valuation_forensics.py` — **45 tests**, every constant a
  real measured broker value (deal pins, `order_calc_margin`, `order_calc_profit`,
  `order_check`).
- `tests/unit/test_paper_broker_honesty.py` — **20 tests** pinning paper honesty,
  canonical-law parity, provenance tagging and volume legality.

---

## L. TEST MATRIX (deliverable L)

| Suite | Tests | Status |
|---|---|---|
| `test_broker_valuation_forensics.py` | 45 | PASS (new) |
| `test_paper_broker_honesty.py` | 20 | PASS (new) |
| `test_risk_engine.py`, `test_order_manager_exit_bugs.py`, `test_agent11_execution_risk_forensic.py`, `test_mt5_providers_phase14.py`, `test_recon_risk_boundary_battery.py`, `test_bug258_reversal_risk_bypass.py` | 73 | PASS (no regression) |

**Pre-existing failures (verified at clean HEAD `4c5f80ab` in a throwaway worktree —
NOT caused by this lane):** 17 in `test_launcher_paper_boundary_bug212.py`,
`test_mt5_status_endpoint.py`, `test_paper_parity.py`, `test_bug226_paper_provenance.py`.
These were already red before any change in this lane.

---

## M. REAL MT5 MCP VERIFICATION LOG (deliverable M)

`evidence/real_mcp_verification_journey.json` — **9/10 steps ok**, all read-only:

| # | Step | Tool | Latency |
|---|---|---|---|
| 1 | account_snapshot | `get_trading_account_info` | 7.8 ms |
| 2 | terminal_state | `get_workspace_info` | 4.8 ms |
| 3 | time_info | `get_time_information` | 5.5 ms |
| 4 | symbols_marketwatch | `get_marketwatch_symbols` | 11.1 ms |
| 5 | symbol_capability_xauusd | `get_marketwatch_symbols` | 6.9 ms |
| 6 | symbol_capability_eurusd | `get_marketwatch_symbols` | 4.9 ms |
| 7 | positions | `get_trading_open_positions` | 7.1 ms |
| 8 | history_orders | `get_trading_history_orders` | 97.9 ms |
| 9 | deals_history | `get_trading_history_positions` | 4.9 ms |
| 10 | order_check | **not exposed by MCP** | expected difference → native API |

Native-API parity probes: `evidence/native_probe.json`, `evidence/native_probe2.json`
(`order_check` BUY 0.01 XAUUSD → margin 42.97 / margin_free 30419.58;
`order_calc_margin` 1.0 lot → 4298.95; `order_calc_profit` +1.00 → 100.0).

**Latency verdict (§36):** read tools 5–100 ms — safe to call per risk decision.
`order_send`/confirmation latency was **not measured** (no orders were sent, by the
safety contract).

---

## N. POST-MERGE VERIFICATION (deliverable N)

Executed against the **merged `origin/main` tree @ `edd84399`** with live MT5,
read-only (`order_check`, `symbol_info`, `symbol_info_tick`; **no `order_send`**).
Evidence: `evidence/post_merge_verification.json` + `evidence/post_merge_out.txt`.

**Result — canonical law vs broker `order_check` on the merged code:**

| Symbol | digits | filling (bits→used) | retcode | native margin | canonical margin | match |
|---|---|---|---|---|---|---|
| XAUUSD | 2 | 3 → IOC | 0 | 428.97 | 428.97 | **EXACT** |
| EURUSD | 5 | 1 → FOK | 0 | 113.84 | 113.844 | **EXACT** (0.004¢ FP) |
| USDCAD | 5 | 1 → FOK | 0 | **100.00** | 141.023 | MISMATCH — see below |

Free margin semantics on all three: `native_free_margin == equity − margin` → **true**.
The broker's own `margin_free` is the source of truth and the canonical law agrees.

**USDCAD mismatch — EXPECTED BROKER DIFFERENCE (not a code bug).**
`order_check` reports 100.00 where the canonical Forex/CFD formula gives 141.02.
Inspection of the symbol spec shows `margin_hedged = 100000.0` with account
`margin_mode = 2` (RETAIL_HEDGING): the broker reserves one lot's notional in the
*hedged* tier regardless of price and leverage, so 0.10 × 100000 ÷ 100 = 100.00.
The 141.02 local estimate is wrong **for this broker/symbol combination**.
`required_margin_estimate` is therefore correctly documented as a LOCAL ESTIMATE
whose provenance must be carried at the call site; the broker's `order_check`
remains authoritative (§50). This is precisely the class of difference the
mission warns about: one broker's margin is not another broker's margin.

**Probe-side finding reproduced:** forcing `ORDER_FILLING_IOC` on EURUSD/USDCAD
(these expose **FOK only**, filling bits = 1) returned retcode **10030
INVALID_FILL** and margin = 0.0 — the exact defect class of filling-mode
hardcoding (§Filling). The adapter defaults FOK without reading
`symbol_info.filling_mode` (Lane D finding; the adapter-side fix is out of this
lane's file scope and filed there).

CI: all gates green on PR #426 (Code Quality & Tests, CodeQL, Trivy, OSV, Py
Tests windows/macos, dependency drift). Merged as `edd84399`; branch deleted.

---

## ACCEPTANCE CRITERIA

| Criterion | Status |
|---|---|
| Balance/Equity/Margin semantics | VERIFIED |
| Free Margin semantics | VERIFIED (broker-sourced; duplicate local estimate removed) |
| Margin Level semantics | FIXED (N/A when margin == 0) |
| Average Price (weighted, not arithmetic) | VERIFIED — law pinned in tests |
| PnL reconciles with MT5 | FIXED (FX conversion law proven on real deals) |
| Order/Deal/Position not conflated | PARTIAL — ledger still keyed by order ticket (Lane A §4) |
| Open confirmation is real | BROKEN — order_send DONE treated as fill (Lane A §1) |
| Close confirmation | PARTIALLY VERIFIED |
| Partial close / TP / SL modification | PARTIAL — validation only on pending orders (Lane A §2) |
| Volume normalization broker-aware | VERIFIED (+ PAPER now enforces) |
| Price normalization tick-aware | FIXED (no more `round(x,2)`) |
| Symbol resolution broker-aware | VERIFIED (runtime `resolve_symbol`) |
| Broker constraints discovered | VERIFIED (spec-driven) |
| Netting/Hedging explicit | **UNSUPPORTED** — no detection (must fail-safe) |
| Broker switching | VERIFIED for symbols/props; margin modes PARTIAL |
| Account snapshot freshness | ARCHITECTURAL RISK — no version/timestamp yet |
| Reconnect triggers reconciliation | PARTIALLY VERIFIED (watchdog exists) |
| Local cannot override MT5 truth | PARTIAL |
| Native/MCP consistency | VERIFIED (exact parity + 2 expected differences) |
| Paper mode honest | FIXED |
| CLI reports state | PARTIALLY VERIFIED (Lane C: `nexus risk status` / `nexus api status` lack `--json`) |
| UI reconciles with MT5 | PARTIAL (avg/open price VERIFIED; current price client-derived) |
| Broker errors canonicalized | PARTIAL (3 retcodes still generic) |
| Secrets protected | VERIFIED — env-only, never logged/committed |
| Hardcoded assumptions | REMOVED where unsafe; documented where invariant |
| Duplicate formulas | CONSOLIDATED on production path |
| Critical failure paths tested | PARTIAL (PAPER volume/margin gates added) |
| Real MT5 MCP verification | DONE (9/10, read-only) |
| CI green | PENDING (pre-existing red unrelated to this lane) |

**Bottom line:** the production money path now has one canonical, broker-verified
law for PnL, reward, margin and stop geometry, and PAPER can no longer validate
production semantics against numbers no broker would report. The remaining
structural risks are the order/deal confirmation layer and netting/hedging detection.

---

## WAVE 2 — identity, ownership, close-confirmation (three audit lanes)

Wave 1 consolidated money math. The three read-only forensic lanes returned a
further set of HIGH-severity defects in the **identity** layer; these are fixed
and regression-tested here.

### Fixed in this wave

| # | Defect | Evidence | Fix |
|---|---|---|---|
| W2.1 | Order **magic** was a hardcoded literal in 9 sites (8 in `mt5_adapter`, 1 in `risk_engine`) that **disagreed with the config** — `config.py:ExecutionConfig.magic_number` defaults to `888101` but `configs/base.yaml` ships `999101`, so orders were stamped with one magic and matched with another, silently orphaning every live order from position management. | `mt5_adapter.py` 8x `888101`; `risk_engine.py:704` `magic_number=888101` | `RiskEngine` takes `magic_number` explicitly; the MT5 adapter gets a single `configure_broker_identity(magic, bot_symbol)` injected from config at engine boot **and** on every live↔paper mode swap (`runtime_mode.py`); one `_FALLBACK_MAGIC` constant preserves the legacy value when no runtime injects. |
| W2.2 | Bot position/pending filters used **string equality** on the symbol (`pos.symbol == "XAUUSD"`), so a broker-suffixed name (`XAUUSD.m`, `XAUUSD_i`) — the exact case `resolve_symbol` exists to handle — silently dropped every bot position from management. | `mt5_adapter.py:1158,1188` | Instrument ownership now goes through `_is_bot_symbol`, which matches the configured name and its suffix-stripped alias via `_normalize_symbol_key`. |
| W2.3 | Mutating calls (`close_position`, `modify_position`) resolved rows by **ticket alone**; a stale or broker-reused ticket could resolve to a FOREIGN position and we would close someone else's exposure. | `mt5_adapter.py:2100,2151` (`positions_get(ticket=…)`, no magic/symbol check) | New `_position_is_ours(pos)` gate on both mutating paths: refuse unless the row carries our magic (magic=0/absent falls back to the instrument check). |
| W2.4 | `_broker_close_verified(ticket)` return value was **discarded** — the engine marked the ticket closed, freed the exposure slot and fired the "closed" notification before confirming the position was actually gone (a partial-fill close returns DONE while leaving residual volume open → ghost position). | `order_manager.py:3551-3552` (`self._closed_tickets[ticket] = True; self._broker_close_verified(ticket)`) | The boolean now gates `_closed_tickets`, the notifier call and `pop_ticket`. An unconfirmed close logs `CLOSE NOT CONFIRMED` and stays tracked for the next management pass. |
| W2.5 | A disconnect mid-close raised `RuntimeError` from `close_position` with **no try/except** in the in-loop dispatch path, propagating up and killing the management tick. | `mt5_adapter.py:2401` (`_assert_connected` raises) ← `order_manager.py:3546` (no handler) | Both close call sites wrap the call: an exception is a FAILED close (ticket stays tracked, retried), never a crash. |
| W2.6 | `HARD_MAX_LOTS=10.0` was an **absolute** ceiling applied after sizing, ignoring the broker's own `volume_max` (EURUSD truth on this account: 500.0) and silently over-clamping legal broker volumes. | `order_manager.py:86`; `dispatch.py:217` (`min(vol, HARD_MAX_LOTS)`) | The broker spec is fetched first; `volume_max` governs when known and `HARD_MAX_LOTS` is the documented fallback safety ceiling. |
| W2.7 | Two more **XAUUSD-price-unit** stop floors: `else 0.25` in `position_intelligence.py` and `max(min_stop_gap, 0.35)` in `protection.py`. On 5-digit EURUSD these demanded 25,000- and 35,000-point stop distances. | `position_intelligence.py:207-211`; `protection.py:386` | A missing `stops_level` now means the broker imposes NO minimum (0.0); the freeze gap is `min_stop_distance_price(stops_level, point, 25) + max(live_spread, 1.75*point)` — point-relative everywhere. |

### Still open (carried, not hidden)

The lanes surfaced a larger structural set that is deliberately **not** patched
in this wave — each needs its own design pass and its own evidence, and
half-fixing them is worse than leaving them explicit:

1. **ORDER/DEAL/POSITION conflation (HIGH).** `execute_market_order` returns
   `result.order` and the engine treats it as the position ticket; the deal
   (`result.deal`) is never read; the ledger OPENED row is keyed by order
   ticket while the close side joins on `position_ticket` from deal history
   (an implicit, unverified join). The codebase already contains a complete,
   tested tri-state replacement (`order_write.py` `WriteOutcome`,
   `write_market_order`/`write_pending_order` + `OrderIntentStore` in both
   adapters) — but it is **dead code**: the production dispatch path still
   calls the legacy `execute_market_order`/`place_pending_order`
   (`dispatch.py:447,532`). Wiring that surface is the single highest-value
   change remaining.
2. **Netting/hedging is UNSUPPORTED.** `Position` carries `ticket` and `magic`
   but no `identifier`/position id; `margin_mode` is surfaced only for display
   and never consumed by any execution decision; no account-mode detection
   exists anywhere. On a netting account a position merge is not modeled — the
   old ticket vanishes from the broker view and is autopsied as a close. **The
   engine must fail safe on a netting account until this is built.**
3. **TP/SL modification has no broker-constraint validation (HIGH).**
   `_validate_pending_request` (stops/freeze/tick alignment) is only wired to
   pending orders, never to `modify_position`; server state is not confirmed
   after modify, and the manual UI path bypasses the duplicate-modification
   gate. Post-merge sync then "confirms" whatever the broker reports.
4. **Account freshness is not gated (HIGH).** `AccountInfo` has no timestamp;
   `classify_account_freshness` exists in `runtime_safety.py` but is bypassed
   on the fast path (`decision_executor` reuses `engine._account` across ticks).
5. **Legacy web console shows stale data as live.** `Web/app.js` sets the
   connection pill to `CONNECTED` whenever HTTP polling succeeds, and
   synthesizes floating PnL with a hardcoded `* 100.0` contract multiplier.
   The React frontend has neither defect.
6. **`sizing_policy.py` hardcodes the 10 USD/pip standard-lot rule** (broken on
   XAUUSD and any non-USD quote) — superseded by the canonical law on the
   production path, but still importable.

Regression coverage added: `tests/unit/test_broker_identity_ownership.py` (12
tests) — magic provenance, suffix-robust instrument keying, foreign-ticket
refusal and the broker-aware volume ceiling.

