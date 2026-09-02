---
title: Get started
description: Qué es Nexus Scalp Engine, por qué existe y cómo los datos se convierten en una decisión auditable.
lang: es
translation-status: complete
source-revision: en:start@2026-09-02
---

Nexus Scalp Engine es una **plataforma de trading cuantitativo basada en investigación**: un runtime hexagonal y dirigido por eventos para MetaTrader 5 (mercado principal: XAUUSD M1) que conecta datos de mercado, ingeniería causal de características, inferencia de modelos, política de estrategia, control de riesgo, replay, ejecución, observabilidad y validación reproducible en un único pipeline auditable.

<div class="callout"><p><strong>No es una promesa de beneficios.</strong> Es una plataforma de investigación e ingeniería. Los estados se gradúan por evidencia y los resultados negativos se publican — incluido el rechazo de nuestro propio candidato de investigación 70D por evidencia fuera de muestra. El scalping apalancado conlleva un riesgo financiero extremo.</p></div>

## Qué lo hace diferente

- **Evidencia antes que afirmaciones** — las métricas sin evidencia muestran `n/a`, nunca ceros falsos.
- **Sin mirar el futuro** — walk-forward purgado + con embargo, características estrictamente causales (INV-008).
- **Paridad causal** — semántica idéntica en vivo = replay = entrenamiento, protegida por hashing de esquema.
- **Verdad del runtime** — la verdad del bróker gana al estado obsoleto; las puertas son la autoridad (INV-011).
- **Autoridad de órdenes cero** para investigación/aprendizaje (INV-002); la ruta caliente de ticks nunca se bloquea (INV-001).
- **Validación antes de la promoción** — fallo OOS ⇒ RECHAZADO; promoción estrictamente operada por humanos.

## El pipeline de un vistazo

```text
Datos de mercado → Características causales (50D vivo / 70D investigación) → Validador de inferencia
→ ScalpNet → Régimen → Matriz de política SMC → Motor de riesgo → OrderManager
→ Bróker (MT5 / paper / shadow) → Contabilidad (libro inmutable)
→ Bucle de experiencia e investigación → Promoción operada
```

## Explora

<div class="grid">
  <div class="card"><h3>🚀 Ejecutar</h3><p>PAPER por defecto · SHADOW sin órdenes · LIVE controlado.</p><a href="contributing.html">Inicio rápido →</a></div>
  <div class="card"><h3>🗺️ Arquitectura</h3><p>Capas hexagonales, flujo de datos, pipeline del modelo.</p><a href="architecture.html">Abrir →</a></div>
  <div class="card"><h3>🔬 Investigación</h3><p>Walk-forward, puerta OOS, replay, contrafactuales.</p><a href="research.html">Abrir →</a></div>
  <div class="card"><h3>📌 Estado</h3><p>Certificado vs experimental vs planificado — con evidencia.</p><a href="status.html">Abrir →</a></div>
  <div class="card"><h3>🗓️ Hoja de ruta</h3><p>AHORA / DESPUÉS / MÁS ADELANTE con criterios de cierre.</p><a href="roadmap.html">Abrir →</a></div>
  <div class="card"><h3>🧭 Referencia y FAQ</h3><p>Respuestas honestas, vocabulario del proyecto.</p><a href="reference.html">Abrir →</a></div>
</div>

## Ejecútalo en 60 segundos

```bash
git clone https://github.com/Opselon/NexusTradingForexBot.git
cd NexusTradingForexBot
python -m venv .venv && source .venv/bin/activate   # Windows: .\.venv\Scripts\Activate.ps1
pip install -e .[dev]
nexus doctor          # diagnóstico de solo lectura
nexus start           # modo PAPER (seguro por defecto) → http://127.0.0.1:8080
```

Los usuarios finales pueden descargar la versión empaquetada para Windows (sin Python) desde GitHub Releases o usar el instalador de PowerShell. Instrucciones completas: [Instalación](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/getting-started/installation.md).
