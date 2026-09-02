---
title: Contribuir
description: Cómo contribuir — bootstrap, modelo de propiedad, puertas de calidad, flujo de documentación.
lang: es
translation-status: complete
source-revision: en:contributing@2026-09-02
---

El repositorio se desarrolla con agentes coordinados bajo un contrato de ingeniería estricto; los contribuyentes humanos se benefician de la misma disciplina.

## Bootstrap

```bash
git clone https://github.com/Opselon/NexusTradingForexBot.git
cd NexusTradingForexBot
python -m venv .venv && .\.venv\Scripts\Activate.ps1
pip install -e .[dev]
pytest tests/unit -q
```

## Disciplina de cambio

- **Lee primero la memoria de ingeniería**: [`agents/skill.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/skill.md) (mapa de arquitectura), [`agents/runtime_invariants.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/runtime_invariants.md), [`agents/bugs.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/bugs.md).
- **Reclama antes de codificar** — añade una fila a `agents/taskboard.md`.
- **Reutilizar > extender > refactorizar > crear**; los archivos de ruta caliente están bloqueados por convención.
- **Commits**: `<Nombre>: <resumen>` con cuerpo estructurado; un paso coherente por commit.
- **Puerta de calidad**: `./beforePush.sh -SkipPush` (ruff · format · mypy · suite crítica pytest · puerta de despliegue forense).

## Flujo de documentación

La documentación pertenece al rol Nexus-Docs; los cambios de documentación nunca tocan código del runtime.

```bash
python scripts/docs/check_docs.py            # doctor: enlaces · anclas · traducciones · secretos · deriva · build
python scripts/docs/check_translations.py    # cobertura por idioma desde inspección real
python scripts/docs/build_site.py            # construye el sitio Pages en site/public
```

El inglés es el idioma fuente; las traducciones llevan `translation-status` y `source-revision`. Los nombres de productos/módulos no se traducen; la terminología canónica vive en `site/terminology/terms.csv`.

## Añadir un idioma

1. Crea `site/content/<lang>/` (copia el conjunto de páginas en inglés).
2. Registra el idioma en `scripts/docs/site_config.py` (`dir: rtl` para derecha-a-izquierda — el layout gira automáticamente y el código queda en LTR).
3. Marca las páginas con `lang` + `translation-status` + `source-revision`.
4. Añade los términos canónicos a `site/terminology/terms.csv`.
5. Ejecuta el doctor y la auditoría de traducciones; abre un PR solo de documentación.
