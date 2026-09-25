---
title: Vision — warum Nexus existiert
description: Das Problem, die Engineering-Philosophie und die evidenzbasierte Kultur des Nexus Scalp Engine.
lang: de
translation-status: complete
source-revision: en:project/vision@9.0.6
---

# Vision — warum Nexus?

## Das Problem

Algorithmische Handelsprojekte scheitern meist auf eine von zwei Arten:

1. **Sie verstecken die Wahrheit.** Metriken sind fabriziert oder optimistisch,
   Fehler passieren still, und die Lücke zwischen Backtest und Runtime wird nie
   gemessen.
2. **Sie leaken die Zukunft.** Features oder Labels verwenden Informationen,
   die zum Entscheidungszeitpunkt nicht verfügbar waren — Ergebnisse sind
   strukturell ungültig, egal wie gut sie aussehen.

Der Nexus Scalp Engine wurde gezielt gegen beide Fehlermodi gebaut: eine
**vollständige, prüfbare Pipeline** — Marktdaten → Features → Modell →
Policy → Risiko → Ausführung → Buchhaltung → Forschung — in der jede Stufe
beobachtbar, jede Identität mit Fingerabdruck versehen und jede Aussage
falsifizierbar ist.

## Philosophie (im Repository verankert)

- **Belege vor Behauptungen.** Metriken ohne Evidenz zeigen `n/a` — niemals
  fake Nullen.
- **Kein Lookahead.** Gepurgedes + geembargotes Walk-Forward, streng kausale
  Features, Broker-Historie REPLACE+ALIGN (INV-008).
- **Kausale Parität.** live = replay = Training; derselbe Feature-Vertrag mit
  Schema-Hash.
- **Runtime-Wahrheit.** Broker-Wahrheit schlägt veralteten lokalen Zustand
  (INV-011); historische Ledger-Zeilen sind unveränderlich (INV-007).
- **Schichtenarchitektur.** Ports-and-Adapters; Forschungs-/Lernkomponenten
  haben null Order-Autorität (INV-002).
- **Validierung vor Promotion.** OOS-Fehlschlag ⇒ REJECTED; Promotion erfolgt
  nur operatorenkontrolliert.

## Ehrlicher Status

NSE ist eine **produktionsgehärtete Runtime mit ehrlicher Forschungshaltung**:
Die Release-Pipeline ist real und veröffentlicht (v9.0.x-Tags), die MT5-Live-
Ausführung funktioniert mit Account-Identity-Fail-Safe, und die geschlossene
Forschungsschleife existiert — während die 70D-Forschungsreihe
**nur Kandidat mit negativer OOS-Evidenz** bleibt und der Live-Vertrag
bewusst bei 50D bleibt. Nichts wird automatisch befördert.
