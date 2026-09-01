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

## Out-of-scope defects observed (recorded only — owning agent notified via this registry)

*(none at this time)*
