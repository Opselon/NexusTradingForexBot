# scripts/* (build + CI tooling)

- **PURPOSE:** Build/CI helper scripts (7 .py under scripts/): the
  release build helpers (scripts/build/update_helpers.py — token-guard /
  scan-tree / manifest / sbom actions, ps1-safe by design: never inline
  multi-line python with quotes in .ps1), CI results makers
  (scripts/ci/make_ci_results.py — per-check status JSON consumed by the
  aggregate job), CI telegram notifier (scripts/ci/telegram_notify.py —
  exit 0 ALWAYS so CI never fails on Telegram; HTML escaping; NEXUS-CI
  correlation; sendDocument bundles).
- **ARCHITECTURE LAYER:** Build/CI tooling (outside the runtime package).
- **RESPONSIBILITY:** keep the CI workflows (ci.yml, release.yml)
  thin orchestrators and the logic in testable python.
- **DEPENDENCIES:** stdlib + httpx (telegram), json/yaml.
- **CONNECTS TO:** .github/workflows, release pipeline, CI reporting
  dashboards, tests (test_release_build_system, test_ci_telegram_reporter).
- **KEY CONCEPTS:** token-guard prevents secret leaks in build artifacts;
  the CI results JSON files are the aggregate job's source of truth
  (ci-results/run-info/*.json); telegram_notify always exits 0 (notification
  failure must never fail the build — observability, not gatekeeping).
- **EDGE CASES & PITFALLS:** ps1-invoked scripts must be BOM-free/encoding
  safe (BUG-093: UTF-8 BOM breaks json.loads in build-info.json); the
  scripts must be idempotent (CI re-runs).