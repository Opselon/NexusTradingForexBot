---
title: Mitwirken
description: Wie man beiträgt — Bootstrap, Eigentumsmodell, Qualitäts-Gates, Dokumentations-Workflow.
lang: de
translation-status: complete
source-revision: en:contributing@2026-09-02
---

Das Repository wird von koordinierten Agenten unter einem strengen Ingenieursvertrag entwickelt; menschliche Beitragende profitieren von derselben Disziplin.

## Bootstrap

```bash
git clone https://github.com/Opselon/NexusTradingForexBot.git
cd NexusTradingForexBot
python -m venv .venv && .\.venv\Scripts\Activate.ps1
pip install -e .[dev]
pytest tests/unit -q
```

## Änderungsdiziplin

- **Lies zuerst das Engineering-Gedächtnis**: [`agents/skill.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/skill.md) (Architekturkarte), [`agents/runtime_invariants.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/runtime_invariants.md), [`agents/bugs.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/bugs.md).
- **Beanspruche, bevor du programmierst** — füge eine Zeile zu `agents/taskboard.md` hinzu.
- **Wiederverwenden > erweitern > refaktorieren > erstellen**; Hot-Path-Dateien sind konventionsgesperrt.
- **Commits**: `<Name>: <Zusammenfassung>` mit strukturiertem Body; ein kohärenter Schritt pro Commit.
- **Qualitäts-Gate**: `./beforePush.sh -SkipPush` (ruff · format · mypy · kritische pytest-Suite · forensisches Deploy-Gate).

## Dokumentations-Workflow

Die Dokumentation gehört der Rolle Nexus-Docs; Doku-Änderungen berühren niemals Runtime-Code.

```bash
python scripts/docs/check_docs.py            # Doctor: Links · Anker · Übersetzungen · Secrets · Drift · Build
python scripts/docs/check_translations.py    # Abdeckung pro Sprache aus echter Inspektion
python scripts/docs/build_site.py            # baut die Pages-Site nach site/public
```

Englisch ist die Quellsprache; Übersetzungen tragen `translation-status` und `source-revision`. Produkt-/Modulnamen bleiben unübersetzt; kanonische Terminologie liegt in `site/terminology/terms.csv`.

## Sprache hinzufügen

1. Lege `site/content/<lang>/` an (kopiere den englischen Seitensatz).
2. Registriere die Sprache in `scripts/docs/site_config.py` (`dir: rtl` für Rechts-nach-Links — das Layout dreht automatisch und Code bleibt LTR).
3. Markiere Seiten mit `lang` + `translation-status` + `source-revision`.
4. Ergänze kanonische Begriffe in `site/terminology/terms.csv`.
5. Führe Doctor und Übersetzungs-Audit aus; öffne einen Docs-only-PR.
