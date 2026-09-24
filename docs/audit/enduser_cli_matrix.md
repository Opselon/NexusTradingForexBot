# COMMAND / CLI MATRIX — ENDUSER-OPERABILITY-HARDENING

Executed against the real entry point (subprocess, not in-process). "Works?" is
backed by an exit code observed on this machine.

| Command | Purpose | Works? | User Clarity | Exit Code | Recovery | Automation | Issues | Status |
|---|---|---|---|---|---|---|---|---|
| `nexus --help` | discover the product | yes | lists every user command incl. dashboard/status/doctor | 0 | — | parseable | — | OK |
| `nexus status` | full status: install + RUNNING product | yes | one panel: state, why, trading safety, dashboard | 0 | points at dashboard when not running | `--json` | — | UPGRADED (EU-09) |
| `nexus status --json` | machine-readable | yes | canonical axes now at payload ROOT | 0 | next_action field | `--json` | — | FIXED |
| `nexus dashboard` | "where is my program?" | yes | prints the address + product state; opens browser if interactive | 0 running / non-0 not running | prints the exact next action | `--json` | NEW | NEW (EU-03) |
| `nexus dashboard --url <bogus>` | explicit address | yes | tells the user what a valid URL looks like | 2 | — | deterministic | — | NEW |
| `nexus dashboard --json` | machine-readable | yes | reachable / http_status / opened / next_action | honest | next_action always set | `--json` | — | NEW |
| `nexus doctor` | installation health | yes | PASS/WARN/FAIL + suggestion per check | 0 | suggestion per check | — | cannot say if the product is RUNNING (by design — see status) | OK |
| `nexus health` | installation health (alias) | yes | as doctor | 0 | as doctor | `--json` | — | OK |
| `nexus logs` | tail engine logs | yes | reports the REAL log root now | 0 | tells the user to start the engine once | `--json` NEW | — | FIXED (EU-05) |
| `nexus logs --json` | machine-readable | yes | log_root / log_files / latest / lines | 0 | next_action when empty | `--json` | — | NEW |
| `nexus logs --export <zip>` | export bundle | yes | zip path | 0 | — | scriptable | not a full support bundle (EU-08, documented) | OK |
| `nexus config --validate` | validate config | yes | per-field errors | 0/usage | actionable errors | scriptable | — | OK |
| `nexus db status` | DB state | yes | schema vs expected | 0 | — | `--json` | false `DB_MIGRATION_PENDING` when EU-01 shipped a DB — fixed at the source | FIXED |
| `nexus --version` | version | yes | stamped | 0 | — | `--json` | prior gap (F1) already fixed by the earlier lane | OK |
| `nexus setup` | first-run wizard | yes | guided | 0 | — | interactive | must be auto-started (EU-10, documented) | DOCUMENTED |
| `nexus update check` | update check | yes | honest when offline | 0 | — | `--json` | — | OK |
| `nexus <unknown>` | failure mode | yes | usage error | non-0 | — | deterministic | — | OK |

Exit-code contract in force (release/exit_codes.py): `EXIT_USAGE`, `EXIT_RUNTIME`,
etc. — `nexus dashboard` honours it: 0 answering, `EXIT_RUNTIME` not answering,
`EXIT_USAGE` for a bad `--url`.
