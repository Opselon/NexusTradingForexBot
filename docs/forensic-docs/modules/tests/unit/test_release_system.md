# tests/unit/test_release_system.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- Release-system behavioral tests (spec 55 & 66) at the source level; packaged-EXE smoke tests live in the release pipeline, not here.
- Version: canonical version parses AND matches pyproject; version-info shape.
- Manifest/checksums: roundtrip; corruption detected; 64-char sha256.
- Architecture: detection reports supported/unsupported; arm64 EXPLICITLY unsupported by the dependency stack (`test_arm64_explicitly_unsupported_by_dependency_stack`).
- Health: all categories returned; verdicts in (PASS, WARNING, FAIL, UNKNOWN); NEVER raises on missing database.
- Repair: creates user dirs and NEVER deletes (user-data marker file untouched after repair).
- Update plan: refuses newer release without digest; refuses prerelease on stable channel; no-newer-release path; artifact name/hash locked; arm64 refused with explicit message.
- Config: default config is never LIVE in repair template; `release verify` flags live default config.
- CLI: version/help; health JSON parseable; doctor JSON never raises; exit-codes contract; diagnostics export sanitized.
- 24 defs / 363 lines.