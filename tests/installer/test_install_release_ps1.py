"""One-command release installer: static source-contract tests.

Offline source-level checks for installer/install-release.ps1 (the end-user
installer for the PACKAGED Windows release). No network, no PowerShell, no
Windows-only API: every assertion runs over the script source itself, so the
suite is meaningful on the Linux release-gate host as well as Windows.

Guards the contract inherited from install.ps1 (see tests/installer/
test_installer_protocol.py::TestSyntax for the bootstrap-installer twin):

  * pure-ASCII source (PS 5.1 parser + legacy codepage safety)
  * no forbidden tokens: Read-Host (prompt under -NonInteractive),
    Invoke-Expression / iex (uncontrolled execution), the [Net.ServicePoint
    Manager]::SecurityProtocol mistake class (per-process TLS monkeypatching)
  * no PowerShell-7-only syntax (null-conditional ?., ternary ?:, the
    -Encoding utf8NoBOM switch value that PS 5.1 rejects)
  * the integrity gate exists AND is compared BEFORE anything is installed
    (fail-closed ordering - the whole point of the release installer)
  * every documented exit code is defined and reachable
  * the Inno silent switches match the CI clean-install gate
  * user data is never deleted or overwritten
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALLER = REPO_ROOT / "installer" / "install-release.ps1"

pytestmark = pytest.mark.skipif(
    not INSTALLER.exists(),
    reason="installer/install-release.ps1 is required for these source-contract tests",
)


def _src() -> str:
    return INSTALLER.read_bytes().decode("ascii", errors="strict")


# ---------------------------------------------------------------------------
# Source-level hygiene (mirrors the install.ps1 constraints)
# ---------------------------------------------------------------------------


class TestSourceHygiene:
    def test_source_is_pure_ascii(self):
        """PS 5.1 parser + legacy codepage safety: source must be pure ASCII."""
        INSTALLER.read_bytes().decode("ascii")

    def test_declares_powershell_51_compatibility(self):
        src = _src()
        assert "PS 5.1" in src, "must document the PS 5.1 compatibility target"
        assert "pure ASCII source" in src, (
            "the ASCII-source constraint must be stated as a source property, "
            "not as a console-encoding note"
        )

    def test_no_interactive_prompt_token(self):
        """Read-Host would hang under -NonInteractive (the documented driver
        invocation pattern); no path in this installer may reach a prompt."""
        src = _src()
        assert "Read-Host" not in src
        # Code lines only: the docstring mention of 'no interactive prompt'
        # is prose, and assertions must be against real code.
        code_lines = [
            ln
            for ln in src.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        ]
        code = "\n".join(code_lines)
        assert "Read-Host" not in code

    def test_no_invocation_expression(self):
        """Invoke-Expression (and the `iex` alias) runs attacker-controlled
        strings; the release installer downloads a signed, hash-verified
        payload and executes it as a process, never through the evaluator."""
        src = _src()
        code_lines = [
            ln
            for ln in src.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        ]
        code = "\n".join(code_lines)
        for token in ("Invoke-Expression", "iex(", "| iex"):
            assert token not in code, (
                f"uncontrolled execution token present: {token!r}"
            )
        # 'iex (' as a statement start (the iex | iex pattern). The docstring
        # example 'iex (irm ...)' is prose and is excluded above.
        assert not re.search(r"(?m)^\s*iex\s+\(", code), (
            "the iex(irm ...) bootstrap pattern must not appear as code"
        )

    def test_no_legacy_tls_protocol_patch(self):
        """The [Net.ServicePointManager]::SecurityProtocol = Tls12 monkeypatch
        class is a PS 5.1 cargo-cult that changes process-global TLS policy
        instead of using the .NET defaults; the release installer must not
        reintroduce it."""
        src = _src()
        assert "ServicePointManager" not in src
        assert "[Net." not in src
        assert "Tls12" not in src
        assert "Tls11" not in src

    def test_no_powershell_7_only_syntax(self):
        """PS 5.1 rejects null-conditional (?.), null-coalescing (??) and the
        ternary operator (cond ? a : b). This installer must parse on both
        hosts, so none of them may appear."""
        src = _src()

        # Strip string literals first so regex/JSON quantifiers inside
        # strings (e.g. '(?<sha>...)' ) cannot be mistaken for a ternary ?.
        stripped = re.sub(r"'(?:[^']|'')*'", "''", src)
        stripped = re.sub(r'"(?:[^"]|"")*"', '""', stripped)
        stripped = re.sub(r"#.*", "", stripped)

        for pattern, name in (
            (r"\?\?", "null-coalescing"),
            (r"\?\.", "null-conditional"),
            (r"(?<![A-Za-z0-9_$\)\]])\s?\?\s?[^\s]", "ternary operator"),
        ):
            assert not re.search(pattern, stripped), (
                f"PS7-only {name} found in install-release.ps1 source"
            )

    def test_no_utf8nobom_switch_value(self):
        """`Set-Content -Encoding utf8NoBOM` is a PowerShell 7-only value; the
        BOM-less UTF-8 writer here is System.Text.UTF8Encoding($false), which
        works on both hosts."""
        src = _src()
        code_lines = [
            ln
            for ln in src.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        ]
        code = "\n".join(code_lines)
        assert "utf8NoBOM" not in code
        # The PS5.1-safe writer must genuinely be the mechanism used.
        assert "New-Object System.Text.UTF8Encoding $false" in code

    def test_error_action_preference_is_stop(self):
        """$ErrorActionPreference = 'Stop' is the fail-closed posture:
        an unexpected error terminates instead of being swallowed."""
        assert "$ErrorActionPreference = \"Stop\"" in _src()

    def test_irm_pipe_documented_one_liner_uses_runable_form(self):
        """The documented one-liner must invoke the script (not dot-source
        it), so the file's own $MyInvocation.InvocationName guard stays
        inert for real users."""
        src = _src()
        assert "iex (irm " in src


# ---------------------------------------------------------------------------
# Exit-code contract
# ---------------------------------------------------------------------------

# (code, token that must appear as the definition, human-readable class)
# Every documented exit code must exist as a real definition the script can
# actually emit, and must be documented in the file header.
DOCUMENTED_EXIT_CODES = {
    0: "EXIT_OK",
    1: "EXIT_INTERNAL",
    2: "EXIT_USAGE",
    3: "EXIT_ENVIRONMENT",
    4: "EXIT_RELEASE",
    5: "EXIT_RATE_LIMIT",
    6: "EXIT_DOWNLOAD",
    7: "EXIT_INTEGRITY",
    8: "EXIT_INSTALL",
    9: "EXIT_VERIFY",
}


class TestExitCodeContract:
    def test_every_documented_exit_code_is_defined(self):
        src = _src()
        missing = []
        for code, token in DOCUMENTED_EXIT_CODES.items():
            if (f"$Script:{token} = {code}") not in src:
                missing.append(f"{token}({code})")
        assert not missing, f"documented exit codes missing definitions: {missing}"

    def test_every_definition_is_reachable(self):
        """A defined-but-unused exit code is a documentation lie: each one
        must be reachable from a real Exit-NexusRelease / exit site."""
        src = _src()
        unreachable = []
        for code, token in DOCUMENTED_EXIT_CODES.items():
            if code == 0:
                continue
            # Either passed to Exit-NexusRelease (-Code $Script:EXIT_*) or
            # used in a bare `exit <number>` literal.
            reachable = (f"$Script:{token}" in src.replace(f"$Script:{token} = {code}", ""))
            if not reachable and (f"exit {code}" not in src):
                unreachable.append(token)
        assert not unreachable, f"unreachable exit codes: {unreachable}"

    def test_exit_codes_are_documented_in_header(self):
        """Every code appears in the header table with its one-line meaning.
        The lines read '#   <code>  <meaning>' - a leading '#', not the
        optional whitespace an unanchored pattern would allow."""
        src = _src()
        header = src.split("$ErrorActionPreference = ", 1)[0]
        for code in DOCUMENTED_EXIT_CODES:
            assert re.search(rf"^#\s*{code}\s+\S", header, re.MULTILINE), (
                f"exit code {code} not documented in the file header"
            )

    def test_json_frame_carries_the_same_exit_code(self):
        """The machine-readable frame must echo the numeric code drivers key
        on (never a bare ok flag hiding a distinct process exit)."""
        src = _src()
        assert "exit_code" in src
        assert src.count("exit_code       = $Code") >= 1

    def test_distinct_failure_classes_have_distinct_codes(self):
        """The contract is one code per failure class; collapse would make
        driver-side triage impossible."""
        seen = set()
        for code in DOCUMENTED_EXIT_CODES:
            assert code not in seen
            seen.add(code)


# ---------------------------------------------------------------------------
# Integrity gate: hash-verified BEFORE install (the core contract)
# ---------------------------------------------------------------------------


class TestIntegrityGate:
    def test_digest_comparison_helper_exists(self):
        src = _src()
        assert "function Get-FileSha256" in src
        assert "function Test-FileIntegrity" in src
        # Streaming hash, not file-load: large archives must not be buffered.
        assert "[System.Security.Cryptography.SHA256]::Create()" in src

    def test_expected_digest_originates_from_the_release_checksum_file(self):
        """The expected SHA256 must be resolved from SHA256SUMS.txt by asset
        file name - never hardcoded, never computed from the download."""
        src = _src()
        assert "function Get-NexusSumsDigest" in src
        # BASE-NAME comparison so both the release-root and the CI-staged
        # relative-path spellings of the entry resolve.
        assert "LastIndexOfAny" in src
        assert "-ieq" in src

    def test_manifest_cross_check_is_independent_and_fail_closed(self):
        """release-manifest.json is a second source that must agree."""
        src = _src()
        assert "function Get-NexusManifestDigest" in src
        assert "artifacts" in src
        assert "disagree" in src, "must BLOCK when the two contract files disagree"

    def test_hash_comparison_runs_before_any_install(self):
        """Ordering is the contract: the digest is compared BEFORE the
        installer is spawned / the archive is expanded. Locate each site and
        assert the hash call precedes it in the source."""
        src = _src()
        hash_sites = [m.start() for m in re.finditer(r"-ExpectedSha256", src)]
        assert hash_sites, "no digest-pinned download found"
        # Definition sites install nothing - only call sites do.
        install_sites = [
            m.start()
            for m in re.finditer(r"Install-NexusReleaseSetup|Expand-NexusReleaseZip", src)
            if not src[max(0, m.start() - 40) : m.start()].rstrip().endswith("function ")
        ]
        assert install_sites, "no install entry point found"
        first_hash = min(hash_sites)
        first_install = min(install_sites)
        assert first_hash < first_install, (
            "integrity gate must run before the install: a digest check after "
            "the install would already have committed an unverified payload"
        )

    def test_download_verification_happens_on_the_partial_file(self):
        """The atomic-move pattern: verification on .partial before the
        destination exists, so an interrupted download can never be mistaken
        for a complete artifact."""
        src = _src()
        assert ".partial-$PID" in src
        assert "Test-FileIntegrity -Path $partial" in src
        assert "Move-Item -LiteralPath $partial -Destination $Destination -Force" in src
        # Verify-on-partial must come before the move.
        assert src.index("Test-FileIntegrity -Path $partial") < src.index(
            "Move-Item -LiteralPath $partial -Destination $Destination -Force"
        )

    def test_partial_residue_is_cleaned_on_every_failure_path(self):
        src = _src()
        assert 'if (Test-Path -LiteralPath $partial) { Remove-Item -LiteralPath $partial -Force' in src
        # At least two sites: the pre-attempt cleanup and the catch branch.
        assert src.count("Remove-Item -LiteralPath $partial -Force") >= 2

    def test_size_floor_and_empty_rejection(self):
        """Truncated and zero-byte payloads are BLOCKED before hashing."""
        src = _src()
        assert "MinBytes" in src
        assert "downloaded file is empty (0 bytes)" in src
        assert "truncated download suspected" in src

    def test_missing_checksum_entry_blocks_the_install(self):
        src = _src()
        assert "has no entry for" in src
        assert "INTEGRITY BLOCKED" in src
        assert "EXIT_INTEGRITY" in src

    def test_digest_mismatch_blocks_and_records_state(self):
        """A mismatch must never be a warning, and the state file must
        record it so support tooling sees the truth."""
        src = _src()
        assert "SHA256 MISMATCH" in src
        assert "InstallState" in src or 'install_state     = "BLOCKED"' in src
        assert '"BLOCKED"' in src

    def test_contract_files_are_downloaded_before_the_payload(self):
        """The checksum manifest must exist before the payload is fetched, so
        a payload can always be verified (never downloaded un-pinnable)."""
        src = _src()
        contract_loop = src.index("foreach ($contract in @(")
        payload_download = src.index("-Destination $payloadPath")
        assert contract_loop < payload_download, (
            "contract files must be staged before the payload download"
        )

    def test_unverified_payload_is_never_installed(self):
        """Fail-closed posture: the payload download is digest-pinned, the
        install section only runs after that call returns, and every failure
        path around it exits before any install call site."""
        src = _src()
        # Definition sites install nothing - only call sites do.
        install_sites = [
            m.start()
            for m in re.finditer(r"Install-NexusReleaseSetup|Expand-NexusReleaseZip", src)
            if not src[max(0, m.start() - 40) : m.start()].rstrip().endswith("function ")
        ]
        assert install_sites, "no install entry point found"
        first_install = min(install_sites)

        # The payload download call site (invoked with -Destination
        # $payloadPath, so the definition is excluded) must be digest-pinned
        # and must precede every install call site.
        payload_call = re.search(
            r"Invoke-NexusReleaseDownload\s+-Url\s+\$PayloadUrl\s+-Destination\s+\$payloadPath",
            src,
        )
        assert payload_call, "the payload download must be called"
        assert payload_call.start() > first_install, (
            "the digest-pinned payload download must run after the install "
            "helpers are defined (call sites only)"
        )
        rest = src[payload_call.start() : payload_call.start() + 260]
        assert "-ExpectedSha256 $sumsDigest" in rest, (
            "the payload download must pass -ExpectedSha256 (the digest "
            "resolved from SHA256SUMS.txt), not a size check alone"
        )

        # No failure path may fall through into the install: the payload
        # download sits in a try block whose catch exits before the install
        # section is reached.
        payload_catch = src.index("catch {", payload_call.start())
        install_section = src.index("Write-Step \"Installing\"")
        assert payload_catch < install_section, (
            "the payload-download catch must run before the install section"
        )
        assert "Exit-NexusRelease" in src[payload_catch : install_section], (
            "a payload-download failure must exit before the install section"
        )

    def test_install_call_sites_are_surrounded_by_try(self):
        """Both install entry points run inside try blocks whose catch exits:
        an exception can never be swallowed into a success report."""
        src = _src()
        for call in ("Install-NexusReleaseSetup", "Expand-NexusReleaseZip"):
            # Only real call sites: the invocation carries call arguments
            # (-SetupExe / -ZipPath), which the function definition does not.
            call_sites = [
                m.start()
                for m in re.finditer(rf"{call}\s+\-", src)
            ]
            assert call_sites, f"{call} must have at least one call site"
            for site in call_sites:
                before = src[max(0, site - 1500) : site]
                after = src[site : site + 8000]
                assert "try {" in before, f"{call} must run inside a try block"
                catch = after.find("catch {")
                assert catch >= 0, f"{call} must have a catch that exits"
                # The first catch after the call site must exit (not just
                # log), so a failed install never falls through to the
                # post-install proof or the success summary.
                assert "Exit-NexusRelease" in after[catch : catch + 1400], (
                    f"{call} failure path must exit rather than continue"
                )

    def test_telemetry_records_digest_and_outcome(self):
        src = _src()
        assert "actual_sha" in src
        assert "expected_sha" in src
        assert "verification" in src
        assert "outcome" in src
        assert "download_telemetry" in src

    def test_state_file_records_digest_and_availability_state(self):
        src = _src()
        assert r"state\install-release.json" in src
        assert "asset_sha256" in src
        assert "sums_sha256" in src
        assert "manifest_sha256" in src
        assert "install_state" in src
        # Availability vocabulary (INSTALL_INTEGRITY.md truth table).
        for state in ("AVAILABLE", "DEGRADED", "BLOCKED"):
            assert f'"{state}"' in src

    def test_state_file_is_written_atomically(self):
        """A crash must never leave a truncated state file (the durable
        record drivers and support tooling read)."""
        src = _src()
        assert ".tmp-$PID" in src
        assert "Move-Item -LiteralPath $tmp -Destination $StatePath -Force" in src

    def test_zip_slip_guard_present(self):
        """The portable path extracts an untrusted archive: traversal and
        absolute entries must be rejected before extraction."""
        src = _src()
        assert "function Expand-NexusReleaseZip" in src
        assert "zip-slip blocked" in src
        assert "Expand-Archive" in src


# ---------------------------------------------------------------------------
# Install invocation (mirrors scripts/build/clean_install_test.ps1)
# ---------------------------------------------------------------------------


class TestInstallInvocation:
    def test_inno_silent_switches_match_ci_gate(self):
        """The exact switch set CI's clean-install test uses (verified against
        installer/NexusScalpEngine.iss + scripts/build/clean_install_test.ps1):
        /VERYSILENT /SUPPRESSMSGBOXES /NORESTART + /DIR + /LOG."""
        src = _src()
        for switch in ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/DIR=", "/LOG="):
            assert switch in src, f"missing Inno silent switch {switch}"

    def test_installer_is_run_with_wait_and_passthru(self):
        """The exit code must be the Inno installer's own, not a spawned-shell
        approximation."""
        src = _src()
        assert "Start-Process -FilePath $SetupExe -ArgumentList $installerArgs -Wait -PassThru" in src
        assert "$p.ExitCode -ne 0" in src

    def test_nonzero_installer_exit_is_reported_with_log_tail(self):
        src = _src()
        assert "setup.exe exited with code" in src
        assert "Setup log" in src

    def test_post_install_proof_runs_version_and_verify_release(self):
        """Post-install proof must run the installed product's own checks -
        the same commands scripts/build/clean_install_test.ps1 runs against
        the installed tree, and the verify-release contract the installer
        payload embeds (BUG-160/BUG-166)."""
        src = _src()
        assert "version --plain" in src
        assert "verify-release --root" in src
        # Version probe must be exit-code AND content checked, like CI.
        assert '-notmatch "version"' in src

    def test_post_install_proof_failure_blocks_success(self):
        """A failed verify must exit non-zero with the documented verify exit
        code - never report success."""
        src = _src()
        # The real call site passes -TargetInstallDir as a call argument
        # (the definition's body only contains probe commands, not the exit
        # route - it must not satisfy this assertion on its own).
        verify_call = re.search(r"Invoke-NexusPostInstallVerify\s+-TargetInstallDir", src)
        assert verify_call, "Invoke-NexusPostInstallVerify must be called"
        tail = src[verify_call.start() : verify_call.start() + 1800]
        # The catch around the call must record BLOCKED state and exit with
        # the documented verify exit code.
        assert "catch {" in tail
        assert '"BLOCKED"' in tail
        assert "EXIT_VERIFY" in tail
        assert "post-install verification failed" in tail

    def test_no_launch_reports_degraded_not_available(self):
        """-NoLaunch cannot execute the verifier: the install must be recorded
        DEGRADED, never falsely AVAILABLE."""
        src = _src()
        no_launch = src.index("if ($NoLaunch)")
        block = src[no_launch:no_launch + 700]
        assert "DEGRADED" in block
        assert "skipped-no-launch" in block

    def test_user_data_is_never_deleted_or_overwritten(self):
        """Hard safety boundary: config/logs/data/models under the
        %LOCALAPPDATA% user-data root survive every path in this script."""
        src = _src()
        # Only additive, installer-owned writes into the data root.
        assert r"state\install-release.json" in src
        assert r"logs\install-release.log" in src
        code_lines = [
            ln
            for ln in src.splitlines()
            if ln.strip() and not ln.lstrip().startswith("#")
        ]
        code = "\n".join(code_lines)
        # No recursive delete may ever target the user-data root.
        assert "NexusScalpEngine` -Recurse" not in code
        assert "$DataDir -Recurse" not in code
        assert not re.search(r"Remove-Item[^|;]*DataDir[^|;]*-Recurse", code)
        # The only recursion allowed is our own temp staging tree.
        allowed = re.findall(r"Remove-Item\s+-LiteralPath\s+\$StagingDir\s+-Recurse", code)
        assert len(allowed) >= 1

    def test_installer_default_dir_matches_inno_script(self):
        """The default -InstallDir must agree with DefaultDirName in
        installer/NexusScalpEngine.iss, otherwise two installs of the same
        release would land in different trees."""
        src = _src()
        iss = (REPO_ROOT / "installer" / "NexusScalpEngine.iss").read_bytes().decode(
            "ascii", errors="replace"
        )
        m = re.search(r"DefaultDirName=(\{localappdata\}\\[^\r\n]+)", iss)
        assert m, "could not read DefaultDirName from the .iss"
        # {localappdata}\Programs\NexusScalpEngine -> Programs\NexusScalpEngine
        relative = m.group(1).split("}", 1)[1]
        assert relative in src, (
            "default install dir diverged from the Inno DefaultDirName "
            f"({relative!r})"
        )


# ---------------------------------------------------------------------------
# Idempotency + repair
# ---------------------------------------------------------------------------


class TestIdempotency:
    def test_already_current_detection_exists(self):
        """A second run on an already-current install must exit 0 WITHOUT
        reinstalling."""
        src = _src()
        assert "function Test-NexusAlreadyCurrent" in src
        assert "already-current" in src
        # The guard must be checked before the download section.
        guard = src.index("Test-NexusAlreadyCurrent")
        download = src.index("Downloading release artifacts")
        assert guard < download

    def test_already_current_exits_zero(self):
        src = _src()
        idx = src.index("action           = \"already-current\"")
        tail = src[idx - 200 : idx + 700]
        assert "EXIT_OK" in tail

    def test_repair_bypasses_the_already_current_guard(self):
        src = _src()
        assert "if ((-not $Repair) -and (Test-NexusAlreadyCurrent" in src
        assert "-Repair" in src

    def test_repair_re_installs_the_same_version(self):
        src = _src()
        assert "repaired" in src

    def test_single_writer_lock(self):
        """A second concurrent installer must not race the first; it reports
        a well-formed skip and exits 0 (install.ps1 parity)."""
        src = _src()
        assert "Wait-NexusReleaseInstallLock" in src
        assert "another release installer is running" in src
        assert "skipped" in src

    def test_lock_is_released_on_every_path(self):
        src = _src()
        assert "Release-NexusReleaseInstallLock" in src
        # Held across the whole mutating section via try/finally.
        assert "finally {" in src
        assert src.index("Wait-NexusReleaseInstallLock") < src.index("Release-NexusReleaseInstallLock")


# ---------------------------------------------------------------------------
# JSON protocol discipline
# ---------------------------------------------------------------------------


class TestJsonProtocol:
    def test_protocol_version_is_pinned_and_refused_when_unsupported(self):
        """A driver passing a protocol version this installer does not
        implement must get a loud exit 2, not silent misinterpretation."""
        src = _src()
        assert "$Script:ProtocolVersionValue = 1" in src
        assert "unsupported -ProtocolVersion" in src
        assert "EXIT_USAGE" in src

    def test_default_protocol_version_is_one(self):
        assert "[int]$ProtocolVersion = 1" in _src()

    def test_json_only_on_stdout_in_driver_mode(self):
        """stdout is reserved for the single JSON frame; human diagnostics
        must route to stderr (the RC-1 banner-purity contract)."""
        src = _src()
        assert "[Console]::Error.WriteLine" in src
        assert "function Emit-NexusFrame" in src
        assert "Write-Output" in src

    def test_failure_frame_is_machine_readable(self):
        src = _src()
        assert "function Exit-NexusRelease" in src
        for field in ("ok", "protocol_version", "exit_code", "reason", "hint"):
            assert field in src

    def test_dry_run_mutates_nothing(self):
        """-DryRun must resolve the release and print the plan without any
        download or install."""
        src = _src()
        dry = src.index("if ($DryRun)")
        block = src[dry : dry + 1200]
        assert "dry-run" in block
        assert "Would install" in block or "payload_url" in block
        # Dry run must return before the staging/download section.
        download = src.index("Downloading release artifacts")
        assert dry < download
        dry_exit = src.index("RUN dry-run")
        assert dry_exit < download

    def test_dry_run_and_repair_are_mutually_exclusive(self):
        src = _src()
        assert "Cannot combine -DryRun with -Repair" in src
        assert "EXIT_USAGE" in src

    def test_show_resolved_paths_exits_before_any_network(self):
        """-ShowResolvedPaths is the zero-network, zero-mutation probe path
        for support (install.ps1 parity)."""
        src = _src()
        # The dispatch site (not the param declaration or a hint string).
        idx = src.index("if ($ShowResolvedPaths) {")
        assert "exit 0" in src[idx : idx + 200]
        # The only release-API request in the script lives inside
        # Resolve-NexusRelease. The DISPATCH site must precede the CALL to
        # that function; the function definition itself sits earlier in the
        # file and executes nothing on its own.
        resolve_call = re.search(r"Resolve-NexusRelease\s+-RequestedVersion\s+\$Version", src)
        assert resolve_call, "Resolve-NexusRelease must be called"
        assert idx < resolve_call.start(), (
            "-ShowResolvedPaths must return before the release is resolved "
            "(i.e. before any network access)"
        )

    def test_resolved_path_report_shape(self):
        src = _src()
        for key in (
            "protocol_version",
            "installer_version",
            "mode",
            "install_dir",
            "data_dir",
            "state_file",
        ):
            assert key in src


# ---------------------------------------------------------------------------
# Release resolution: failure classes the API path must handle
# ---------------------------------------------------------------------------


class TestReleaseResolution:
    def test_latest_release_endpoint(self):
        src = _src()
        assert "/releases/latest" in src
        assert "api.github.com/repos/Opselon/NexusTradingForexBot/releases" in src

    def test_rate_limit_is_distinguished_and_actionable(self):
        """Unauthenticated callers get 60/hour. The failure must be exit 5
        with a message that names the -Version pin as the workaround."""
        src = _src()
        assert "60 requests/hour" in src
        assert "x-ratelimit-remaining" in src
        assert "x-ratelimit-reset" in src
        assert "EXIT_RATE_LIMIT" in src

    def test_no_assets_is_blocked(self):
        src = _src()
        assert "published no assets" in src

    def test_draft_and_prerelease_are_refused(self):
        """A draft cannot be installed; a prerelease requires an explicit
        -Version pin so a pre-release never lands as 'latest stable'."""
        src = _src()
        assert "is a DRAFT" in src
        assert "is a PRERELEASE" in src

    def test_pinned_version_skips_the_api_and_builds_urls(self):
        """The rate-limit-proof path: build download URLs from the tag
        directly, with no API call."""
        src = _src()
        assert "releases/download" in src
        assert "pinned via -Version (no API lookup)" in src
        pinned = src.index("if ($RequestedVersion)")
        api = src.index("Invoke-WebRequest -Uri $url")
        assert pinned < api

    def test_asset_names_match_the_release_pipeline(self):
        """The canonical asset names produced by .github/workflows/release.yml
        and hashed into SHA256SUMS.txt."""
        src = _src()
        assert "NexusScalpEngine-$ver-win-x64-setup.exe" in src
        assert "NexusScalpEngine-$ver-win-x64.zip" in src
        assert "SHA256SUMS.txt" in src
        assert "release-manifest.json" in src

    def test_missing_payload_asset_names_available_ones(self):
        """When the release exists but the requested asset does not, the
        error must list what IS published (the first question support asks)."""
        src = _src()
        assert "Available assets" in src
        assert "does not publish" in src

    def test_http_error_status_is_reported(self):
        src = _src()
        assert "404" in src
        assert "HTTP $status" in src

    def test_non_windows_and_non_x64_are_refused(self):
        """ARM64 is explicitly unsupported by the dependency stack; the
        packaged release is x64 only (installer/NexusScalpEngine.iss)."""
        src = _src()
        assert "Windows 10/11 x64" in src
        assert "ARM64" in src or "arm64" in src
        assert "EXIT_ENVIRONMENT" in src


# ---------------------------------------------------------------------------
# The .iss / CI cross-check: asset names + switches stay in sync
# ---------------------------------------------------------------------------


class TestPipelineContractSync:
    def test_payload_names_agree_with_the_iss_output_base(self):
        iss = (REPO_ROOT / "installer" / "NexusScalpEngine.iss").read_bytes().decode(
            "ascii", errors="replace"
        )
        base = re.search(r"OutputBaseFilename=([^\r\n]+)", iss)
        assert base, "could not read OutputBaseFilename from the .iss"
        # NexusScalpEngine-{#MyAppVersion}-win-x64-setup
        assert "win-x64-setup" in base.group(1)
        src = _src()
        assert "win-x64-setup.exe" in src
        assert "win-x64.zip" in src

    def test_inno_privileges_are_lowest_so_no_admin_is_needed(self):
        iss = (REPO_ROOT / "installer" / "NexusScalpEngine.iss").read_bytes().decode(
            "ascii", errors="replace"
        )
        assert "PrivilegesRequired=lowest" in iss
        src = _src()
        assert "no Administrator" in src or "no admin" in src.lower()

    def test_ci_gate_commands_are_mirrored(self):
        """scripts/build/clean_install_test.ps1 is the authority for how the
        built setup.exe is driven; assert the same switch set."""
        ci = (REPO_ROOT / "scripts" / "build" / "clean_install_test.ps1")
        if not ci.exists():
            pytest.skip("scripts/build/clean_install_test.ps1 not present")
        ci_src = ci.read_bytes().decode("ascii", errors="replace")
        src = _src()
        for switch in ("/VERYSILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/DIR=", "/LOG="):
            assert switch in ci_src, f"CI gate no longer uses {switch} - contract drift"
            assert switch in src, f"release installer diverged from CI gate on {switch}"

    def test_ci_post_install_checks_are_mirrored(self):
        ci = (REPO_ROOT / "scripts" / "build" / "clean_install_test.ps1")
        if not ci.exists():
            pytest.skip("scripts/build/clean_install_test.ps1 not present")
        ci_src = ci.read_bytes().decode("ascii", errors="replace")
        src = _src()
        assert "version --plain" in ci_src
        assert "version --plain" in src
        assert "verify-release" in src

    def test_checksum_line_format_matches_the_pipeline(self):
        """.github/workflows/release.yml writes coreutils-style
        '<lowercase-hex>  <relative-path>' lines; the parser must accept that
        shape plus the optional binary-mode ' *' marker."""
        wf = REPO_ROOT / ".github" / "workflows" / "release.yml"
        if not wf.exists():
            pytest.skip("release.yml not present")
        wf_src = wf.read_bytes().decode("ascii", errors="replace")
        assert '"$h  $rel"' in wf_src
        src = _src()
        assert '[0-9a-fA-F]{64}' in src
        assert '\\*?' in src

    def test_manifest_artifact_shape_matches_the_pipeline(self):
        """release-manifest.json artifacts carry name/relative_path/
        size_bytes/sha256 records; the digest lookup keys on name."""
        wf = REPO_ROOT / ".github" / "workflows" / "release.yml"
        if not wf.exists():
            pytest.skip("release.yml not present")
        wf_src = wf.read_bytes().decode("ascii", errors="replace")
        assert "generate_manifest" in wf_src
        src = _src()
        assert '$a.name' in src
        assert '$a.sha256' in src

