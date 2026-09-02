---
title: Investigación
description: Cómo los datos históricos se convierten en evidencia falsable — datasets, backtests, replay, contrafactuales.
lang: es
translation-status: complete
source-revision: en:research@2026-09-02
---

La investigación existe para producir **evidencia falsable**. Un candidato que no puede ser falsado (sin OOS, sin paridad de replay, sin procedencia) nunca toca la ruta en vivo.

## La cadena

```text
DATOS (datasets con huella) → CARACTERÍSTICAS (contrato causal) → ETIQUETADO (triple-barrera)
→ ENTRENAMIENTO (con semilla, determinista) → BACKTEST (con fricción)
→ WALK-FORWARD (purgado + embargo) → PUERTA OOS (fallo ⇒ RECHAZADO)
→ ESTRÉS DE ROBUSTEZ → CONTRAFACTUAL → REGISTRO → SHADOW → PROMOCIÓN OPERADA
```

## Componentes

- **Datasets** — inmutables, con huella, procedencia rastreada; los datasets de ticks se adquieren por la superficie de adaptador certificada y quedan offline tras la adquisición.
- **Backtests** — deterministas; spread/slippage/latencia modelados.
- **Replay** — bit-exacto vs dataset (tests anti-fuga); el replay en streaming ejecuta el motor compartido sobre un reloj lógico con fills simulados y tiene prohibido por test llamar a `order_send`.
- **Contrafactuales (CHG-0041)** — recorre decisiones NO_TRADE con fills hipotéticos: 2095 decisiones, 476 cubiertas; el estrato CONFIDENCE_GATE demostró ser un filtro válido (R medio −0.506); el estrato SUPPORT-margin quedó señalado para revisión de política.

## Procedencia y determinismo

Cada ejecución registra dataset ID, hash de esquema, commit de git y purga/embargo efectivos (regresión BUG-183). `NOT_RECORDED` se escribe cuando honestamente se desconoce — nunca se rellena a posteriori. Los tests hacia adelante con captura congelada re-verifican la congelación tras la ejecución.

Profundizar: [methodology.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/research/methodology.md)
· [datasets.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/research/datasets.md)
· [replay.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/research/replay.md)
· [counterfactuals.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/research/counterfactuals.md).
