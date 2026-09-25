---
title: Estado del proyecto
description: Estado veraz y graduado por evidencia de cada capacidad principal — sin marketing.
lang: es
translation-status: complete
source-revision: en:status@2026-09-02
---

Las etiquetas de estado se **gradúan por evidencia**: *certificado* requiere aceptación forense formal con artefactos reproducibles; *implementado* significa en código con cobertura de regresión; *experimental* significa explícitamente fuera de la ruta en vivo.

| Dimensión | Estado | Evidencia |
| :--- | :--- | :--- |
| Versión | **v9.0.6** (fuente única `pyproject.toml`) | el pipeline de release sella cada artefacto |
| Release | Publicado (v9.0.0 → v9.0.6; SHA-256, manifiestos, SBOM) | GitHub Releases |
| Ejecución en vivo (MT5) | Operativa, con fail-safe de identidad de cuenta | suite BUG-142 |
| Contrato vivo 50D (`scalp_v1`) | **ACTIVO** | `features/schema.py` |
| Contrato 70D (`scalp_v3`) | Solo candidato | `features/schema_contract.py` |
| Evidencia 70D | **Negativa / inconclusa (OOS NOT_ELIGIBLE)** | informes TASK-05/TASK-09 |
| Bucle de investigación | Operativo de extremo a extremo | suites de research/ |
| CI | ruff · mypy · suite crítica ~779 tests · CodeQL · Trivy | workflows |
| Gobernanza de modelos | RESTORED_CANDIDATE — decisión del operador pendiente | MODEL_GOVERNANCE v2 |

## Capacidades destacadas

| Capacidad | Estado |
| :--- | :--- |
| Características causales 50D · límites de riesgo · runtime shadow · puerta OOS · instalador | ✅ Certificado |
| Fábrica de modelos · router de ejecución · contabilidad · incidentes · Centro de Control | 🟢 Implementado |
| Serie 70D · puerta de noticias · temporal · MSLIE | 🟡 Experimental / 🔵 Investigación |
| Multi-bróker | 📌 Planificado |

Matriz completa: [capabilities.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/project/capabilities.md)
· limitaciones conocidas: [status.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/project/status.md)
· registro forense de bugs: [`agents/bugs.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/bugs.md).
