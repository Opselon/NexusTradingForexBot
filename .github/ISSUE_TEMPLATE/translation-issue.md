---
name: Translation issue
about: Report a wrong, missing, or stale translation (fa / es / ar / de)
title: "[I18N] "
labels: documentation, translation
assignees: ""
---

## Translation issue

**Language:** (fa / es / ar / de)

**Page** (site URL or site/content/<lang>/ path):

**Current text:**

**Proposed correction:**

**Why** (terminology contract, grammar, RTL rendering…):

## Checklist

- [ ] Product/module names (Nexus, ScalpNet, OrderManager, 70D, MT5…) are NOT translated — flag only real terminology issues
- [ ] For RTL problems: browser + viewport where it renders wrong
- [ ] Ran `python scripts/docs/check_translations.py` locally (paste output if relevant)
