---
title: Forschung
description: Wie historische Daten zu falsifizierbarer Evidenz werden — Datensätze, Backtests, Replay, Kontrafaktische.
lang: de
translation-status: complete
source-revision: en:research@2026-09-02
---

Forschung existiert, um **falsifizierbare Evidenz** zu erzeugen. Ein Kandidat, der nicht falsifiziert werden kann (kein OOS, keine Replay-Parität, keine Provenienz), berührt den Live-Pfad nie.

## Die Kette

```text
DATEN (fingerprintierte Datensätze) → FEATURES (kausaler Vertrag) → LABELING (Triple-Barrier)
→ TRAINING (seeded, deterministisch) → BACKTEST (reibungsbewusst)
→ WALK-FORWARD (gepurgt + Embargo) → OOS-GATE (Fehlschlag ⇒ REJECTED)
→ ROBUSTHEITS-STRESS → KONTRAFAKTISCH → REGISTRY → SHADOW → OPERATOR-PROMOTION
```

## Komponenten

- **Datensätze** — unveränderlich, fingerprintiert, Provenienz getrackt; Tick-Datensätze werden über die zertifizierte Adapterfläche bezogen und sind danach offline.
- **Backtests** — deterministisch; Spread/Slippage/Latenz modelliert.
- **Replay** — bit-exakt vs. Datensatz (Anti-Leakage-Tests); Streaming-Replay führt die geteilte Engine über eine logische Uhr mit simulierten Fills aus und darf per Test niemals `order_send` aufrufen.
- **Kontrafaktische (CHG-0041)** — Wandert durch NO_TRADE-Entscheidungen mit hypothetischen Fills: 2095 Entscheidungen, 476 abgedeckt; die CONFIDENCE_GATE-Schicht erwies sich als gültiger Filter (mittleres R −0.506); die SUPPORT-margin-Schicht wurde für Policy-Review markiert.

## Provenienz & Determinismus

Jeder Lauf registriert Datensatz-ID, Schema-Hash, Git-Commit und effektive Purg/Embargo-Werte (BUG-183-Regression). `NOT_RECORDED` wird geschrieben, wenn etwas ehrlich unbekannt ist — niemals nachträglich erfunden. Forward-Tests mit eingefrorenem Capture verifizieren das Einfrieren nach dem Lauf erneut.

Vertiefung: [methodology.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/research/methodology.md)
· [datasets.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/research/datasets.md)
· [replay.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/research/replay.md)
· [counterfactuals.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/research/counterfactuals.md).
