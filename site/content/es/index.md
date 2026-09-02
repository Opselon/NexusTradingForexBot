---
title: Nexus Scalp Engine
description: Plataforma de investigación y ejecución de trading cuantitativo — características causales, modelos profundos, riesgo invariante, investigación determinista y observabilidad forense.
lang: es
translation-status: complete
source-revision: en:site-content-index@2026-09-02
---

:::cards
- **Evidencia antes que afirmaciones** — las métricas sin evidencia se muestran `n/a`; los resultados negativos se publican, no se ocultan.
- **Sin mirar el futuro** — walk-forward con purge y embargo, características estrictamente causales, replay bit a bit.
- **Verdad del runtime** — los datos del broker y las puertas del runtime prevalecen sobre la intención y las cachés obsoletas.
- **Autoridad de órdenes cero** — los componentes de investigación, sombra y aprendizaje no pueden colocar órdenes.
:::

## ¿Qué es Nexus?

Un motor de scalping hexagonal y dirigido por eventos para MetaTrader 5 —
mercado principal XAUUSD (oro) en M1 — que conecta **50 características
causales**, **inferencia de modelos profundos** (TCN de doble ruta +
self-attention), una **matriz de política SMC**, un **motor de riesgo
acotado**, **herramientas de investigación deterministas** (walk-forward,
puerta OOS, replay, contrafactuales) y **observabilidad forense** en un
único pipeline auditable.

La plataforma publica sus propios resultados negativos — el candidato 70D,
cuidadosamente diseñado, fue **rechazado** por la puerta out-of-sample, y ese
rechazo se conserva como un resultado de primera clase. Una capa de
validación que puede decir *no* es exactamente el punto.

## Qué puedes hacer desde aquí

- **Ejecutarlo** — modo PAPER por defecto, nunca LIVE en silencio:
  [inicio rápido](getting-started/quickstart.md).
- **Entenderlo** — [arquitectura](architecture/overview.md) y el
  [flujo de datos](architecture/data-flow.md) de tick a decisión.
- **Juzgar la investigación** — [metodología](research/methodology.md) y el
  [estado del proyecto](project/status.md), graduado por capacidad con evidencia.
- **Contribuir** — la [guía de contribución](contributing/contribution-guide.md)
  destila un contrato de ingeniería multi-agente en un flujo de trabajo humano.
