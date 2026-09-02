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
