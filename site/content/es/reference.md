---
title: Referencia y FAQ
description: Glosario del proyecto, terminología y respuestas honestas a las preguntas de los recién llegados.
lang: es
translation-status: complete
source-revision: en:reference@2026-09-02
---

## FAQ

**¿Qué es Nexus?**
Una plataforma de trading cuantitativo basada en investigación: motor de scalping hexagonal y dirigido por eventos para MetaTrader 5 (XAUUSD M1) con características causales, modelos profundos, riesgo invariante, investigación determinista y observabilidad forense.

**¿Es un bot de trading en vivo?**
Puede ejecutar operaciones en vivo — pero PAPER es el modo por defecto, SHADOW no tiene autoridad de órdenes, LIVE exige confirmación interactiva explícita y el repositorio publica candidatos rechazados. Es una plataforma de investigación con un runtime, no una máquina de dinero.

**¿Puedo ejecutarlo sin MT5?**
Sí — el modo PAPER y la suite completa de tests corren sin bróker; Docker funciona de serie en modo PAPER.

**¿Qué es 70D?**
El contrato de características canónico de investigación (50 base + 10 noticias + 10 liquidez). No está en vivo — el contrato vivo es 50D. El candidato 70D está rechazado hasta ahora por evidencia OOS.

**¿Cómo se previene la fuga de información?**
Walk-forward purgado y con embargo, características estrictamente causales, manejo REPLACE+ALIGN del histórico, tests de replay bit-exacto, y purga/embargo efectivos registrados por ejecución.

**¿Cómo se identifican los modelos?**
Manifiestos de artefactos: dataset ID, hash de esquema, identidad del scaler, commit de git — validados por la puerta de carga de 10 pasos en cada conexión.

**¿En qué se diferencia el replay del backtesting?**
El backtest puntúa una estrategia sobre el histórico; el replay demuestra que el *mismo camino de código* que en vivo se comporta idénticamente sobre el histórico (bit-exacto vs dataset).

**¿Es rentable?**
No se hace ninguna afirmación — ese es el punto. Juzga por la evidencia publicada, incluidos los resultados negativos.

## Glosario (selección)

| Término | Significado |
| :--- | :--- |
| 50D / scalp_v1 | el contrato causal de 50 dimensiones ACTIVO en vivo |
| 70D / scalp_v3 | contrato canónico de investigación: Base 0..49 + Noticias 50..59 + Liquidez 60..69 |
| Hash de esquema | SHA-256 sobre el JSON canónico de características — reordenar invalida modelos |
| Shadow | runtime con datos en vivo, `simulated=True`, autoridad de órdenes cero |
| Replay | re-ejecutar la lógica del motor sobre el histórico; bit-exacto vs dataset |
| Puerta OOS | puerta dura fuera de muestra; fallo ⇒ RECHAZADO |
| Champion / Challenger | modelo de producción vs candidato; promoción operada |
| Puerta de despliegue | veredicto forense pre-release: PASS / REVIEW / BLOCK |
| Procedencia | cadena de identidad de un artefacto; `NOT_RECORDED` cuando se desconoce honestamente |
| INV-NNN / BUG-NNN / CHG-NNNN | invariantes / registro forense de bugs / registro de cambios |

Glosario completo: [glossary.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/reference/glossary.md)
· FAQ: [faq.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/reference/faq.md).
