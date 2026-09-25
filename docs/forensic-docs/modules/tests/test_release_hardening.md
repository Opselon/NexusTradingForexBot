# tests/release/test_release_hardening.py

- **GUARDS / KEY ASSERTIONS / PITFALLS IT ENCODES / NOTES**
- Release-system hardening tests (spec 12-18, 44-45): run against REAL artifacts under `release/` when present; otherwise the SAME code paths on synthetic fixtures — suite stays deterministic in CI without a release build.
- Checksums: verify from release root AND from portable dir (`checksums["status"] == "PASS"`); missing file DETECTED (status != PASS).
- Manifest: tamper artifact detected; missing artifact detected; identity fields (sha256 64-char, relative_path starts with `portable/`).
- Architecture support matrix EXPLICIT (x64 supported, arm64 blocked by dependency stack).
- Secrets scan: flags real bot token / real API key / JWT; PASSES placeholders and normal source (no false positives).
- Verifier: fails on tampered release; fails on missing manifest (`"release-manifest.json missing" in detail`); identity mismatch fails.
- `test_real_release_artifacts_verify` (line 264): when a real `release/` root exists, the committed artifacts must actually verify (release gate).
- 19 defs / 270 lines.