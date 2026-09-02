# NSE Documentation Registry — Nexus-Docs

> Owned per the MASTER MULTI-AGENT CONTRACT (§3 role ownership). This registry
> tracks documentation-surface tasks performed by the **Nexus-Docs** agent
> (Documentation / GitHub Experience / Project Presentation Engineer).
>
> **Ownership boundary (hard):** README.md, docs/ (new IA tree), the GitHub
> Pages site, documentation CI, and documentation-specific validation tooling.
> Nexus-Docs does NOT modify trading logic, strategy logic, model logic,
> execution, risk, database internals, installer internals, replay, shadow,
> provider gate, research engine, or any subsystem owned by another agent.
>
> If documentation exposes a suspected implementation defect, it is recorded in
> **§Out-of-scope defects observed** below with subsystem attribution — the
> implementation itself is left untouched for the owning agent.

## Documentation tasks

```text
DOC-TASK-ID: DOCS-001
Agent: Nexus-Docs
Role: Documentation / GitHub Experience / Project Presentation Engineer
Task: TASK-DOCS-PLATFORM — GitHub project experience & documentation platform
Scope: Evidence-based README landing rebuild · GitHub Pages site (static,
       no heavy framework) · multilingual documentation (EN source + FA/ES/AR/DE
       with full RTL support for FA/AR) · docs validation tooling
       (links / anchors / translations / secrets / drift) · documentation CI
       workflow (docs.yml) · roadmap, capability matrix, glossary, FAQ,
       contribution docs, project status page · release/documentation workflow
Affected files: README.md, docs/** (new IA tree), site/** (Pages source +
       generated), scripts/docs/**, .github/workflows/docs.yml,
       .github/ISSUE_TEMPLATE/**, .github/PULL_REQUEST_TEMPLATE.md
Affected functions/classes: NONE in src/ (zero code changes; docs-only surface)
Contracts touched: none (documentation only)
Runtime paths touched: NONE
Owners affected: none (docs surface is unowned before this task)
Risk: NONE for runtime; LOW for repository presentation
Dependencies: none (evidence gathered from existing repository state)
Required checks: docs doctor (links/anchors/translations/secrets/drift/build)
Status: IN_PROGRESS
```

## Translation status model

Source language is **English**. Every translated page carries a
`translation-status` HTML comment / front-matter key:

| Status  | Meaning                                          |
| :------ | :----------------------------------------------- |
| complete | Full translation of the English source page      |
| partial  | Some sections translated; rest linked to English |
| stale    | English source changed after this translation    |

`scripts/docs/check_translations.py` audits coverage, staleness, broken
navigation and terminology consistency; `scripts/docs/check_docs.py` is the
overall doctor. CI (`.github/workflows/docs.yml`) runs both.

## DOC-TASK-ID: DOCS-002 — Pages v2 rebuild (user brief 2026-09-02, phase 2)

```text
DOC-TASK-ID: DOCS-002
Agent: Nexus-Docs
Task: Fetch remote state, reproduce live defects, rebuild the Pages experience
Scope: LIVE-VERIFIED defects fixed:
  D1 root-absolute asset paths (/assets/...) 404 under Pages subpath ->
     depth-relative URLs via rel_base() (single canonical base-path mechanism)
  D2 search dead (window.NEXUS_SEARCH never embedded) -> search.js fetches
     search-index.json relative to page; keyboard + focus + Escape handling
  D3 no mobile nav -> hamburger + body.nav-open wired (JS enhancement only;
     content readable without JS)
  D4 root page was a raw markdown dump -> generated homepage (hero, pillars,
     capability highlights, What's New, version timeline); hub at /docs-hub/
  D5 language switch lost page context -> keeps current page when a
     translation exists, else language landing (flagged)
  D6 no prev/next -> section pagination + titled breadcrumbs
  D7 release awareness -> fetch_releases.py + What's New + /releases/ page;
     version + git revision injected from pyproject/git (site-meta.json);
     docs.yml triggers on release published/edited + v* tags + pyproject
  D8 depth pass: vision (tick-to-decision narrative), model-pipeline,
     observability, methodology, new guides/api.md (203 /api endpoints
     counted from real route modules)
Validation: DOCS_HEALTH PASS (11 checks incl. built-site structure +
  base-path discipline), TRANSLATION AUDIT PASS, RELEASE SIMULATION PASS
  (version bump propagates to site-meta/homepage/footer; pyproject restored),
  DOCS_LIVE_SMOKE PASS against the fresh deployment (36 checks), Docs
  workflow green on 4013f91, live site-meta revision == deployed commit.
Status: VERIFIED against live deployment
```



## DOC-TASK-ID: DOCS-003 — Live 404 page-link fix (user report: "every link returns not found")

