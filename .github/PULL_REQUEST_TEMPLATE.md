## Documentation PR checklist (Nexus-Docs)

<!-- Integration boundary: every change to main arrives through a PR.
     Canonical workflow + branch/sync/merge rules: agents/git_governance.md -->

Docs-only changes are validated by the `docs.yml` workflow. Local check:

```bash
python scripts/docs/build_site.py
python scripts/docs/check_docs.py            # must print DOCS_HEALTH = PASS
python scripts/docs/check_translations.py
```

- [ ] English pages updated first (source of truth); translations marked `translation-status: partial|stale` if not yet updated
- [ ] No hard-coded version numbers (version comes from `pyproject.toml`)
- [ ] Status labels are evidence-graded (CERTIFIED / IMPLEMENTED / EXPERIMENTAL / PLANNED) — no unsupported claims
- [ ] No secrets, no private URLs, no machine-specific paths
- [ ] Product/module names untranslated; terminology matches `site/terminology/terms.csv`
- [ ] RTL pages (fa/ar) verified: content RTL, code/CLI/paths LTR
- [ ] Links/anchors resolve locally (doctor is the authority)
- [ ] No changes outside the docs surface (README, docs/, site/, scripts/docs/, docs workflow)

## General PR checklist (all PRs)

<!-- Required for every PR. Canonical model: agents/git_governance.md -->

- [ ] Task/issue identity given (TASK-ID / BUG-ID / CHANGE-ID / DEC-ID)
- [ ] Branched from `origin/main`, not local main (a tracking mirror)
- [ ] Preflight passed: `python scripts/git/preflight.py --for-push`
- [ ] Summary + affected areas + validation evidence + risk notes provided
- [ ] Local gate green (`beforePush.sh` / `beforePush.ps1`)
- [ ] No test, golden, or CI check weakened to get green
- [ ] No secrets committed; no destructive git used on foreign work
- [ ] Merge only after all required contexts are green AND the coordinator
      approval gate (DEC-0008) — green CI alone is not an authorization

