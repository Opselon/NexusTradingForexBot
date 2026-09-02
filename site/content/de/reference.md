---
title: Referenz & FAQ
description: Projektglossar, Terminologie und ehrliche Antworten auf die Fragen von Neueinsteigern.
lang: de
translation-status: complete
source-revision: en:reference@2026-09-02
---

## FAQ

**Was ist Nexus?**
Eine forschungsgetriebene quantitative Handelsplattform: hexagonale, ereignisgesteuerte Scalping-Engine für MetaTrader 5 (XAUUSD M1) mit kausalen Features, tiefen Modellen, invariantem Risiko, deterministischer Forschung und forensischer Observability.

**Ist das ein Live-Trading-Bot?**
Er *kann* Live-Trades ausführen — aber PAPER ist der Standardmodus, SHADOW hat null Order-Autorität, LIVE erfordert explizite interaktive Bestätigung, und das Repository veröffentlicht abgelehnte Kandidaten. Das ist eine Forschungsplattform mit einer Runtime, keine Geldmaschine.

**Kann ich es ohne MT5 ausführen?**
Ja — PAPER-Modus und die gesamte Testsuite laufen ohne Broker; Docker funktioniert ab Werk im PAPER-Modus.

**Was ist 70D?**
Der kanonische Forschungs-Feature-Vertrag (50 Basis + 10 News + 10 Liquidität). Nicht live — der Live-Vertrag ist 50D. Der 70D-Kandidat ist bislang auf OOS-Evidenz abgelehnt.

**Wie wird Leakage verhindert?**
Gepurgtes + embargo Walk-Forward, streng kausale Features, REPLACE+ALIGN-Historienbehandlung, bit-exakte Replay-Tests und registrierte effektive Purg/Embargo-Werte pro Lauf.

**Wie werden Modelle identifiziert?**
Artefakt-Manifeste: Datensatz-ID, Schema-Hash, Scaler-Identität, Git-Commit — validiert vom 10-Gate-Load-Gate bei jedem Attach.

**Wie unterscheidet sich Replay vom Backtesting?**
Backtest bewertet eine Strategie auf Historie; Replay beweist, dass sich *derselbe Codepfad* wie live identisch auf Historie verhält (bit-exakt vs. Datensatz).

**Ist es profitabel?**
Es wird keine Behauptung aufgestellt — genau das ist der Punkt. Beurteilen Sie anhand der veröffentlichten Evidenz, einschließlich der negativen Ergebnisse.

## Glossar (Auswahl)

| Begriff | Bedeutung |
| :--- | :--- |
| 50D / scalp_v1 | der AKTIVE 50-dimensionale kausale Feature-Vertrag im Live-Betrieb |
| 70D / scalp_v3 | kanonischer Forschungsvertrag: Basis 0..49 + News 50..59 + Liquidität 60..69 |
| Schema-Hash | SHA-256 über kanonisches Feature-JSON — Umordnung invalidiert Modelle |
| Shadow | Runtime mit Live-Daten, `simulated=True`, null Order-Autorität |
| Replay | Erneute Ausführung der Engine-Logik auf Historie; muss bit-exakt vs. Datensatz sein |
| OOS-Gate | hartes Out-of-Sample-Gate; Fehlschlag ⇒ REJECTED |
| Champion / Challenger | Produktionsmodell vs. Kandidat; Promotion operator-gesteuert |
| Deploy-Gate | forensisches Pre-Release-Verdikt: PASS / REVIEW / BLOCK |
| Provenienz | Identitätskette eines Artefakts; `NOT_RECORDED` wenn ehrlich unbekannt |
| INV-NNN / BUG-NNN / CHG-NNNN | Invarianten / forensisches Bug-Ledger / Change-Registry |

Vollständiges Glossar: [glossary.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/reference/glossary.md)
· FAQ: [faq.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/reference/faq.md).
