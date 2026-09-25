---
title: Projektstatus
description: Wahrheitsgemäßer, evidenzbasierter Status jeder Kernfähigkeit — ohne Marketing.
lang: de
translation-status: complete
source-revision: en:status@2026-09-02
---

Status-Labels werden **evidenzbasiert graduiert**: *zertifiziert* erfordert formale forensische Abnahme mit reproduzierbaren Artefakten; *implementiert* bedeutet im Code mit Regression-Abdeckung; *experimentell* bedeutet explizit außerhalb des Live-Pfads.

| Dimension | Status | Evidenz |
| :--- | :--- | :--- |
| Version | **v9.0.6** (einzige Quelle `pyproject.toml`) | Release-Pipeline stemplt jedes Artefakt |
| Release | Veröffentlicht (v9.0.0 → v9.0.6; SHA-256, Manifeste, SBOM) | GitHub Releases |
| Live-Ausführung (MT5) | Funktional, mit Account-Identity-Fail-Safe | BUG-142-Suite |
| 50D-Live-Vertrag (`scalp_v1`) | **AKTIV** | `features/schema.py` |
| 70D-Vertrag (`scalp_v3`) | Nur Kandidat | `features/schema_contract.py` |
| 70D-Evidenz | **Negativ / unklar (OOS NOT_ELIGIBLE)** | TASK-05/TASK-09-Berichte |
| Forschungsschleife | End-to-end funktional | research/-Suiten |
| CI | ruff · mypy · kritische Suite ~779 Tests · CodeQL · Trivy | Workflows |
| Modell-Governance | RESTORED_CANDIDATE — Operator-Entscheidung ausstehend | MODEL_GOVERNANCE v2 |

## Kernfähigkeiten

| Fähigkeit | Status |
| :--- | :--- |
| Kausale 50D-Features · Risiko-Klemmen · Shadow-Runtime · OOS-Gate · Installer | ✅ Zertifiziert |
| Modellfabrik · Ausführungs-Router · Buchhaltung · Incidents · Control Center | 🟢 Implementiert |
| 70D-Serie · News-Gate · Temporal · MSLIE | 🟡 Experimentell / 🔵 Forschung |
| Multi-Broker | 📌 Geplant |

Vollständige Matrix: [capabilities.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/project/capabilities.md)
· bekannte Einschränkungen: [status.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/project/status.md)
· forensisches Bug-Ledger: [`agents/bugs.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/bugs.md).
