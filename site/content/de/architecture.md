---
title: Architektur
description: Hexagonale, ereignisgesteuerte Architektur — Schichten, Grenzen und der Weg vom Tick zur Entscheidung.
lang: de
translation-status: complete
source-revision: en:architecture@2026-09-02
---

NSE ist **hexagonal (Ports-and-Adapters) und ereignisgesteuert**: Broker-Plattformen, Modelle und Netzwerkadapter liegen hinter Port-Verträgen (`IMT5Port`, `IGatewayPort`); die Domain weiß nie, welcher Broker angeschlossen ist.

```text
Marktdaten (MT5 / ZMQ / paper)
  → Kausale Features — 50D-Basis (scalp_v1) · gesteuerte 70D-Assemblierung
  → Inferenz-Validator — Schema-Hash · Scaler-Dim · Grenzen (lautablehnend)
  → ScalpNet (TCN + Self-Attention) — 4 Logits → Confidence-Gate
  → Regime-Wächter → SMC-Policy-Matrix (~30 Regeln)
  → Risikomotor (Kelly-Sizing · Marge ≤20% · Tier-Kappen · HARD_MAX_LOTS=10)
  → OrderManager (60-Szenario-Router · 11 Positionsstände · atomares Teardown)
  → IMT5Port-Adapter → MT5 / paper / shadow (null Order-Autorität)
  → Buchhaltung (unveränderliches SQLite-WAL) → Erfahrung & Autopsie
  → Observability (Logs · Incidents · Forensik) + Control Center (REST/SSE/WS)
```

## Schichten

| Schicht | Verantwortung | Harte Grenze |
| :--- | :--- | :--- |
| Domain | eingefrorene Pydantic-Verträge | nie mutieren |
| Ports/Adapter | MT5-Win32-IPC · ZMQ · paper · SQLite-WAL-Repo | keine synchrone DB im Hot-Path |
| Features | kausale 50D/70D-Engine · Schema-Registry + Hash | Ordnung ist Vertrag |
| Modelle/Training | ScalpNet · gepurgtes Walk-Forward · Triple-Barrier | kein Lookahead |
| Signale | Policy · SMC-Regelmatrix | keine Order-Autorität |
| Risiko / Ausführung | Sizing-Grenzen · 60-Szenario-Dispatch | autoritative Klemmen |
| Intelligenz-Schleife | Erfahrung · Forschung · Shadow · Governance | null Order-Autorität |
| Observability | Logs · Incidents · Forensik | nur Diagnostik |

## Schlüsseleigenschaften

- **INV-001** — null synchrone DB/Training/Netzwerk auf dem Tick-Pfad.
- **INV-002** — Lernkomponenten können physisch keine Orders platzieren.
- **INV-008** — kein Lookahead; Liquidität strikt kausal.
- **INV-011** — Broker-Wahrheit gewinnt beim Abgleich von Positionen.
- Modellpakete passieren bei jedem Attach das **10-Gate-Load-Gate**; Dim/Hash-Abweichung blockiert laut mit Diagnosecode — niemals still.

Vertiefung: [overview.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/architecture/overview.md)
· [data-flow.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/architecture/data-flow.md)
· [model-pipeline.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/architecture/model-pipeline.md)
· interne autoritative Karte: [`agents/skill.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/skill.md).
