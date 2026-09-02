# Web/styles.css + tailwind.css + tailwind_input.css

- **PURPOSE:** Styling: styles.css = premium dark glassmorphism custom
  CSS (77 lines); tailwind.css = compiled Tailwind (locally built, no
  CDN); tailwind_input.css = the tiny Tailwind source input.
- **ARCHITECTURE LAYER:** Web UI (style).
- **RESPONSIBILITY:** visual layer only.
- **DEPENDENCIES:** built at release time.
- **CONNECTS TO:** index.html.
- **KEY CONCEPTS:** local/vendored assets keep the UI offline and
  deterministic (a release-time build concern — never runtime CDN).
- **EDGE CASES & PITFALLS:** regenerating tailwind.css requires the
  build step, keep the input minimal.