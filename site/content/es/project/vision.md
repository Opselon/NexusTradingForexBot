---
title: Visión — por qué existe Nexus
description: El problema, la filosofía de ingeniería y la cultura basada en evidencia de Nexus Scalp Engine.
lang: es
translation-status: complete
source-revision: en:project/vision@9.0.6
---

# Visión — por qué Nexus

## El problema

Los proyectos de trading algorítmico suelen fracasar de dos maneras:

1. **Ocultan la verdad.** Métricas fabricadas, fallos silenciosos y la brecha
   entre backtest y runtime nunca se mide.
2. **Filtran el futuro.** Características o etiquetas usan información no
   disponible en el momento de la decisión — resultados estructuralmente
   inválidos.

Nexus Scalp Engine se construyó contra ambos modos de fallo: un **pipeline
completo y auditable** — datos de mercado → características → modelo →
política → riesgo → ejecución → contabilidad → investigación — donde cada etapa
es observable, cada identidad está empañetada (fingerprint) y cada afirmación
es falsable.

## Filosofía (respaldada por el repositorio)

- **Evidencia antes que afirmaciones.** Métricas sin evidencia se muestran
  `n/a` — nunca ceros falsos.
- **Sin mirar el futuro.** Walk-forward con purge + embargo, características
  estrictamente causales, historial del broker REPLACE+ALIGN (INV-008).
- **Paridad causal.** live = replay = entrenamiento; el mismo contrato de
  características con hash de esquema.
- **Verdad del runtime.** La verdad del broker gana al estado local obsoleto
  (INV-011); las filas históricas del libro mayor son inmutables (INV-007).
- **Arquitectura por capas.** ports-and-adapters; los componentes de
  investigación/aprendizaje no tienen autoridad de órdenes (INV-002).
- **Validación antes de la promoción.** Fallo OOS ⇒ REJECTED; la promoción es
  operada por el operador, nunca automática.

## Estado honesto

NSE es un **runtime endurecido con una postura de investigación honesta**: el
pipeline de releases está publicado (etiquetas v9.0.x), la ejecución MT5 en
vivo funciona con fail-safe de identidad de cuenta, y el ciclo de investigación
cerrado existe — mientras la serie 70D sigue **solo como candidata con
evidencia OOS negativa** y el contrato en vivo se mantiene deliberadamente en
50D. Nada se promociona automáticamente.
