---
title: Inicio rápido
description: El camino más corto y seguro del clon al motor en marcha — PAPER por defecto.
lang: es
translation-status: complete
source-revision: en:getting-started/quickstart@9.0.6
---

# Inicio rápido

```bash
# 1. Instalar (desarrolladores, desde el código)
git clone https://github.com/Opselon/NexusTradingForexBot.git
cd NexusTradingForexBot
python -m venv .venv && .\.venv\Scripts\Activate.ps1   # Linux: source .venv/bin/activate
pip install -e .[dev]

# 2. Verificar el sistema
nexus doctor          # diagnóstico (solo lectura), 19 categorías + correcciones sugeridas
nexus health          # READY / DEGRADED / NOT READY

# 3. Ejecutar — PAPER por defecto, nunca LIVE en silencio
nexus start           # simulación, Control Center en http://127.0.0.1:8080

# 4. Evaluar con datos reales, cero autoridad de órdenes
nexus start --mode shadow

# 5. Detener
nexus stop            # para --daemon; Ctrl+C en primer plano
```

## Modos

| Modo | Datos | Órdenes | Uso |
| :--- | :--- | :--- | :--- |
| `paper` (por defecto) | simulados | simuladas | primera ejecución, UI, desarrollo |
| `shadow` | reales | **ninguna — cero autoridad** (`simulated=True`) | evaluar modelo/señales con mercado real |
| `live` | reales | reales | **confirmación interactiva explícita**; panel de riesgo completo antes |

> [!WARNING]
> Este motor coloca **operaciones reales con dinero real** en modo LIVE. La
> progresión recomendada es [cuenta demo → SHADOW → LIVE pequeño](#modos).

## Siguiente paso

- [Arquitectura](/architecture/overview/) · [Estado](/es/project/status/) · [FAQ](/reference/faq/)
