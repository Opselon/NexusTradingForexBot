"""WSL2 Linux platform registry-entry tests (Agent 11, 2026-09-11).

Pins the documented environment claims so documentation and reality cannot
silently diverge:

1. scripts/linux/mt5_runtime.sh must have LF line endings in the REPO BLOB
   (the .gitattributes `text eol=lf` pin only fixes checkouts - this test
   fails if someone re-commits the script with CRLF, which breaks bash on
   Linux while exiting 0: masked breakage, proven 2026-09-11 on WSL2).
2. .gitattributes must keep the `scripts/linux/*.sh text eol=lf` pin.
3. The WSL2 platform entry in docs/RELEASE.md section 3 must keep the
   fail-closed claims: direct Python MetaTrader5 IPC = unsupported,
   supported path = RemoteMT5GatewayAdapter, Xvfb required, network caveat.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]


class TestLinuxScriptEol:
    def test_runtime_script_blob_is_lf(self):
        """The committed mt5_runtime.sh blob must contain zero CRLF bytes.

        A CRLF blob breaks every subcommand on Linux with
        `set: -CR: invalid option` while the script still exits 0 on some
        paths - masked breakage (proven on WSL2 2026-09-11).
        """
        blob = REPO / "scripts" / "linux" / "mt5_runtime.sh"
        data = blob.read_bytes()
        assert b"\r\n" not in data, (
            "scripts/linux/mt5_runtime.sh was committed with CRLF line endings; "
            "bash on Linux fails with confusing $-CR errors. Re-commit with LF."
        )
        assert data.count(b"\n") > 20, "script looks truncated"

    def test_gitattributes_pins_linux_scripts_lf(self):
        """The `scripts/linux/*.sh text eol=lf` pin must stay in .gitattributes."""
        attrs = (REPO / ".gitattributes").read_text(encoding="utf-8")
        assert "scripts/linux/*.sh text eol=lf" in attrs

    def test_all_linux_shell_scripts_are_lf(self):
        """Every committed shell script under scripts/linux must be CRLF-free."""
        linux_dir = REPO / "scripts" / "linux"
        offenders = [p.name for p in linux_dir.glob("*.sh") if b"\r\n" in p.read_bytes()]
        assert not offenders, f"CRLF shell scripts under scripts/linux/: {offenders}"


class TestWsl2PlatformRegistryEntry:
    def test_release_md_documents_wsl2_entry_with_fail_closed_claims(self):
        """docs/RELEASE.md section 3 must carry the WSL2 entry with the correct,
        fail-closed capability claims (no Wine-IPC-as-supported drift)."""
        text = (REPO / "docs" / "RELEASE.md").read_text(encoding="utf-8")
        assert "Linux (WSL2" in text, "WSL2 platform row missing from RELEASE.md section 3"
        assert "RemoteMT5GatewayAdapter" in text
        assert "unsupported" in text.lower(), "row must state direct IPC is unsupported"

    def test_platform_doc_rejects_wine_ipc_and_requires_xvfb(self):
        """docs/linux_wsl2_mt5_platform.md must mark Wine IPC unsupported and
        keep Xvfb + gateway-path requirements (never document the hack)."""
        text = (REPO / "docs" / "linux_wsl2_mt5_platform.md").read_text(encoding="utf-8")
        assert "UNSUPPORTED" in text, "direct Python MT5 IPC must be marked UNSUPPORTED"
        assert "Xvfb" in text, "headless display requirement missing"
        assert "RemoteMT5GatewayAdapter" in text, "supported path missing"
        assert "blocked by default" in text, "WSL2-to-Windows-host network caveat missing"
        assert "numpy" in text and "<2" in text, "numpy<2 requirement missing"

    def test_doctor_script_never_gains_live_credential_defaults(self):
        """Guard the TEST-platform boundary: mt5_doctor.py must keep failing
        on exported MT5 credential env vars (no defaults added)."""
        src = (REPO / "scripts" / "linux" / "mt5_doctor.py").read_text(encoding="utf-8")
        for key in ("NSE_MT5__ACCOUNT", "NSE_MT5__PASSWORD", "NSE_MT5__SERVER"):
            assert key in src, f"doctor must keep checking {key}"
        assert "no-live-credentials-in-env" in src


if __name__ == "__main__":
    sys.exit(__import__("pytest").main([__file__, "-v"]))
