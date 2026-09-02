# MT5 History Data Contract

Authoritative mapping between REAL MetaTrader 5 responses and the internal
database / accounting / API / UI layers. Verified 2026-08-17 against a live
MetaQuotes-Demo terminal (account 10011755849, XAUUSD) — fixtures captured in
`tests/fixtures/mt5/*.json` reflect the EXACT shapes the installed
MetaTrader5 Python package returns.

## 1. Source of truth

| API | Returns | Fixture |
| :--- | :--- | :--- |
| `account_info()` | namedtuple (28 fields) | account_info.json |
| `terminal_info()` | namedtuple | terminal_info.json |
| `symbol_info("XAUUSD")` | namedtuple (spec block) | xauusd_symbol.json |
| `symbol_info_tick("XAUUSD")` | namedtuple (tick block) | xauusd_tick.json |
| `positions_get()` | tuple of namedtuples | positions.json |
| `orders_get()` | tuple of namedtuples | orders.json |
| `history_orders_get(from,to)` | tuple of TradeOrder | history_orders.json |
| `history_deals_get(from,to)` | tuple of TradeDeal | history_deals.json |
| `copy_rates_from_pos(...)` | numpy record array | xauusd_m1_rates.json |
| `order_calc_profit/margin(...)` | float (positional-only!) | order_calc.json |

All timestamps are UTC epoch seconds → `datetime.fromtimestamp(ts, tz=UTC)`.

## 2. account_info fields

| MT5 raw | Type | Example | Internal | Accounting use | NULL semantics |
| :--- | :--- | :--- | :--- | :--- | :--- |
| login | int | 10011755849 | AccountSnapshot.login | identity | — |
| balance | float | 37717.91 | .balance | live balance | unavailable→None |
| equity | float | 37717.91 | .equity | live equity / drawdown | unavailable→None |
| margin | float | 0.0 | .margin | margin level | 0 = no exposure |
| margin_free | float | 37717.91 | .margin_free | free margin | — |
| leverage | int | 100 | .leverage | risk plan | — |
| currency | str | USD | .currency | display | — |
| trade_mode | int | 0 (demo) | .trade_mode | mode honesty | 0=demo, 2=real |
| trade_allowed | bool | True | .trade_allowed | runtime mode | — |
| profit | float | 0.0 | .profit | floating net | — |
| margin_level | float | 0.0 | .margin_level | margin level % | 0 when no margin |

Derived: `floating_pnl = equity - balance` (MT5 definition).

## 3. history_orders fields (TradeOrder namedtuple, 24 fields)

| MT5 raw | Type | Internal (audit_broker_orders) | Notes |
| :--- | :--- | :--- | :--- |
| ticket | int | ticket (PK) | order identity |
| position_id | int | position_id | link to position lifecycle |
| type | int | type | ORDER_TYPE_* |
| magic | int | magic | bot magic 888101 |
| state | int | state | ORDER_STATE_* |
| volume_initial | float | volume_initial | — |
| volume_current | float | volume_current | — |
| price_open | float | price_open | — |
| price_current | float | price_current | — |
| **price_stoplimit** | float | price_stop_limit | REAL MT5 name (not `price_stop_limit`) |
| sl / tp | float | sl / tp | — |
| time_setup / time_done | int | time_setup / time_done | UTC epoch |
| reason | int | reason | ORDER_REASON_* |
| comment | str | comment | e.g. "NSE_PENDING" |
| external_id | str | external_id | — |

## 4. history_deals fields (TradeDeal namedtuple, 18 fields)

| MT5 raw | Type | Internal (audit_broker_deals) | Accounting use | NULL semantics |
| :--- | :--- | :--- | :--- | :--- |
| ticket | int | ticket (PK) | deal identity | — |
| order | int | order | link to order | — |
| position_id | int | position_id | LOGICAL TRADE KEY | — |
| entry | int | entry | 0=IN, 1=OUT | partial closes = many OUT |
| type | int | type | 0=BUY, 1=SELL (direction) | direction from OPEN deal |
| volume | float | volume | trade volume (OPEN leg) | — |
| price | float | price | entry/exit prices | — |
| profit | float | profit | gross PnL sum | — |
| commission | float | commission | cost, stored NEGATIVE | net = profit − \|comm\| − \|swap\| − \|fee\| |
| swap | float | swap | cost | — |
| fee | float | fee | cost | — |
| reason | int | reason | DEAL_REASON_* | exit reason |
| comment | str | comment | e.g. "NSE_CLOSE" | — |

**net_result (per deal) = profit − |commission| − |swap| − |fee|** — sign
convention verified on real terminal (all costs negative in MT5 records).

## 5. Logical trade reconstruction (audit_broker_trades)

Identity: `trade_id = position_id` (deterministic broker identity, no UUIDs).

One position lifecycle = open deal + N close/partial deals → ONE row:

| Field | Source |
| :--- | :--- |
| entry_time / entry_price | OPEN deal (entry=0) |
| exit_time / exit_price | last OUT deal (entry=1) |
| volume | sum of OPEN-leg deal volumes |
| gross_pnl | Σ deal profit |
| commission / swap / fee | Σ (abs of costs) |
| net_pnl | gross − commission − swap − fee |
| deal_ids / order_ids | all deal tickets / order tickets of the lifecycle |
| duration_sec | exit_time − entry_time |

Positions with NO OUT deal in the fetched window are still OPEN at the broker
→ never persisted as trades (no fabricated realized result).

## 6. Deduplication / idempotency

- `audit_broker_orders.ticket` UNIQUE → insert-or-ignore
- `audit_broker_deals.ticket` UNIQUE → insert-or-ignore
- `audit_broker_trades.trade_id` UNIQUE → insert-or-ignore
- Re-ingesting identical history 10× ⇒ 0 new rows, identical totals (tested)

## 7. Data validation

Deal/order tickets unique per fetch; position_id consistent across deals;
timestamps UTC-normalized; volumes/prices finite; profit/costs aggregated
with sign convention above. Malformed rows are diagnosed via
`[MT5_HISTORY] event=FETCH_RESULT` logs — never silently discarded.

## 8. Accounting source hierarchy

1. `audit_broker_trades` (authoritative reconstructed broker outcomes)
2. `audit_ledger` (engine's own execution autopsy — fallback, may carry
   zero-PnL rows when the deal path never reached it)

`AccountingCore.load_trades()` reads the ledger first (engine-lived trades
with full context) and falls back to the broker copy. When broker history is
present, every financial total (net PnL, win rate, best/worst, expectancy,
profit factor) is traceable to real MT5 deal rows.