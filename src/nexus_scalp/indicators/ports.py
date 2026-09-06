"""Indicators domain — SOLID building blocks.

Design
------
* **S**ingle responsibility per class (RSI vs MACD vs MA vs pivot vs gauge).
* **O**pen/closed: new indicators register via the Indicator protocol, no
  modification of the aggregator.
* **L**iskov: every indicator satisfies `IndicatorCalculator`.
* **I**nterface segregation: calculators, pivot calculators and gauge
  aggregator are distinct ports.
* **D**ependency inversion: the service depends on `BarSource` / `PriceSeries`
  abstractions, the API depends on `IndicatorService` — no direct DB/engine
  coupling outside the adapters.

Pure math
---------
All indicator formulas are deterministic pure functions over a closed price
series (`prices: list[float]`, oldest→newest, `close` of completed bars).
No hidden state; every method is a single-responsibility computation.  The
service layer (#3) is the only place that touches I/O (bars → prices).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


class IndicatorCalculator(Protocol):
    """Port: computes one indicator value from a price series."""

    def calculate(self, prices: Sequence[float]) -> float | None: ...


@dataclass(frozen=True)
class IndicatorResult:
    name: str
    value: float | None
    action: str  # Buy | Sell | Neutral | Strong buy | Strong sell

    def as_api_dict(self) -> dict[str, object]:
        return {"name": self.name, "value": self.value, "action": self.action}