```text
DOC-TASK-ID: DOCS-003
Agent: Nexus-Docs
Task: Fix every internal page link 404ing on the live site; generate ALL pages
      for ALL languages; upgrade UI/UX
Root cause (live-verified by crawling the deployed site): page links were
      built root-absolute ('/architecture/...') which resolve OUTSIDE the
      /NexusTradingForexBot/ subpath -> 49/49 internal homepage links 404.
      Same defect class as v2's asset fix, but for page URLs.
Fix (v3 builder, 75c4de3 + a77a04e):
  - LINK LAW: single page_href(target, lang, from_rel, from_lang) helper;
    every internal link on every page is depth-relative (the source page's
    /<lang>/ prefix counts toward its depth). Root-absolute internal links
    are structurally impossible now.
  - FULL TREES: every language builds EVERY page (334 pages total; missing
    translations use the English source with an explicit notice) + real
    section landing pages for every sidebar section.
  - EN skips a separate /docs-hub/ (hub content is the root homepage);
    fa/es/ar/de keep /<lang>/docs-hub/.
  - UI/UX upgrade: section landing pages, homepage section grid, code copy
    buttons, richer section cards.
Validation:
  - BUILT_LINK_AUDIT: 22,411 internal links across 335 built pages -> PASS
    (wired into DOCS_HEALTH; one dead link fails the doctor)
  - 75-page live sweep: 74/75 200; the single failure was the unlinked EN
    /docs-hub/ path (fixed; sitemap + search index contain only the 4 valid
    localized /<lang>/docs-hub/ URLs)
  - USER_CLICK_SWEEP on the live deployment: 532 unique user-clickable links
    across 9 representative pages (EN/FA/AR/ES/DE) -> 0 failures, PASS
  - DOCS_LIVE_SMOKE = PASS (36 checks); Docs workflow green; live
    site-meta revision == pushed HEAD
Status: VERIFIED against live deployment
```



## DOC-TASK-ID: DOCS-004 — P0 localization repair + P1/P2 UI system (user steer 2026-09-02)

- P0 root cause: UI chrome was partially hard-coded English regardless of
  locale (nav page names = raw slugs; hero/pillars/capability rows/section
  intros/404/releases intro/callout titles = EN literals).
- P0 fix (translation contract): site/locales/<lang>/ui.json = SINGLE source
  of truth (en/fa/ar/es/de), key parity verified (0 missing keys); builder
  resolves EVERY generated user-visible string via t(lang, key); t() fallback:
  lang value -> en value -> prettified key. New full FA/AR translations:
  getting-started/first-run + configuration.
- Gates: check_localization.py = LOCALIZATION_GATE (FA/AR chrome assertions +
  English-leak detector with technical-English allowlist), wired into
  DOCS_HEALTH as a deploy-blocking gate. coverage_report.py = machine-readable
  coverage matrix (fa/ar 13 translated / 45 fallback; es/de 11/47; 0 missing
  keys; RTL status).
- P1: header theme picker (light/dark/system; localStorage persisted; no-FOUC
  boot before paint; localized labels); NEXUS_LOCALE runtime bundle per page;
  copy-code with localized feedback (FA/AR/EN) + aria-live; search no-results
  localized.
- P2: data-theme token overrides; hero grid + radial glow layers (hover-only
  depth, no layout shift); card lift; reduced-motion disables decorative
  layers.
- DELIVERY FORENSIC: live site went stale after the P0 commit because the
  Docs run failed at the mypy step (record() arg-type — my error). Deploy is
  gated on CI health by design; fixed (6b16928..a2dc1cb) and deploy followed.
- Validation: LOCALIZATION_GATE PASS / DOCS_HEALTH PASS (12 checks) /
  BUILT_LINK_AUDIT PASS / ruff+mypy clean / node --check search.js OK /
  LIVE: /fa/ hero+nav+theme picker+locale bundle verified (rev == HEAD).
- Commits: 02d5855 (P0) · a2dc1cb (mypy fix + rebuild) · 4830d72 (locale
  bundle) · c1f8bee (P1/P2) · this record.
- Status: VERIFIED against live deployment.




## Out-of-scope defects observed (recorded only — owning agent notified via this registry)

1. OBS-DOCS-001 (2026-09-02): project CI (ci.yml, "Code Quality & Tests") failing on
   main since at least 297a4e7 — ruff lint/format failures + mypy errors in
   src/nexus_scalp/release/health.py (L112 dict-item, L701 name-defined
   NOT_APPLICABLE) and src/nexus_scalp/release/release_status.py (L137 name-defined re)
   + pytest rc=1 (make_ci_results.py TypeError: Element vs str in junit parse).
   Owning subsystem: src/nexus_scalp/release/ + scripts/ci/make_ci_results.py —
   active CHG-0043/CHG-0046 workers (Hermes-Main / Nexus-Main). NOT touched by
   Nexus-Docs (zero src/ changes verified per commit).
2. OBS-DOCS-002 (2026-09-02): repository had GitHub Pages disabled — docs.yml deploy
   failed with "Get Pages site failed ... Not Found". Resolved by enabling Pages
   (build_type=workflow) via REST API by Nexus-Docs (this is a docs-platform
   responsibility). Docs workflow green as of run 33578596716 attempt 2.


## Ownership note (2026-09-02, user-directed commit/push check)

- ALL Nexus-Docs owned surface (scripts/docs/, site/assets/styles.css,
  site/assets/search.js, site/content/, docs/ IA tree, .github/workflows/docs.yml,
  README.md) is COMMITTED and PUSHED — verified file-by-file against origin/main
  (git cat-file -e) and HEAD == origin/main.
- UNTRACKED and NOT MINE (disclosed, untouched): site/assets/css/site.css and
  site/assets/js/site.js — authored by the Nexus-UX agent (CHG-0048 surface),
  present as untracked WIP inside site/assets/. Per the no-silent-absorption
  contract these are left for their owner to commit/push.
- Remaining scratch/ + doc1.txt/end_limit/pyvenv.cfg untracked files belong to
  other agents/locals — not docs surface, untouched.
