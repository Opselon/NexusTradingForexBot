---
title: Arquitectura
description: Arquitectura hexagonal y dirigida por eventos — capas, fronteras y el camino del tick a la decisión.
lang: es
translation-status: complete
source-revision: en:architecture@2026-09-02
---

NSE es **hexagonal (ports-and-adapters) y dirigido por eventos**: las plataformas de bróker, modelos y adaptadores de red están detrás de contratos de puerto (`IMT5Port`, `IGatewayPort`); el dominio nunca sabe qué bróker está conectado.

```text
Datos de mercado (MT5 / ZMQ / paper)
  → Características causales — base 50D (scalp_v1) · ensamblado 70D gobernado
  → Validador de inferencia — hash de esquema · dim del scaler · límites (rechazo sonoro)
  → ScalpNet (TCN + auto-atención) — 4 logits → puerta de confianza
  → Guardián de régimen → Matriz de política SMC (~30 reglas)
  → Motor de riesgo (tamaño Kelly · margen ≤20% · topes de nivel · HARD_MAX_LOTS=10)
  → OrderManager (router de 60 escenarios · 11 estados de posición · teardown atómico)
  → Adaptador IMT5Port → MT5 / paper / shadow (autoridad de órdenes cero)
  → Contabilidad (SQLite WAL inmutable) → Experiencia y autopsia
  → Observabilidad (logs · incidentes · forense) + Centro de Control (REST/SSE/WS)
```

## Capas

| Capa | Responsabilidad | Frontera dura |
| :--- | :--- | :--- |
| Dominio | contratos Pydantic congelados | nunca mutar |
| Puertos/Adaptadores | IPC Win32 de MT5 · ZMQ · paper · repositorio SQLite WAL | sin BD síncrona en la ruta caliente |
| Características | motor causal 50D/70D · registro y hash de esquema | el orden es contrato |
| Modelos/Entrenamiento | ScalpNet · walk-forward purgado · triple-barrera | sin lookahead |
| Señales | política · matriz de reglas SMC | sin autoridad de órdenes |
| Riesgo / Ejecución | límites de tamaño · despacho de 60 escenarios | topes autoritativos |
| Bucle de inteligencia | experiencia · investigación · shadow · gobernanza | autoridad de órdenes cero |
| Observabilidad | logs · incidentes · forense | solo diagnóstico |

## Propiedades clave

- **INV-001** — cero BD/entrenamiento/red síncronos en la ruta de ticks.
- **INV-002** — los componentes de aprendizaje no pueden colocar órdenes.
- **INV-008** — sin lookahead; liquidez estrictamente causal.
- **INV-011** — la verdad del bróker gana al reconciliar posiciones.
- Los paquetes de modelos pasan la **puerta de carga de 10 pasos** en cada conexión; el desajuste de dimensión/hash bloquea con código de diagnóstico, nunca en silencio.

Profundizar: [overview.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/architecture/overview.md)
· [data-flow.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/architecture/data-flow.md)
· [model-pipeline.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/architecture/model-pipeline.md)
· mapa interno autoritativo: [`agents/skill.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/skill.md).
