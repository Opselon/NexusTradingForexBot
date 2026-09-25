# tests/unit/test_web_chart_forming_bar_bug082.py + tests/integration/test_diagnostics_api.py + tests/release/test_build_script_hardening.py

# test_web_chart_forming_bar_bug082.py
- **GUARDS:** BUG-082 web chart forming-bar handling — the visualizer
  must render the forming bar distinctly and never duplicate it.
- **KEY ASSERTIONS:** /api/chart/history includes the forming bar with
  is_complete=false only ONCE; the 900-bar window contract; resync after
  downtime (BUG-058: history REPLACE + ALIGN, no duplicated completed
  bar at the same timestamp).

# tests/integration/test_diagnostics_api.py
- **GUARDS:** /api/diagnostics/* surface (incidents, lineage, forensics,
  health, search, trace, reports, zip).
- **KEY ASSERTIONS:** endpoints return the safe envelope (X-Request-ID);
  incident correlation joins work (EXEC-... keys); zip export produces a
  valid archive; error paths never leak stack traces.

# tests/release/test_build_script_hardening.py
- **GUARDS:** Release build-script hardening (the .ps1 unparseable-class
  bugs: apostrophes in inline python, UTF-8 BOM, GetRelativePath on
  .NET Framework).
- **KEY ASSERTIONS:** build_release.ps1 re-parses clean (Parser.ParseFile);
  update_helpers.py actions run (token-guard/scan-tree/manifest/sbom);
  build-info.json written BOM-free (BUG-093) and json.loads() clean;
  no inline multi-line python with quotes inside .ps1 strings.