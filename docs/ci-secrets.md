# CI Secrets

> Authoritative documentation of the GitHub Actions secrets used by this
> repository. **Only names and purpose are documented here — values are
> never stored in the repository, workflows, artifacts, or logs.**

## Where maintainers configure secrets

GitHub-standard locations:

| Scope | Location |
|-------|----------|
| Repository (all workflows) | **Settings > Secrets and variables > Actions > Secrets** |
| Environments (future env-specific deploys) | **Settings > Environments > <environment> > Secrets** |
| Organization (shared across org repos) | **Organization settings > Secrets and variables > Actions > Secrets** |

Secrets are injected into workflows only via `${{ secrets.NAME }}` during a
run; they are never visible in the repository itself. Never commit `.env`
files or any other credential storage into this repository.

## Secrets used by workflows

| Secret | Used By | Purpose | Required | Scope |
|--------|---------|---------|----------|-------|
| `CODECOV_TOKEN` | `.github/workflows/ci.yml` (codecov upload step) | Upload coverage.xml to Codecov; presence-checked (never printed) | No (upload skipped when absent; `fail_ci_if_error: false`) | Repository |
| `GITHUB_TOKEN` | `.github/workflows/docker.yml` (GHCR login) | Authenticate to ghcr.io for image push | Yes for docker.yml push | Repository (auto-provided) |
| `TELEGRAM_BOT_TOKEN` | `.github/workflows/ci.yml`, `release.yml` (Telegram CI/CD observability) | Bot token for the CI/release Telegram feed (`telegram_notify.py`); presence-checked, never printed | No for CI (notifications skip when absent) | Repository |
| `TELEGRAM_CHAT_ID` | `.github/workflows/ci.yml`, `release.yml` (Telegram CI/CD observability) | Destination chat/group id for CI notifications; falls back to `USER_ID` | No for CI | Repository |
| `USER_ID` | `.github/workflows/*.yml` (Telegram CI/CD observability) | Telegram destination chat id (numeric); used as fallback when `TELEGRAM_CHAT_ID` is absent | No for CI | Repository |
| `NEXUS_TELEGRAM_BOT_TOKEN` | runtime / Telegram reports; presence-checked in CI | Telegram bot token for project notifications | No for CI (bounded report feature) | Repository (runtime env) |
| `NEXUS_TELEGRAM_ADMIN_ID` | runtime / Telegram reports; presence-checked in CI | Admin chat id for Telegram reports | No for CI | Repository (runtime env) |

> `NEXUS_TELEGRAM_*` are read by the application at runtime (settings service),
> not by workflows; CI only records whether they are configured
> (`SECRET_PRESENT=true/false`, values never printed or written to artifacts).

## Secret presence checks (CI)

The CI pipeline checks **only presence** (boolean) of `CODECOV_TOKEN`,
`NEXUS_TELEGRAM_BOT_TOKEN` and `NEXUS_TELEGRAM_ADMIN_ID` and records the
result in `ci-results/run-info/secrets-present.json`:

```
SECRET_PRESENT=true
SECRET_PRESENT=false
```

Values are never printed, dumped, or written into artifacts.

## Leak-safety rules (enforced by CI hygiene)

- No `printenv` / `set` / `env` dumps in any workflow (audited 2026-08-19).
- Artifacts (`ci-results/`) contain only tool outputs, summaries, manifests
  and checksums — never environment dumps or credentials; `.env`, private
  keys and auth caches are excluded from upload paths by construction.
- `release.yml` runs a secret-shaped string scan on the tree before building
  and fails the release if any are found.
