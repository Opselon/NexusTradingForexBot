---
title: Nexus Scalp Engine — Documentación
description: Centro de documentación de Nexus Scalp Engine — plataforma de investigación y ejecución de trading cuantitativo.
lang: es
translation-status: complete
source-revision: en:index@9.0.6
---

# Nexus Scalp Engine — Documentación

**Plataforma de trading cuantitativo basada en investigación** — un motor de
scalping hexagonal y dirigido por eventos para MetaTrader 5 (mercado principal:
XAUUSD M1), construido en torno a ingeniería de características causal,
gobernanza de modelos artefacto-primero, herramientas de investigación
deterministas y observabilidad forense.

> [!IMPORTANT]
> Esta documentación describe una **plataforma de investigación e ingeniería**.
> No es asesoramiento de inversión ni una promesa de rentabilidad. El scalping
> apalancado conlleva un riesgo financiero extremo. Consulta
> [Estado del proyecto](../project/status.md).

## Empieza aquí

| Pregunta | Página |
| :--- | :--- |
| ¿Qué es Nexus y por qué existe? | [Visión](vision.md) |
| ¿Cómo lo ejecuto? | [Inicio rápido](quickstart.md) |
| ¿Cómo funciona la arquitectura? | [Arquitectura](../architecture/overview.md) |
| ¿Cómo se valida la investigación? | [Metodología](../research/methodology.md) |
| ¿Qué es real vs experimental? | [Estado](../project/status.md) · [Matriz de capacidades](../project/capabilities.md) |
| ¿Hacia dónde va el proyecto? | [Hoja de ruta](../project/roadmap.md) |
| ¿Cómo contribuyo? | [Guía de contribución](../contributing/contribution-guide.md) |

## Inicio rápido

```bash
nexus doctor          # diagnóstico completo (solo lectura)
nexus start           # modo PAPER por defecto — nunca LIVE sin confirmación explícita
nexus start --mode shadow   # datos de mercado reales, cero autoridad de órdenes
```

## Idiomas

| 🇬🇧 English | 🇮🇷 فارسی | 🇪🇸 Español | 🇸🇦 العربية | 🇩🇪 Deutsch |
| :---: | :---: | :---: | :---: | :---: |
| [completo](/index.md) | [نمای کلی](/fa/index.md) | **actual** | [نظرة عامة](/ar/index.md) | [Übersicht](/de/index.md) |

Otras páginas están disponibles en inglés; la cobertura de traducción se audita
con `scripts/docs/check_translations.py`.
