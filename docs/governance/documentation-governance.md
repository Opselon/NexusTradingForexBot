---
title: Documentation Governance Contract
description: The one canonical documentation root, the classification rules, and the no-random-Markdown law.
lang: en
---

> Status: Active
> Owner: Nexus-Docs (documentation surface)
> Last reviewed: 2026-09-28
> Canonical: Yes

# Documentation Governance Contract

There is exactly **one canonical root for human-authored project
documentation** in this repository:

```text
docs/
```

## 1. The contract

1. One repository, one canonical documentation root, one documentation
   information architecture, one source of truth per topic.
2. All human-authored project documentation lives under `docs/` or one of its
   subdirectories.
3. `docs/README.md` is the canonical documentation map. Every category that
   holds documents appears there.
4. Duplicate sources of truth are prohibited. When two documents cover the
   same topic, one is authoritative and the other is merged, redirected or
   archived.
5. Historical material is preserved and labelled — never deleted to make the
   repository look clean.
6. Generated documentation must identify its source, its generator, its output
   location and its regeneration command.
7. No documentation migration is complete with broken links.

## 2. Classification — by consumer, not by extension

A `.md` file is **not automatically documentation**. Before moving, renaming
or deleting any document-like file, classify it by who consumes it:

| Classification | Test | Disposition |
| :--- | :--- | :--- |
| **Human documentation** | Read by people to understand or operate the project | Lives under `docs/` |
| **Machine-consumed repository artifact** | Parsed by scripts, read by CI, consumed by tests, used as synchronization state, embedded in production error messages, or required at a specific path | Stays at its path; that path is a contract |
| **Agent control-plane data** | Read/written by agent orchestration (`agents/`) | Stays under `agents/` |
| **Pinned repository interface** | A hard test or tool asserts the path | Stays at its path |
| **Generated artifact** | Produced by a generator from a source | Source is canonical; output is labelled |
| **Source-colocated documentation** | A `README.md` inside a package directory that the package's tooling expects | Stays colocated |

To determine the class of a file, search the whole repository for its exact
filename, its exact relative path, its string literal in scripts, tests,
workflows and production error strings. **A path referenced by executable code
or a hard test is a contract.** Do not break it for cosmetic organization.

## 3. Named exceptions

These are declared exceptions to "all documentation under `docs/`". Each is
machine-required; none is a filing omission.

- `agents/` — the multi-agent control plane. Consumed by CI scripts
  (`scripts/maint/compress_bug_ledger.py`,
  `scripts/ci/check_duplicate_task_rows.py`, `scripts/git/preflight.py`),
  test suites, agent orchestration, and production error strings. Moving it is
  an infrastructure migration, not a documentation reorganization.
- `CONTRACT.md` — pinned at repository root by a hard test.
- `README.md` — repository entry point (GitHub discoverability). Points into
  `docs/`; it is not a second documentation system.
- `site/` — generated/published documentation. `docs/` + `site/content/` are
  the sources; `scripts/docs/build_site.py` generates `site/_site/`. Never
  hand-edit generated output.
- `LICENSE`, `.github/` — repository infrastructure.

`docs/README.md` § Root-level files lists every document that lives directly
under `docs/` because tooling requires that exact path.

An agent may not invent a new exception. Adding one requires a recorded
decision in `docs/decisions/` naming the tooling that requires the path.

## 4. Agent creation rule — NO RANDOM MARKDOWN FILES

An agent MUST NOT create `README-new.md`, `notes.md`, `debug.md`,
`status.md`, `analysis.md`, `result.md`, `final.md`, `temp.md`, `report.md`,
`copy.md`, `copy2.md`, `new.md`, `new2.md`, `latest.md`, `old.md`,
`stuff.md`, `misc.md`, `draft.md`, or any other arbitrary `*.md` file in the
repository root or in an unrelated directory.

Before creating documentation:

1. **Search `docs/`** for the topic.
2. **Update** an existing document when one already covers it.
3. **Select the correct `docs/<category>/`** location from the map in
   `docs/README.md`.
4. **Use the naming convention** (§ 5 below).
5. **Update `docs/README.md`** when a new major document or category is
   introduced.
6. **Repair references** in the same change.
7. **Avoid duplicate sources of truth.**

For new machine-consumed agent state, create a file under `agents/` **only if
the consuming infrastructure explicitly requires it**. An agent must not
create an `agents/*.md` file merely as a place to store notes.

## 5. Filename governance

Every documentation filename must be:

