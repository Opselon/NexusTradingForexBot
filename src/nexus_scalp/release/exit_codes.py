"""CLI exit-code contract for the Nexus release CLI (documented in docs/RELEASE.md).

Stable semantics (do not arbitrarily re-purpose):

    0  SUCCESS                 command completed successfully
    1  RUNTIME_OR_VALIDATION   runtime/validation failure (config invalid,
                               health NOT READY where gating, test failures)
    2  USAGE                   invalid CLI usage (bad option / bad mode value)
    3  ENVIRONMENT_BLOCKED     environment not supported (ARM64, missing
                               platform prerequisite) — never install blindly
    4  RELEASE_VERIFICATION    release verification failed (tamper, checksum
                               mismatch, secret found, missing artifact)

Typer uses `typer.BadParameter` → its own exit code 2 for usage errors, which
matches rule 2. The remaining codes are applied by the CLI commands.
"""

EXIT_OK = 0
EXIT_RUNTIME = 1
EXIT_USAGE = 2
EXIT_ENVIRONMENT = 3
EXIT_RELEASE = 4
#: TASK-9 extension: update not applicable / failed / needs user action.
#: Additive — codes 0-4 keep their documented meanings.
EXIT_UPDATE = 5

#: Human-readable names for --json consumers.
EXIT_NAMES = {
    EXIT_OK: "success",
    EXIT_RUNTIME: "runtime_or_validation_failure",
    EXIT_USAGE: "invalid_usage",
    EXIT_ENVIRONMENT: "environment_blocked",
    EXIT_RELEASE: "release_verification_failure",
    EXIT_UPDATE: "update_not_applicable_or_failed",
}
