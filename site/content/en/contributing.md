---
title: Contributing
description: How to contribute — bootstrap, ownership model, quality gates, documentation workflow.
lang: en
---

The repository is developed by coordinated agents under a strict engineering
contract; human contributors benefit from the same discipline.

## Bootstrap

```bash
git clone https://github.com/Opselon/NexusTradingForexBot.git
cd NexusTradingForexBot
python -m venv .venv && .\.venv\Scripts\Activate.ps1
pip install -e .[dev]
pytest tests/unit -q
```

## Change discipline

- **Read the engineering memory first**: [`agents/skill.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/skill.md)
  (architecture map), [`agents/runtime_invariants.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/runtime_invariants.md),
  [`agents/bugs.md`](https://github.com/Opselon/NexusTradingForexBot/blob/main/agents/bugs.md) (don't re-discover known bugs).
- **Claim before you code** — add a row to `agents/taskboard.md`.
- **Reuse > extend > refactor > create**; hot-path files are convention-locked.
- **Commits**: `<Name>: <summary>` with a structured body; one coherent step per commit.
- **Quality gate**: `./beforePush.sh -SkipPush` (ruff · format · mypy · critical pytest suite · forensic deploy gate).

## Documentation workflow

Docs are owned by the Nexus-Docs role; docs changes never touch runtime code.

```bash
python scripts/docs/check_docs.py            # doctor: links · anchors · translations · secrets · drift · build
python scripts/docs/check_translations.py    # per-language coverage from actual inspection
python scripts/docs/build_site.py            # build the Pages site into site/public
```

English is the source language; translations carry
`translation-status: complete|partial|stale` + `source-revision` front-matter.
Product/module names stay untranslated; canonical terminology lives in
`site/terminology/terms.csv`.

## Adding a language

1. Create `site/content/<lang>/` (copy the English page set).
2. Register the language in `scripts/docs/site_config.py` (`dir: rtl` for
   right-to-left — the layout flips automatically and code stays LTR).
3. Mark pages with `lang` + `translation-status` + `source-revision`.
4. Add canonical glossary terms to `site/terminology/terms.csv`.
5. Run the doctor + translation audit; open a docs-only PR.
