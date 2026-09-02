---
title: Hoja de ruta
description: AHORA / DESPUÉS / MÁS ADELANTE / LARGO PLAZO — con objetivos, dependencias y criterios de cierre.
lang: es
translation-status: complete
source-revision: en:roadmap@2026-09-02
---

Disciplina de redacción: los ítems son **Planificado**, **En evaluación**, **Dirección de investigación** o **Completado** — nunca "próximamente", nunca garantizado.

## AHORA (activo)

| Flujo | Ítem | Estado |
| :--- | :--- | :--- |
| VALIDACIÓN | Reconstruir la evidencia del candidato 70D (semántica de confianza corregida, CHG-0042) | En evaluación |
| INVESTIGACIÓN | Profundizar la evidencia contrafactual (CHG-0041) | En progreso |
| ARQUITECTURA | Descomposición de archivos grandes con tests dorados (CHG-0032-A1) | En progreso |
| RUNTIME | Endurecimiento del contrato de constructores de registros (BUG-185) | Completado |

## DESPUÉS

| Flujo | Ítem | Dependencias |
| :--- | :--- | :--- |
| ML | Decisión de promoción 70D — operada, o retiro honesto | candidato validado + verificación de 14 puertas |
| VALIDACIÓN | Revisión de política guiada por contrafactuales | salidas contrafactuales |
| DOCS | 100% de cobertura de traducción de páginas core | flujo de traducción |
| OBSERVABILIDAD | Reducción de brechas OBS-001..016 | evidencia de auditoría |

## MÁS ADELANTE (direcciones de investigación)

Candidato de promoción de liquidez temporal (`scalp_v4_temporal_candidate`) · integración de MSLIE en la política · selección de modelos condicionada al régimen · expansión multi-activo (hoy afinado a XAUUSD).

## LARGO PLAZO (dirección, no compromiso)

Abstracción de bróker más allá de MT5 (costura `IMT5Port`) · perfil opcional PostgreSQL · extracción open-core selectiva.

Hoja de ruta completa con criterios de cierre:
[roadmap.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/project/roadmap.md).
Los ítems nuevos entran por el taskboard con un responsable y un criterio de cierre.
