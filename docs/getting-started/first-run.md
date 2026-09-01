---
title: First-Run Safety
description: The Demo → Shadow → Live progression, account-identity fail-safe, and the safety invariants that bound the engine.
lang: en
---

# First-Run Safety (Demo → Shadow → Live)

This engine executes on a real broker. The repository's own first-run guidance
is a strict ladder — do not skip steps.

| Step | Action | Why |
| :--- | :--- | :--- |
| 1️⃣ | Log into a **DEMO account** in MT5 (`File → Open an Account → Practice/Demo`), confirm the account number/label before touching the bot | LIVE and demo accounts can look identical in MT5's login window |
| 2️⃣ | MT5: `Tools → Options → Expert Advisors → tick "Allow Algo Trading"` | Without this, orders are rejected |
| 3️⃣ | Copy `configs/live.yaml` → `configs/demo.yaml`; set demo credentials; confirm `risk.max_concurrent_positions: 1`, `risk.risk_per_trade_pct` (e.g. 0.5), `risk.max_account_drawdown_pct` | Never touch a live config while real money can be reached |
| 4️⃣ | Run **SHADOW** first (`nexus start --mode shadow`) for days; review the dashboard + Telegram reports | Proves model, signals and gates before any execution |
| 5️⃣ | Run the test suite weekly (`pytest tests/unit tests/integration`) | Catches regressions before they reach a live account |
| 6️⃣ | Only then consider **LIVE** with a small balance you can afford to lose, `risk_per_trade_pct: 0.25` or lower | Leveraged XAUUSD scalping carries extreme risk |

## Account-identity fail-safe

The live adapter refuses to connect to a terminal logged into a different
account than configured (BUG-142): the account-identity check is enforced at
connect time, not by convention.

## Safety invariants (engine-enforced)

- `nexus start` **never** defaults to LIVE — PAPER is the default; LIVE requires
  an explicit interactive confirmation after printing the full risk panel.
- Shadow runs carry `simulated=True` and hold **zero order authority**: no order
  can be placed, modified or closed.
- Research/strategy/news workers never hold order authority or a risk handle.
- Risk engine (`RiskEngine`) is authoritative for sizing boundaries: margin
  clamps (≤ 20% free margin), tier caps, single-position exposure cap,
  `HARD_MAX_LOTS = 10.0`, `MAX_TOTAL_EXPOSURE = 1`.
- Circuit breaker → SAFE_MODE after repeated rejections; order-churn throttle;
  kill switch via dashboard/CLI.
- Pre-flight doctor gates every launch: missing MT5 terminal, invalid config or
  unsupported platform ⇒ the engine refuses to start.
- Model promotion is strictly operator-gated; candidates never promote
  themselves; shadow never mutates execution.

## Also read

- [Project status](../project/status.md) — what is certified vs experimental
- [Runtime invariants](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/runtime_invariants.md) (INV-001…)
- [Troubleshooting](../guides/troubleshooting.md)
