---
title: Roadmap
description: JETZT / ALS NÄCHSTES / SPÄTER / LANGFRISTIG — mit Zielen, Abhängigkeiten und Abschlusskriterien.
lang: de
translation-status: complete
source-revision: en:roadmap@2026-09-02
---

Formulierungsdiziplin: Punkte sind **Geplant**, **In Bewertung**, **Forschungsrichtung** oder **Abgeschlossen** — niemals „bald", niemals garantiert.

## JETZT (aktiv)

| Strom | Punkt | Status |
| :--- | :--- | :--- |
| VALIDIERUNG | Wiederaufbau der 70D-Kandidaten-Evidenz (korrigierte Confidence-Semantik, CHG-0042) | In Bewertung |
| FORSCHUNG | Vertiefung der kontrafaktischen Evidenz (CHG-0041) | In Arbeit |
| ARCHITEKTUR | Aufteilung großer Dateien mit Golden-Tests (CHG-0032-A1) | In Arbeit |
| RUNTIME | Härtung des Record-Builder-Vertrags (BUG-185) | Abgeschlossen |

## ALS NÄCHSTES

| Strom | Punkt | Abhängigkeiten |
| :--- | :--- | :--- |
| ML | 70D-Promotionsentscheidung — operator-gesteuert oder ehrliche Einstellung | validierter Kandidat + 14-Gate-Verifikation |
| VALIDIERUNG | Kontrafaktisch-getriebenes Policy-Review | kontrafaktische Ergebnisse |
| DOCS | 100% Übersetzungsabdeckung der Kernseiten | Übersetzungs-Workflow |
| OBSERVABILITY | Abbau der OBS-001..016-Lücken | Audit-Evidenz |

## SPÄTER (Forschungsrichtungen)

Temporaler Liquiditäts-Promotionskandidat (`scalp_v4_temporal_candidate`) · MSLIE-Integration in die Policy · regimekonditionierte Modellauswahl · Multi-Asset-Erweiterung (derzeit XAUUSD-abgestimmt).

## LANGFRISTIG (Richtung, keine Zusage)

Broker-Abstraktion jenseits von MT5 (`IMT5Port`-Naht) · optionales PostgreSQL-Profil · selektive Open-Core-Auslagerung.

Vollständige Roadmap mit Abschlusskriterien:
[roadmap.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/project/roadmap.md).
Neue Punkte kommen über das Taskboard mit Owner und Abschlusskriterium.
