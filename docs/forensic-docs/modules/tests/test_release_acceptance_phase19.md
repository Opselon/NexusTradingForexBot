# tests/unit/test_release_* (acceptance, build_system, manifest, migration_0007, model_artifacts, versioning)

# test_release_acceptance_phase19.py
- **GUARDS:** Release acceptance (PHASE 19) — end-to-end release
  pipeline behaviors: version metadata, build info JSON (BOM-free —
  BUG-093), frozen-sim pattern (metadata.get_build_info_file resolves
  _internal/build-info.json), installer layout.
- **KEY ASSERTIONS:** packaged EXE reports the real version (never a stale
  build-info); user data survives install/upgrade (BUG-091: installer
  preserves app-tree artifacts/data/logs; rollback NEVER restores old
  user data over migrated).

# test_release_build_system.py
- **GUARDS:** build script machinery (build_release.ps1 + update_helpers.py).
- **KEY ASSERTIONS:** ps1 parseability (no inline multi-line python with
  quotes); token-guard prevents secret leaks; manifest/sbom actions
  produce valid artifacts; UTF-8 BOM discipline on JSON outputs.

# test_release_manifest_phase19.py
- **GUARDS:** the release-manifest.json embedded in the portable tree.
- **KEY ASSERTIONS:** manifest schema + hash verification; `nexus update`
  verifies the manifest inside the payload.

# test_release_migration_0007_phase19.py
- **GUARDS:** the app-settings DB migration path (migrations framework).
- **KEY ASSERTIONS:** idempotent, transactional, version-aware migration;
  a crash mid-migration leaves the source intact (recovery re-run).

# test_release_model_artifacts_phase19.py
- **GUARDS:** release/model_artifacts — model artifact versioning in the
  release tree.
- **KEY ASSERTIONS:** model artifacts carry version/schema/hash metadata;
  model_artifacts resolution under frozen root (exe_dir) works.

# test_release_versioning_phase19.py
- **GUARDS:** release/versioning — version parsing/comparison.
- **KEY ASSERTIONS:** semver compare (compare_versions) edge cases
  (pre-release, build metadata); the version contract consumed by
  UpdateDiscovery.