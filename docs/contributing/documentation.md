---
title: Documentation Workflow
description: How documentation is authored, validated, translated, and deployed — the docs contract.
lang: en
---

# Documentation Workflow

## Ownership

Documentation is owned by the **Nexus-Docs** role
(registry: `docs/agent_handoffs/DOCS_REGISTRY.md`). The boundary is hard:
docs changes never touch runtime code, and code owners never need to fix docs
tooling. If documentation exposes a suspected code defect, it is recorded in
the registry with subsystem attribution — the implementation stays untouched.

## Information architecture

```text
docs/            engineering documentation (markdown, this IA tree)
site/            GitHub Pages source (builds docs/ + translations into a static site)
scripts/docs/    validation tooling (check_docs.py, check_translations.py, build_site.py)
.github/workflows/docs.yml    docs CI + Pages deploy
```

## Source of truth

- **English** is the source language; every other language is a translation
  with status metadata (`complete | partial | stale` + `source_revision`).
- Version numbers and capability statuses are **not** duplicated: the site
  build reads `pyproject.toml`; capability labels live in
  [Project Status](../project/status.md) and [Capability Matrix](../project/capabilities.md)
  only. Drift is detected by `check_docs.py`.

## Validation (local = CI, offline)

```bash
python scripts/docs/check_docs.py          # full doctor: links, anchors, translations, secrets, drift, build
python scripts/docs/check_translations.py  # coverage/staleness audit with numbers
```

CI runs the same checks on every docs-affecting PR — no noisy false positives,
deterministic output, `DOCS_HEALTH = PASS|FAIL` verdict with actionable
diagnostics.

## Translating a page

1. Copy the English source into the target language tree.
2. Set front-matter `lang` and `translation-status: complete|partial` +
   `source-revision` (the English page's identity).
3. Keep product/module names untranslated (Nexus, ScalpNet, OrderManager,
   70D, MT5…); use the terminology glossary for everything else
   ([terminology](../reference/terminology.md)).
4. RTL languages (fa, ar): content flows RTL; code, CLI, paths, URLs stay
   LTR — the site CSS handles this via `[dir=rtl]` rules; do not inline
   direction hacks.
5. Run `check_translations.py` before committing.

See [Adding a language](add-language.md) for the full recipe.
