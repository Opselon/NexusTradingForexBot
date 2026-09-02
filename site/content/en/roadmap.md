---
title: Roadmap
description: NOW / NEXT / LATER / LONG TERM — with objectives, dependencies and completion gates.
lang: en
---

Wording discipline: items are **Planned**, **Under Evaluation**,
**Research Direction** or **Completed** — never "coming soon", never
guaranteed.

## NOW (active)

| Stream | Item | Status |
| :--- | :--- | :--- |
| VALIDATION | Rebuild 70D candidate evidence (corrected confidence semantics, CHG-0042) | Under Evaluation |
| RESEARCH | Counterfactual evidence deepening (CHG-0041) | In progress |
| ARCHITECTURE | Large-file decomposition with golden tests (CHG-0032-A1) | In progress |
| RUNTIME | Record-builder contract hardening (BUG-185) | Completed |

## NEXT

| Stream | Item | Dependencies |
| :--- | :--- | :--- |
| ML | 70D promotion decision — operator-gated, or honest retirement | validated candidate + 14-gate verify |
| VALIDATION | Counterfactual-driven policy review | counterfactual outputs |
| DOCS | 100% translation coverage of core pages | translation workflow |
| OBSERVABILITY | OBS-001..016 gap burn-down | audit evidence |

## LATER (research directions)

Temporal liquidity promotion candidate (`scalp_v4_temporal_candidate`) · MSLIE
integration into policy · regime-conditional model selection · multi-asset
expansion (currently XAUUSD-tuned).

## LONG TERM (direction, not commitment)

Broker abstraction beyond MT5 (`IMT5Port` seam) · optional PostgreSQL profile ·
selective open-core extraction.

Full roadmap with completion criteria:
[roadmap.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/project/roadmap.md).
New items enter through the taskboard with an owner and a completion gate.
