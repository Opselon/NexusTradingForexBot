---
title: Nexus Scalp Engine
description: Forschungs- und Ausführungsplattform für quantitativen Handel — kausale Merkmale, tiefe Modelle, invariante Risiken, deterministische Forschung, forensische Observability.
lang: de
translation-status: complete
source-revision: en:site-content-index@2026-09-02
---

:::cards
- **Beweise vor Behauptungen** — Metriken ohne Belege werden als `n/a` angezeigt; negative Ergebnisse werden veröffentlicht, nicht verborgen.
- **Kein Blick in die Zukunft** — Walk-Forward mit Purge und Embargo, strikt kausale Merkmale, bit-exaktes Replay.
- **Laufzeit-Wahrheit** — Broker-Fakten und Runtime-Gates schlagen Absichten und veraltete Caches.
- **Null Order-Autorität** — Forschungs-, Shadow- und Lernkomponenten können physisch keine Orders platzieren.
:::

## Was ist Nexus?

Eine hexagonale, ereignisgetriebene Scalping-Engine für MetaTrader 5 —
Primärmarkt XAUUSD (Gold) auf M1 — die **kausale 50D-Merkmale**, **Deep-Learning-Inferenz**
(TCN mit zwei Pfaden + Self-Attention), eine **SMC-Policymatrix**, eine
**begrenzte Risikokomponente**, **deterministische Forschungswerkzeuge**
(Walk-Forward, OOS-Gate, Replay, Kontrafaktische) und **forensische
Observability** in einer auditierbaren Pipeline verbindet.

Die Plattform veröffentlicht ihre eigenen negativen Ergebnisse — der
aufwendig konstruierte 70D-Kandidat wurde vom Out-of-Sample-Gate
**abgelehnt**, und diese Ablehnung wird als Ergebnis erster Klasse
bewahrt. Eine Validierungsschicht, die *Nein* sagen kann, ist der
eigentliche Punkt.

## Was Sie von hier aus tun können

- **Ausführen** — PAPER-Modus als Standard, niemals stillschweigend LIVE:
  siehe [Schnellstart](getting-started/quickstart.md).
- **Verstehen** — [Architektur](architecture/overview.md) und der
  [Datenfluss](architecture/data-flow.md) von Tick zu Entscheidung.
- **Die Forschung bewerten** — [Methodik](research/methodology.md) und
  [Projektstatus](project/status.md), je Fähigkeit evidenzgeprüft.
- **Beitragen** — der [Beitragsleitfaden](contributing/contribution-guide.md)
  destilliert einen Multi-Agenten-Engineering-Vertrag in einen menschlichen Workflow.
