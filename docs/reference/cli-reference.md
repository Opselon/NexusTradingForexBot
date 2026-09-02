---
title: CLI Reference
description: Machine-oriented reference of the nexus CLI surface — commands, flags, exit codes.
lang: en
---

# CLI Reference

The authoritative surface is always `nexus help` / `nexus <command> --help`
(this page documents the verified contract; the CLI is golden-tested by 66
end-to-end tests).

## Global flags

| Flag | Effect |
| :--- | :--- |
| `--json` | machine-readable output (parity-tested keys) |
| `--plain` | no color, no formatting |
| `--no-color` | disable color only |

## Command groups

| Command | Purpose | Notes |
| :--- | :--- | :--- |
| `nexus start [--mode paper\|shadow\|live] [--port N] [--daemon]` | run engine | paper default; live needs interactive `--yes`/confirmation; port default 8080 |
| `nexus stop` / `nexus restart` | daemon control | pidfile-based; dead-pid reported honestly (BUG-172) |
| `nexus doctor` | 19-category diagnostics + suggested fixes | `--json` for tooling |
| `nexus status` / `nexus health` | environment + health | READY / DEGRADED / NOT READY |
| `nexus config [--validate path] [--show]` | configuration inspection | secrets masked |
| `nexus logs [--tail N] [--errors]` | log access | severity-split store |
| `nexus test --mode quick\|unit\|integration` | run suites | never runs live-broker tests |
| `nexus update <sub>` | update lifecycle | `check\|latest\|download\|install\|verify\|status\|history\|rollback\|doctor` |
| `nexus release info` | installed release metadata | release identity lock |
| `nexus repair` | non-destructive derived-state repair | never deletes user data |
| `nexus db <sub>` | `migrate`, `hygiene status\|plan\|run\|pause\|resume\|history` | hygiene AUDIT_ONLY default |
| `nexus incidents <sub>` | incident center | list / reports / export (sanitized) |
| `nexus forensic [---deploy-gate]` | forensic health engine | deploy gate wired into beforePush |
| `nexus model-dataset-build` | build canonical dataset | fingerprinted, immutable |
| `nexus model-experiment-create` | create experiment | fair A/B/C protocol |
| `nexus model-train` | train candidate | deterministic seeds |
| `nexus model-validate` | validate candidate | walk-forward + OOS + robustness |
| `nexus model-replay` | replay parity | bit-exact proof |
| `nexus setup` / `nexus install` | first-run wizard | compat → mode → symbol → health |
| `nexus export-diagnostics` | sanitized diagnostics ZIP | never contains secrets |
| `nexus uninstall [--no-keep-data]` | remove installation | user data preserved by default |

## Exit codes

`0` success · `1` runtime/validation failure · `2` invalid usage ·
`3` environment blocked · `4` release verification failure · `5` update not
applicable/failed.

## Legacy entrypoints

`python NexusTradingForexBot.py --doctor | --config configs/live.yaml |
--symbol EURUSD | --gateway` — the legacy launcher (still supported;
`main.py` redirects). Prefer the `nexus` surface.
