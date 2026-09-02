---
title: Projektstatus
description: Wahrheitsgemäßer Status jeder Hauptfunktion — zertifiziert, implementiert, experimentell, geplant.
lang: de
translation-status: complete
source-revision: en:project/status@9.0.6
---

# Projektstatus

> [!IMPORTANT]
> Status-Labels sind evidenzbasiert. „Implementiert" = im Code vorhanden und
> von Tests abgedeckt. „Zertifiziert" = zusätzlich mit formaler forensischer
> Abnahme. Hier wird keine Rentabilität versprochen.

## Überblick

| Dimension | Status | Belege |
| :--- | :--- | :--- |
| Version | **9.0.6** (Semver, einzelne Quelle `pyproject.toml`) | Release-Pipeline |
| Releases | **Veröffentlicht** (v9.0.0 → v9.0.6; SHA-256, Manifeste, SBOM) | GitHub Releases |
| Runtime | Gehärtet; PAPER als Standard; LIVE nur mit Bestätigung | CLI-Vertrag + Pre-Flight-Doctor |
| Live-Ausführung | Funktional (MT5 Win32 IPC + ZMQ-Gateway) mit Account-Fail-Safe | BUG-142-Regressionsuite |
| Forschungsschleife | Ende-zu-Ende (Datensätze → Walk-Forward → OOS-Gate) | research/-Suites |
| Live-Vertrag 50D (`scalp_v1`) | **AKTIV** | `features/schema.py` |
| 70D-Vertrag (`scalp_v3`) | Forschungs-SSoT; **nur Kandidat** | `features/schema_contract.py` |
| 70D-Evidenz bis heute | Walk-Forward & Shadow mit echten Daten **negativ / inkonklusiv** (OOS NOT_ELIGIBLE) | TASK-05/TASK-09-Berichte |
| Champion-Governance | RESTORED_CANDIDATE, Operatorentscheidung ausstehend; keine Auto-Promotion | `governance/` |
| CI | ruff · mypy · pytest (~779 kritische Tests) · CodeQL · Trivy | `.github/workflows` |

## Bekannte Einschränkungen (veröffentlicht, nicht verborgen)

- Die 70D-Reihe ist nur Kandidat mit negativer OOS-Evidenz; der Live-Vertrag
  bleibt 50D, bis ein Kandidat alle Gates besteht **und** ein Operator
  befördert.
- Gepacktes Release nur für Windows x64.
- Die News-Engine ist Opt-in (deaktiviert bis `news:`-Block in der Konfig).
- Das vollständige forensische Bug-Ledger (Ursachen, Belege, Regressionsschutz)
  ist öffentlich: [`agents/bugs.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/bugs.md)