- **deterministic** — the name reflects the content, not the author or moment
- **descriptive** — a reader can infer the topic from the path alone
- **stable** — renames require reference repair
- **searchable** — greppable
- **lowercase and hyphen-separated** where the repository's existing
  conventions permit (the frozen forensic and 70D archives keep their original
  uppercase names — renaming evidence breaks citations in the bug ledger and
  change-control records)
- **domain-specific**

Prefer:

```text
postgresql-runtime-configuration.md
```

Forbidden:

```text
new.md  new2.md  final.md  final2.md  latest.md  latest-final.md
temp.md  tmp.md  notes.md  misc.md  stuff.md  test.md  draft.md
old.md  copy.md  copy2.md
```

Do not introduce artificial numeric prefixes (`01-overview.md`,
`02-architecture.md`) unless ordering is semantically important.

## 6. Document metadata

For documents that carry active authority, use a consistent header:

```markdown
# Document Title

> Status: Active
> Owner: <domain/team>
> Last reviewed: YYYY-MM-DD
> Canonical: Yes
```

Do not add fake owners or dates. Only add metadata that can be truthfully
established.

## 7. Historical documentation

Never destroy engineering history. Historical material is classified and
labelled:

- `docs/historical/` — preserved engineering record (task/phase reports,
  retired root contracts)
- `docs/forensics/`, `docs/forensic-docs/`, `docs/forensic-reports/` —
  forensic evidence, read-only
- `docs/archive/` — deprecated / superseded

Every historical document must be identifiable as **not current operational
truth**. Statuses: `ACTIVE` · `HISTORICAL` · `DEPRECATED` · `SUPERSEDED` ·
`ARCHIVED` · `EVIDENCE`.

Historical documents must not be presented as current, and links into the
frozen forensic archives are exempt from the link doctor because their
targets are preserved evidence, not maintained prose.

## 8. Generated documentation

Generated files are not manually maintained canonical documentation. Where a
generator exists:

```text
source → generator → output
```

Document the source of truth, the generator, the output location and the
regeneration command. Do not hand-edit generated output unless the generator
contract explicitly requires it. In this repository:

| Source | Generator | Output |
| :--- | :--- | :--- |
| `docs/` + `site/content/` | `scripts/docs/build_site.py` | `site/_site/` (GitHub Pages) |
| test inventory | `scripts/testing/build_inventory.py` | `docs/testing/test_value_matrix.md` |
| test protection ledger | `scripts/testing/build_protection_ledger.py` | `docs/testing/critical_regressions.md` |

## 9. Link integrity

Moving documents without repairing references is a failure. After any move or
rename:

1. Search the whole repository for the old path: markdown links, relative
   links, anchors, TOCs, scripts, workflows, tests, production error strings,
   documentation indexes, site navigation.
2. Repair every reference in the same change.
3. Run the link audit:

```bash
python scripts/docs/check_docs.py
```

The doctor's link check covers `docs/` (excluding the frozen forensic
archives, whose links are historical) and `site/content/`.

## 10. Validation gates

```bash
python scripts/docs/check_docs.py          # links, anchors, translations, secrets, drift, build
python scripts/docs/check_translations.py  # coverage/staleness
ruff check scripts/docs/ && ruff format --check scripts/docs/
mypy scripts/docs/
```

`Validate documentation` is a required CI context on every push and PR.

---

```text
╔══════════════════════════════════════════════════════════════╗
║              NEXUS DOCUMENTATION CONTRACT                    ║
╠══════════════════════════════════════════════════════════════╣
║  ONE repository                                              ║
║  ONE canonical root for HUMAN documentation:  /docs          ║
║  ONE documentation information architecture                  ║
║  ONE source of truth per topic                               ║
║                                                              ║
║  Classification is by CONSUMER, not by file extension.       ║
║  A path referenced by executable code or a hard test         ║
║  is a CONTRACT. Do not break it for cosmetic organization.   ║
║                                                              ║
║  Agents MUST NOT create arbitrary *.md files.                ║
║  Before creating documentation:                              ║
║    1. Search existing docs                                   ║
║    2. Reuse/update an existing document when possible        ║
║    3. Select the correct category                             ║
║    4. Use a deterministic filename                            ║
║    5. Update the index if necessary                           ║
║    6. Repair references                                      ║
║                                                              ║
║  Root-level and machine-consumed files require an explicit    ║
║  recorded reason. An agent cannot invent a new exception.    ║
║                                                              ║
║  Duplicate sources of truth are prohibited.                  ║
║  Historical material must be preserved and labelled.         ║
║  Generated documentation must identify its source.           ║
║  No documentation migration is complete with broken links.   ║
╚══════════════════════════════════════════════════════════════╝
```
