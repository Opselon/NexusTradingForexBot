---
title: Adding a Language
description: The step-by-step recipe for adding a new language to the Nexus documentation platform.
lang: en
---

# Adding a Language

The multilingual architecture is data-driven: adding Turkish, French, Russian,
Chinese or Japanese is a directory + registry entry — no code changes.

## Recipe

1. **Create the language tree**
   ```text
   site/content/<lang>/            # translated site content (mirror of en tree)
   docs/<lang>/index.md            # optional docs-root landing page
   ```

2. **Register the language** in `scripts/docs/site_config.py`:
   ```python
   LANGUAGES = {
       "en": {"name": "English", "dir": "ltr"},
       "fa": {"name": "فارسی",   "dir": "rtl"},
       ...
       "tr": {"name": "Türkçe",  "dir": "ltr"},   # ← new
   }
   ```

3. **Translate core pages first** (in priority order):
   `index` → `getting-started/quickstart` → `project/status` →
   `architecture/overview` → `reference/faq` → the rest.

4. **Mark every page** with front-matter:
   ```yaml
   lang: tr
   translation-status: partial   # complete when the whole source page is translated
   source-revision: en:index@<date-or-hash>
   ```

5. **Terminology**: add canonical translations for the project glossary to
   `site/terminology/<lang>.json` (the audit checks consistency). Product
   names stay untranslated.

6. **RTL?** If `dir: rtl`, the layout flips automatically; code blocks and
   technical identifiers stay LTR via the `[dir=rtl] code, [dir=rtl] pre`
   rules. Verify with the doctor's RTL checks (`check_docs.py --rtl`).

7. **Validate**:
   ```bash
   python scripts/docs/check_translations.py   # coverage %, missing, stale
   python scripts/docs/check_docs.py           # full doctor
   ```

8. **PR**: docs-only PR per the [contribution guide](contribution-guide.md);
   CI runs the same validation.

## Coverage reporting

`check_translations.py` prints a per-language coverage table from **actual
inspection** (English word-count parity per section), e.g.:

```text
DOCUMENTATION TRANSLATION AUDIT
en: 100%   fa: 100%   es: 100%   ar: 100%   de: 100%
```

Coverage numbers come from the audit — never hand-written.
