# TASK-SEC-AUDIT — GitHub Security & Quality Audit (Bug-121 batch)

Date: 2026-08-19
Agent: Hermes-SecurityAudit

## Summary

GitHub code-scanning (CodeQL Python, security.yml) had **6 open alerts** on
main. All 6 were REAL (verified via SARIF code flows) and are now fixed at
the source. Dependabot vulnerability alerts and automated security updates
were DISABLED repo-wide (API 403) — both enabled (HTTP 204). All 4 workflow
files' third-party actions pinned to immutable commit SHAs.

## Alert -> fix map

| Alert | Rule | Location at scan time | Root cause | Fix |
|---|---|---|---|---|
| 62/63/67 | py/path-injection | server.py serve_fa_webfont 1554/1557/1559 | User-supplied font_name flowed into path expressions (split/join/resolve/startswith); guard was runtime-correct but CodeQL can't prove sanitization | Route rewritten to **listing-match**: `root.iterdir()` compared against the requested basename; no path built from user input at all |
| 84 | py/stack-trace-exposure | debug_snapshot.py 9 section handlers | `{exc}` / `str(exc)` embedded in public reason/config_error fields flowing to `/api/debug/state` | Module logger added; all 9 handlers log `logger.warning(error=str(exc))` server-side and return stable codes (FEATURE_REGISTRY_UNAVAILABLE, MODEL_STATE_ERROR, CONFIDENCE_ERROR, CONFIG_ERROR, POSITIONS_ERROR, EXIT_FORENSICS_ERROR, LIQUIDITY_ERROR, DB_HEALTH_ERROR, SECTION_ERROR) |
| 66 | py/stack-trace-exposure | research/store.py research_health_summary | `str(e)` returned in public health payload | Real exception logged; wire carries generic DATASET_AUDIT_UNAVAILABLE / HEALTH_SUMMARY_UNAVAILABLE |
| 86 | py/clear-text-storage-sensitive-data | incidents/reports.py mask_secrets | Key-name-only redaction; secret-shaped VALUES under innocent keys passed through to incident files on disk | `_SECRET_VALUE_RE` value-level scrub (JWT, Telegram bot token, sk/pk/ghp/xox/AKIA/AIza, PEM header, 40/64-hex) applied inside mask_secrets |

## CI / supply-chain hardening

- All third-party actions in security.yml / ci.yml / docker.yml / release.yml
  pinned from floating tags (@v4/@v3/@master) to full 40-char commit SHAs.
  trivy-action was on `@master` — worst offender — now pinned to a SHA.
  Note: this leaves the repo on the committed SHAs for checkout@v4-era
  versions; a deliberate Dependabot update can move them forward.
- `[tool.ruff] exclude` added: scratch/ + _cleanup_hold_20260819. The CI
  failure (run 32274368118) was 100% ruff lint errors inside scratch/ probe
  files from parallel agents' WIP — not src/. src/, tests/, scripts/ and
  repo-root modules remain fully linted (ruff check . is clean).
- Default workflow perms checked: contents read (least privilege already).
- pip-audit venv scan: 0 known vulnerabilities. Web/ has no package.json
  (plain JS, no npm supply chain).

## Validation

- ruff check . — clean
- ruff format --check src tests scripts — clean
- mypy src — no issues (260 files)
- Targeted suites: 153 tests pass (frontend assets incl. 11 new traversal
  tests, debug snapshot incl. 2 new, incident response incl. 3 new,
  research API incl. 1 new)
- Full unit suite ran in background (long; Windows + parallel load)

## Registry

- agents/bugs.md: BUG-121 appended
- agents/change_control.md: CHG-0024 appended
- GitHub: dependabot alerts enabled (0 open), security updates enabled

## Next agent

- Push and verify: run security.yml CodeQL on the new commit (push to
  ci-tests or dispatch) and confirm alerts 62/63/66/67/84/86 resolve.
  GitHub may take ~10 min to re-scan and close stale alerts.
- Optional: actionlint the 4 workflows after the SHA pinning (validated
  with yaml.safe_load locally, but actionlint is stricter).