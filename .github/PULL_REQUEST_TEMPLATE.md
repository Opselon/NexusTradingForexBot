## Documentation PR checklist (Nexus-Docs)

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
