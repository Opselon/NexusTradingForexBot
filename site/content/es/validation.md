---
title: Validación
description: La cadena de puertas — qué significa "validado" y por qué los rechazos se publican.
lang: es
translation-status: complete
source-revision: en:validation@2026-09-02
---

La validación es la razón de ser de la plataforma. Un fallo en cualquier puerta es terminal para ese candidato — sin promedios, sin excepciones.

## La cadena de puertas

| Capa | Puertas |
| :--- | :--- |
| Dataset | puertas de calidad · huella · procedencia |
| Características | hash de esquema · dimensión · límites · orden |
| Modelo | puerta de carga de 10 pasos · calibración · evidencia mínima |
| Investigación | walk-forward purgado/con embargo · pisos OOS · robustez |
| Gobernanza | verificación de 14 puertas · transacción de promoción |
| Runtime | puerta de despliegue forense antes de cada release |
| Release | SHA-256 · manifiestos · SBOM · verificación posterior |

## Pisos OOS (ciclo de vida del modelo)

macro-F1 ≥ 0.34 · exactitud balanceada ≥ 0.34 · ECE ≤ 0.15 · evidencia ≥ 100 filas.

## La puerta muerde — prueba pública

El candidato de liquidez 70D — la serie de investigación más trabajada del repositorio — fue rechazado por esta misma cadena (OOS NOT_ELIGIBLE) tras unos benchmarks walk-forward y shadow negativos con datos reales. El contrato en vivo se quedó en 50D. Una puerta que no rechaza nada es decoración; esta tiene un rechazo público en su historial.

Profundizar: [out-of-sample.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/research/out-of-sample.md)
· [validation.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/research/validation.md)
· [reproducibility.md](https://github.com/Opselon/NexusTradingForexBot/blob/main/docs/research/reproducibility.md).
