---
title: Schnellstart
description: Der kürzeste sichere Weg vom Klon zur laufenden Engine — PAPER als Standard.
lang: de
translation-status: complete
source-revision: en:getting-started/quickstart@9.0.6
---

# Schnellstart

```bash
# 1. Installation (Entwickler, aus dem Quellcode)
git clone https://github.com/Opselon/NexusTradingForexBot.git
cd NexusTradingForexBot
python -m venv .venv && .\.venv\Scripts\Activate.ps1   # Linux: source .venv/bin/activate
pip install -e .[dev]

# 2. System prüfen
nexus doctor          # Diagnose (nur Lesen), 19 Kategorien + Lösungsvorschläge
nexus health          # READY / DEGRADED / NOT READY

# 3. Starten — PAPER als Standard, niemals stillschweigend LIVE
nexus start           # Paper-Simulation, Control Center unter http://127.0.0.1:8080

# 4. Mit echten Daten evaluieren, null Order-Autorität
nexus start --mode shadow

# 5. Stoppen
nexus stop            # für --daemon; Strg+C im Vordergrund
```

## Modi

| Modus | Daten | Orders | Verwendung |
| :--- | :--- | :--- | :--- |
| `paper` (Standard) | simuliert | simuliert | erster Start, UI, Entwicklung |
| `shadow` | echt | **keine — null Autorität** (`simulated=True`) | Modell/Signale am echten Markt bewerten |
| `live` | echt | echt | **explizite interaktive Bestätigung**; volles Risikopanel zuerst |

> [!WARNING]
> Diese Engine platziert im LIVE-Modus **echte Trades mit echtem Geld**.
> Empfohlener Erstlauf: [Demo-Konto → SHADOW → kleines LIVE](#modi).

## Weiter

- [Architektur](../architecture/overview.md) · [Status](../project/status.md) · [FAQ](../reference/faq.md)
