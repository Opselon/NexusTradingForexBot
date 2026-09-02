---
title: CLI Guide
description: The nexus command — every operational surface with honest semantics.
lang: en
---

# CLI Guide

`nexus` is the application command (installation lifecycle lives in
`install.ps1`; everything you *operate* lives in `nexus`). From source:
`python -m nexus_scalp.cli.main` (alias `nse`).

## Commands

```text
nexus help            # same authoritative surface as --help (+ 'nexus help start')
nexus version         # build identity (--json / --plain)
nexus doctor          # read-only diagnostics (19 categories) + suggested fixes
nexus status          # health + environment + version
nexus health          # quick health summary (READY / DEGRADED / NOT READY)
nexus config          # inspect/validate active configuration (secrets masked)
nexus start           # PAPER mode by default — never LIVE silently
nexus stop / restart  # control the background engine (honest dead-pid reporting)
nexus logs [--tail N] [--errors]
nexus test --mode quick|unit|integration
nexus update check|latest|download|install|verify|status|history|rollback|doctor
nexus release info    # installed release metadata
nexus repair          # non-destructive derived-state repair (never deletes data)
nexus export-diagnostics   # sanitized ZIP (never contains secrets)
nexus db hygiene status|plan|run|pause|resume|history
nexus db migrate ...  # schema migrations (same engine as startup gate)
nexus incidents ...   # incident center: list, reports, export
nexus forensic ...    # forensic health + --deploy-gate
nexus model-dataset-build / model-experiment-create / model-train / model-validate / model-replay
nexus uninstall       # user data preserved unless --no-keep-data
```

## Exit codes (stable contract)

| Code | Meaning |
| :--- | :--- |
| `0` | success |
| `1` | runtime/validation failure |
| `2` | invalid usage |
| `3` | environment blocked (e.g. ARM64) |
| `4` | release verification failure |
| `5` | update not applicable / failed |

`--json` / `--plain` / `--no-color` are available for CI and automation; JSON
output keys are parity-tested. Full contract: `docs/CLI.md` in the repository
and [CLI reference](../reference/cli-reference.md).
