---
title: Nexus Scalp Engine — Dokumentation
description: Dokumentationszentrum des Nexus Scalp Engine — quantitative Forschungs- und Handelsplattform.
lang: de
translation-status: complete
source-revision: en:index@9.0.6
---

# Nexus Scalp Engine — Dokumentation

**Forschungsgetriebene quantitative Handelsplattform** — eine hexagonale,
ereignisgetriebene Scalping-Engine für MetaTrader 5 (Primärmarkt: XAUUSD M1),
gebaut um kausale Feature-Technik, artefaktbasierte Modellgovernance,
deterministische Forschungswerkzeuge und forensische Observierbarkeit.

> [!IMPORTANT]
> Diese Dokumentation beschreibt eine **Forschungs- und Engineering-Plattform**.
> Sie ist keine Anlageberatung und enthält keine Renditeversprechen. Leveraged
> Scalping birgt extreme finanzielle Risiken. Siehe
> [Projektstatus](../project/status.md).

## Hier starten

| Frage | Seite |
| :--- | :--- |
| Was ist Nexus und warum gibt es es? | [Vision](vision.md) |
| Wie führe ich es aus? | [Schnellstart](quickstart.md) |
| Wie ist die Architektur aufgebaut? | [Architektur](../architecture/overview.md) |
| Wie wird Forschung validiert? | [Methodik](../research/methodology.md) |
| Was ist real vs. experimentell? | [Status](../project/status.md) |
| Wohin geht das Projekt? | [Roadmap](../project/roadmap.md) |
| Wie kann ich mitwirken? | [Beitragsguide](../contributing/contribution-guide.md) |

## Schnellstart

```bash
nexus doctor          # vollständige Diagnose (nur Lesen)
nexus start           # PAPER-Modus als Standard — niemals LIVE ohne Bestätigung
nexus start --mode shadow   # echte Marktdaten, null Order-Autorität
```

## Sprachen

| 🇬🇧 English | 🇮🇷 فارسی | 🇪🇸 Español | 🇸🇦 العربية | 🇩🇪 Deutsch |
| :---: | :---: | :---: | :---: | :---: |
| [vollständig](/index.md) | [نمای کلی](/fa/index.md) | [vista previa](/es/index.md) | [نظرة عامة](/ar/index.md) | **aktuell** |

Weitere Seiten sind auf Englisch verfügbar; die Übersetzungsabdeckung wird mit
`scripts/docs/check_translations.py` geprüft.
