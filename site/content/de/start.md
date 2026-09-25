---
title: Einstieg
description: Was Nexus Scalp Engine ist, warum es existiert und wie Daten zu einer prüfbaren Handelsentscheidung werden.
lang: de
translation-status: complete
source-revision: en:start@2026-09-02
---

Nexus Scalp Engine ist eine **forschungsgetriebene quantitative Handelsplattform**: eine hexagonale, ereignisgesteuerte Scalping-Runtime für MetaTrader 5 (Hauptmarkt: XAUUSD M1), die Marktdaten, kausale Feature-Technik, Modellinferenz, Strategie-Policy, Risikokontrolle, Replay, Ausführung, Observability und reproduzierbare Validierung zu einer prüfbaren Pipeline verbindet.

<div class="callout"><p><strong>Kein Gewinnversprechen.</strong> Das ist eine Forschungs- und Ingenieursplattform. Statusangaben sind evidenzbasiert graduiert, und negative Ergebnisse werden veröffentlicht — einschließlich der Ablehnung unseres eigenen 70D-Forschungskandidaten auf Basis von Out-of-Sample-Evidenz. Leveraged Scalping birgt extremes finanzielles Risiko.</p></div>

## Was es anders macht

- **Evidenz vor Behauptungen** — Metriken ohne Evidenz zeigen `n/a`, niemals falsche Nullen.
- **Kein Lookahead** — gepurgtes + mit Embargo versehenes Walk-Forward, streng kausale Features (INV-008).
- **Kausale Parität** — identische Semantik in Live = Replay = Training, geschützt durch Schema-Hashing.
- **Laufzeit-Wahrheit** — die Wahrheit des Brokers schlägt veralteten Zustand; Gates sind die Autorität (INV-011).
- **Null Order-Autorität** für Forschungs-/Lernkomponenten (INV-002); der Tick-Hot-Path blockiert nie (INV-001).
- **Validierung vor Promotion** — OOS-Fehlschlag ⇒ REJECTED; Promotion strikt operator-gesteuert.

## Die Pipeline auf einen Blick

```text
Marktdaten → Kausale Features (50D live / 70D Forschung) → Inferenz-Validator
→ ScalpNet → Regime → SMC-Policy-Matrix → Risikomotor → OrderManager
→ Broker (MT5 / paper / shadow) → Buchhaltung (unveränderliches Ledger)
→ Erfahrungs- und Forschungsschleife → Operator-gesteuerte Promotion
```

## Erkunden

<div class="grid">
  <div class="card"><h3>🚀 Ausführen</h3><p>PAPER als Standard · SHADOW ohne Orders · LIVE kontrolliert.</p><a href="contributing.html">Quickstart →</a></div>
  <div class="card"><h3>🗺️ Architektur</h3><p>Hexagonale Schichten, Datenfluss, Modellpipeline.</p><a href="architecture.html">Öffnen →</a></div>
  <div class="card"><h3>🔬 Forschung</h3><p>Walk-Forward, OOS-Gate, Replay, Kontrafaktische.</p><a href="research.html">Öffnen →</a></div>
  <div class="card"><h3>📌 Status</h3><p>Zertifiziert vs. experimentell vs. geplant — mit Evidenz.</p><a href="status.html">Öffnen →</a></div>
  <div class="card"><h3>🗓️ Roadmap</h3><p>JETZT / ALS NÄCHSTES / SPÄTER mit Abschlusskriterien.</p><a href="roadmap.html">Öffnen →</a></div>
  <div class="card"><h3>🧭 Referenz & FAQ</h3><p>Ehrliche Antworten, Projektvokabular.</p><a href="reference.html">Öffnen →</a></div>
</div>

## In 60 Sekunden ausführen

```bash
git clone https://github.com/Opselon/NexusTradingForexBot.git
cd NexusTradingForexBot
python -m venv .venv && source .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
pip install -e .[dev]
nexus doctor          # Diagnose im Nur-Lese-Modus
nexus start           # PAPER-Modus (sicherer Standard) → http://127.0.0.1:8080
```

Endanwender laden die paketierte Windows-Version (ohne Python) von GitHub Releases herunter oder nutzen den PowerShell-Installer. Vollständige Anleitung: [Installation](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/getting-started/installation.md).
