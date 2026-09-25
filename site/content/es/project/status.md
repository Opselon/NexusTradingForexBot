---
title: Estado del proyecto
description: Estado veraz de cada capacidad principal — certificado, implementado, experimental, planificado.
lang: es
translation-status: complete
source-revision: en:project/status@9.0.6
---

# Estado del proyecto

> [!IMPORTANT]
> Las etiquetas se gradúan por evidencia. «Implementado» = existe en código y
> está cubierto por pruebas. «Certificado» = además con aceptación forense
> formal. Aquí no se promete rentabilidad.

## Instantánea

| Dimensión | Estado | Evidencia |
| :--- | :--- | :--- |
| Versión | **9.0.6** (semver, fuente única `pyproject.toml`) | pipeline de releases |
| Release | **Publicado** (v9.0.0 → v9.0.6; SHA-256, manifiestos, SBOM) | GitHub Releases |
| Runtime | Endurecido; PAPER por defecto; LIVE con confirmación explícita | contrato CLI + doctor |
| Ejecución en vivo | Funcional (MT5 Win32 IPC + gateway ZMQ) con fail-safe de identidad de cuenta | regresión BUG-142 |
| Ciclo de investigación | De extremo a extremo (datasets → walk-forward → puerta OOS) | suites research/ |
| Contrato en vivo 50D (`scalp_v1`) | **ACTIVO** | `features/schema.py` |
| Contrato 70D (`scalp_v3`) | SSoT de investigación; **solo candidato** | `features/schema_contract.py` |
| Evidencia 70D hasta hoy | walk-forward y shadow con datos reales **negativos / no concluyentes** (OOS NOT_ELIGIBLE) | informes TASK-05/TASK-09 |
| Gobernanza del campeón | RESTORED_CANDIDATE, decisión del operador pendiente; nada se auto-promueve | `governance/` |
| CI | ruff · mypy · pytest (~779 pruebas críticas) · CodeQL · Trivy | `.github/workflows` |

## Limitaciones conocidas (publicadas, no ocultas)

- La serie 70D es solo candidata con evidencia OOS negativa; el contrato en vivo
  se queda en 50D hasta que un candidato supere todas las puertas **y** un
  operador lo promocione.
- Release empaquetado solo para Windows x64.
- El motor de noticias es opt-in (desactivado hasta añadir `news:` a la config).
- El libro completo de bugs (causa raíz, evidencia, guardas de regresión) es
  público: [`agents/bugs.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/bugs.md)
