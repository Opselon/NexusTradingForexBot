---
title: CI Architecture
description: The GitHub Actions surface — what runs when, the results system, and trigger policy.
lang: en
---

# CI Architecture

## Workflows

| Workflow | Trigger | Purpose |
| :--- | :--- | :--- |
| `ci.yml` | push (main/develop/ci-tests), PR, weekly drift scan | ruff · format · mypy · pytest (critical; matrix arms for integration/e2e/research/model on demand) |
| `tests-os.yml` | matrix OS runs | cross-platform unit sanity |
| `security.yml` | PR + weekly schedule | CodeQL + Trivy |
| `osv-scanner.yml` | schedule | dependency vulnerabilities |
| `lockfile-diff.yml` | dependency file changes | lockfile drift |
| `js-tests.yml` | Web/ changes | `node --check` + JS tests |
| `release.yml` | `v*` tags **only** | build + verify + publish release artifacts |
| `docker.yml` | Docker surface changes | image build gates |
| `docs.yml` | docs/site/README changes | documentation validation + Pages deploy (this platform) |

Trigger policy (CI_TRIGGER_POLICY v1): tags never fire CI (release-only), main
pushes run the fast quality gate, heavy matrix arms are opt-in — no 3×
concurrent run flood.

## Results system (CI_RESULTS v1)

One canonical per-run tree (`ci-results/`): run metadata, per-check status JSON,
ruff/format/mypy/pytest outputs (junit.xml, coverage.xml, htmlcov), manifest +
SHA256SUMS, aggregated into a single artifact (30-day retention) and a
GitHub Step Summary. A failure-preserving final gate makes every failure
visible in one place.

## Telegram observability

CI start/finish events post to Telegram (HTML format, secret-redacted,
correlation id `NEXUS-CI-<run>-<sha4>`); the notify step **always exits 0** —
CI never fails because Telegram did.

## Documentation CI (docs.yml)

Docs changes run: markdown lint, link/anchor validation, translation audit,
secret scan, site build, then deploy to GitHub Pages. See
[docs workflow](../contributing/documentation.md).
