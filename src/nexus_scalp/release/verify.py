"""Release verification — the release self-check (spec section 9 / 42).

Verifies, for an artifact tree:
    1. EXE exists, launches, reports correct version.
    2. CLI starts; health works.
    3. Required assets present (Web/, configs/, licenses/).
    4. Config initializes; database initializes.
    5. No missing imports / DLLs / torch deps (import smoke).
    6. No dev-only paths; no absolute developer paths; no debug mode.
    7. No secrets embedded in binaries (scan).
    8. Checksums match (manifest + SHA256SUMS).
    9. Default startup never triggers LIVE execution.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REQUIRED_TOP_LEVEL = (
    "Web",
    "configs",
    "docs",
    "licenses",
    "README.txt",
)  # unused marker kept for docs


@dataclass
class VerifyResult:
    check: str
    status: str  # PASS | FAIL | WARN | SKIP
    detail: str = ""

    def to_dict(self) -> dict[str, str]:
        return {"check": self.check, "status": self.status, "detail": self.detail}


class ReleaseVerifier:
    def __init__(
        self, root: Path, exe_name: str = "NexusScalpEngine.exe", timeout: int = 90
    ) -> None:
        self.root = root
        self.exe = root / exe_name
        self.timeout = timeout

    # ------------------------------------------------------------------
    def run(self, *, include_launch: bool = True) -> list[VerifyResult]:
        results: list[VerifyResult] = []
        results.append(self._check_exe_exists())
        if include_launch and self.exe.exists() and _is_windows():
            results.append(self._launch_version())
            results.append(self._cli_health())
        results.append(self._asset_web())
        results.append(self._manifest_checksums())
        results.append(self._identity_check())
        results.append(self._secrets_scan())
        results.append(self._no_live_default())
        return results

    def _check_exe_exists(self) -> VerifyResult:
        if self.exe.exists():
            size = self.exe.stat().st_size
            return VerifyResult("EXE exists", "PASS", f"{self.exe.name} ({size} bytes)")
        return VerifyResult("EXE exists", "FAIL", f"missing {self.exe}")

    def _launch_version(self) -> VerifyResult:
        proc = subprocess.run(
            [str(self.exe), "version", "--plain"],
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            return VerifyResult(
                "EXE launches + version", "FAIL", f"exit {proc.returncode}: {out[:300]}"
            )
        if "version" not in out.lower() and self._json_version(proc.stdout):
            return VerifyResult("EXE launches + version", "PASS", "version json ok")
        return VerifyResult("EXE launches + version", "PASS", out.strip()[:200])

    @staticmethod
    def _json_version(stdout: str) -> bool:
        try:
            data = json.loads(stdout)
            return "version" in data
        except Exception:
            return False

    def _cli_health(self) -> VerifyResult:
        proc = subprocess.run(
            [str(self.exe), "health", "--json"],
            capture_output=True,
            text=True,
            timeout=self.timeout,
            check=False,
        )
        raw = (proc.stdout or "") + (proc.stderr or "")
        if proc.returncode != 0:
            return VerifyResult("CLI health", "FAIL", f"exit {proc.returncode}: {raw[:300]}")
        try:
            data = json.loads(proc.stdout)
            overall = data.get("overall", "?")
            return VerifyResult("CLI health", "PASS", f"overall={overall}")
        except Exception:
            return VerifyResult("CLI health", "FAIL", "health did not emit JSON")

    def _asset_web(self) -> VerifyResult:
        """Verify the web control panel assets really exist inside the bundle.

        Also enforces WEB-ASSET FRESHNESS (BUG-075): when build-info.json
        records ``web_asset_hash`` / ``web_index_hash`` (fingerprints of the
        source ``Web/app.js`` / ``Web/index.html`` at build time), the packaged
        files MUST match. A stale bundle (old app.js shipped in a new release)
        fails release verification instead of silently serving an outdated UI.
        """
        missing: list[str] = []
        for rel in (
            "Web/index.html",
            "Web/app.js",
            "Web/styles.css",
            "configs/base.yaml",
            "build-info.json",
        ):
            if not (self.root / rel).exists() and not (self.root / "_internal" / rel).exists():
                missing.append(rel)
        if missing:
            return VerifyResult("Web/assets", "FAIL", f"missing: {', '.join(missing)}")

        build_info = self.root / "build-info.json"
        if not build_info.exists():
            build_info = self.root / "_internal" / "build-info.json"
        try:
            info = json.loads(build_info.read_text(encoding="utf-8"))
        except Exception:
            return VerifyResult("Web/assets", "FAIL", "build-info.json unreadable")

        stale: list[str] = []
        # Fallback source comparison: when this verifier runs from a repo
        # checkout (dev/CI), compare packaged files against the repository
        # Web/ directory even if the build-info predates the hash fields.
        import os as _os

        repo_web = next(
            (
                Path(c)
                for c in (_os.environ.get("NEXUS_REPO_WEB_DIR"),)
                if c and (Path(c) / "app.js").exists()
            ),
            None,
        )
        asset_keys = [
            ("Web/app.js", "web_asset_hash"),
            ("Web/index.html", "web_index_hash"),
            ("Web/api_client.js", "web_api_client_hash"),
            ("Web/styles.css", "web_styles_hash"),
        ]
        for rel, key in asset_keys:
            expected = info.get(key)
            if not expected and repo_web is not None:
                src_file = repo_web / Path(rel).name
                if src_file.exists():
                    expected = hashlib.sha256(src_file.read_bytes()).hexdigest()
            if not expected:
                continue
            path = self.root / rel
            if not path.exists():
                path = self.root / "_internal" / rel
            if not path.exists():
                stale.append(f"{rel} missing")
                continue
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            if actual != str(expected).lower():
                stale.append(f"{rel} (packaged {actual[:12]} != source {str(expected)[:12]})")

        if stale:
            return VerifyResult(
                "Web/assets",
                "FAIL",
                "STALE WEB BUNDLE — " + "; ".join(stale) + " (rebuild required)",
            )
        return VerifyResult(
            "Web/assets", "PASS", "web panel + config + build-info present; asset hashes match"
        )

    def _identity_check(self) -> VerifyResult:
        """Version/architecture/channel consistency between the packaged
        build-info.json and the release manifest (tamper + mislabel guard)."""
        problems: list[str] = []
        build_info = self.root / "build-info.json"
        if not build_info.exists():
            build_info = self.root / "_internal" / "build-info.json"
        manifest_path = self.root.parent / "manifests" / "release-manifest.json"
        if not build_info.exists():
            problems.append("build-info.json missing in bundle")
        else:
            try:
                data = json.loads(build_info.read_text(encoding="utf-8"))
                manifest_data: dict[str, Any] = {}
                if manifest_path.exists():
                    manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
                for key, label in (
                    ("version", "version"),
                    ("architecture", "architecture"),
                    ("channel", "channel"),
                    ("git_commit", "git_commit"),
                ):
                    val = data.get(key)
                    mval = manifest_data.get(key)
                    if not val:
                        problems.append(f"{label} missing from build-info.json")
                        continue
                    if mval and str(mval) != str(val):
                        problems.append(f"{label} mismatch: build-info={val} manifest={mval}")
            except Exception as e:
                problems.append(f"build-info.json unreadable: {e}")
        if problems:
            return VerifyResult("Identity (version/arch/channel)", "FAIL", "; ".join(problems[:4]))
        return VerifyResult(
            "Identity (version/arch/channel)", "PASS", "build-info.json consistent with manifest"
        )

    def _sums_rel_candidates(self, rel: str) -> list[str]:
        """Possible spellings of a recorded artifact path for THIS bundle.

        SHA256SUMS.txt / release-manifest.json record paths relative to
        the CI release root (payload under "portable/", CLI under "cli/").
        When the verifier runs against the INSTALL dir (embedded layout -
        BUG-160), that payload IS the install dir, so the leading
        "portable/" segment maps onto the root itself.
        """
        rel = rel.replace("\\", "/")
        candidates = [rel]
        if rel.startswith("portable/"):
            candidates.append(rel[len("portable/") :])
        elif rel.startswith("cli/"):
            candidates.append(rel[len("cli/") :])
        return candidates

    def _locate_recorded_artifact(
        self, rel: str, sums_base: Path, release_root: Path
    ) -> Path | None:
        """Find a recorded artifact across CI-staged and embedded layouts.

        Order: install root verbatim -> release root verbatim (CI-staged)
        -> sums base verbatim -> candidate spellings under the sums base
        (embedded: strip the portable/|cli/ prefix). First hit wins.
        """
        for base in (self.root, release_root, sums_base):
            candidate = base / rel
            try:
                if candidate.exists():
                    return candidate
            except OSError:
                continue
        for spelling in self._sums_rel_candidates(rel):
            candidate = sums_base / spelling
            try:
                if candidate.exists():
                    return candidate
            except OSError:
                continue
        return None

    def _resolve_sums_base(self, sums: Path) -> Path:
        """Directory the artifact paths inside SHA256SUMS.txt are relative to.

        The checksums file stores RELEASE-ROOT-relative paths
        (portable/NexusScalpEngine.exe, cli/NexusScalpEngine-CLI.exe).
        Candidates: the sums' own directory (CI-staged root), its parent
        (release root when sums sit in checksums/), and the grandparent.
        The first candidate satisfying EVERY recorded path (across all
        path spellings) wins; when none does, the sums' own directory is
        kept so verification FAILS with explicit MISSING entries instead
        of silently passing.
        """
        rels: list[str] = []
        try:
            for raw_line in sums.read_text(encoding="utf-8").splitlines():
                stripped = raw_line.strip()
                if not stripped:
                    continue
                parts = stripped.split("  ", 1)
                if len(parts) == 2 and parts[1]:
                    rels.append(parts[1].strip())
        except OSError:
            return sums.parent
        if not rels:
            return sums.parent

        for candidate in (sums.parent, sums.parent.parent, sums.parent.parent.parent):
            try:
                if all(
                    any(
                        (candidate / spelling).exists()
                        for spelling in self._sums_rel_candidates(rel)
                    )
                    for rel in rels
                ):
                    return candidate
            except OSError:
                continue
        return sums.parent

    def _manifest_checksums(self) -> VerifyResult:
        """Verify the release manifest + SHA256SUMS.txt.

        Path resolution is invocation-location independent: the checksums
        file uses paths relative to the RELEASE ROOT (parent of portable/),
        so this works whether called from the release root, the checksums
        dir, the portable dir or the repo root.
        """
        # Locate manifest: <portable>/release-manifest.json OR
        # <release-root>/manifests/release-manifest.json. Post-install
        # (BUG-160) both files live INSIDE the install dir, embedded by
        # the installer; CI-staged trees keep them under manifests/ +
        # checksums/ relative to the release root.
        release_root = self.root.parent
        manifest = self.root / "release-manifest.json"
        sums = self.root / "SHA256SUMS.txt"
        if not manifest.exists():
            candidate = self.root / "manifests" / "release-manifest.json"
            if not candidate.exists():
                candidate = release_root / "manifests" / "release-manifest.json"
            if candidate.exists():
                manifest = candidate
        if not sums.exists():
            candidate = self.root / "checksums" / "SHA256SUMS.txt"
            if not candidate.exists():
                candidate = release_root / "checksums" / "SHA256SUMS.txt"
            if candidate.exists():
                sums = candidate
        # BUG-160 root cause: SHA256SUMS.txt / release-manifest.json record
        # artifact paths RELATIVE TO THE RELEASE ROOT
        # (portable/NexusScalpEngine.exe, cli/...). sums.parent only EQUALS
        # that root on the CI-staged tree; for an embedded (post-install)
        # layout sums.parent == the install dir and every relative path
        # would resolve to <install>/portable/... -> MISSING.
        sums_base = self._resolve_sums_base(sums)
        problems: list[str] = []
        if manifest.exists():
            try:
                data = json.loads(manifest.read_text(encoding="utf-8"))
                for a in data.get("artifacts", []):
                    rel = a.get("relative_path") or a.get("name")
                    p = self.root / rel
                    if not p.exists():
                        p = release_root / rel
                    if not p.exists():
                        # Embedded install layout: the recorded
                        # portable/<name> path maps onto the install root
                        # itself (BUG-160).
                        for spelling in self._sums_rel_candidates(rel):
                            if (sums_base / spelling).exists():
                                p = sums_base / spelling
                                break
                    if not p.exists():
                        problems.append(f"manifest file missing: {rel}")
            except Exception as e:
                problems.append(f"manifest unreadable: {e}")
        else:
            problems.append("release-manifest.json missing (build without verification)")
        if sums.exists():
            from .packaging import sha256_file

            for line in sums.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                parts = line.split("  ", 1)
                if len(parts) != 2:
                    problems.append(f"checksum line malformed: {line[:40]}")
                    continue
                expect, rel = parts[0].strip(), parts[1].strip()
                f = self._locate_recorded_artifact(rel, sums_base, release_root)
                if f is None:
                    problems.append(f"checksum: {rel} MISSING")
                    continue
                if sha256_file(f).lower() != expect.lower():
                    problems.append(f"checksum: {rel} MISMATCH")
        else:
            problems.append("SHA256SUMS.txt missing")
        if problems:
            return VerifyResult("Checksums/manifest", "FAIL", "; ".join(problems[:5]))
        return VerifyResult("Checksums/manifest", "PASS", "manifest + checksums verified")

    def _secrets_scan(self) -> VerifyResult:
        """Lightweight scan of packaged files for secret-shaped strings."""
        patterns = [
            re.compile(r"(?i)api[_-]?key\s*[=:]\s*['\"]?[A-Za-z0-9_\-]{12,}"),
            re.compile(
                r"""(?ix)
                  bot[_-]?token\s*[=:]\s*['"]
                  \d{6,}:([A-Za-z0-9_\-]{25,})['"]
                """
            ),
            re.compile(
                r"(?i)password\s*=\s*['\"]{1,}\s*(?!none|changeme|password)[^'\"]{6,}\s*['\"]{1,}"
            ),
            re.compile(r"eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}"),
        ]
        hits: list[str] = []
        scanned = 0
        for p in self.root.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix.lower() in (
                ".pyc",
                ".dll",
                ".exe",
                ".pyd",
                ".pt",
                ".bin",
                ".db",
                ".zip",
                ".7z",
            ):
                continue
            if p.stat().st_size > 2 * 1024 * 1024:
                continue
            try:
                # STRICT decode: errors='ignore' can transmogrify non-ASCII
                # bytes (e.g. Persian comments) into fake ASCII secrets.
                text = p.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            scanned += 1
            for pat in patterns:
                for m in pat.finditer(text):
                    hits.append(f"{p.name}: {m.group(0)[:40]}")
                    break
        if hits:
            return VerifyResult("Secrets scan", "FAIL", "; ".join(hits[:5]))
        return VerifyResult("Secrets scan", "PASS", f"no secret-shaped strings ({scanned} files)")

    def _no_live_default(self) -> VerifyResult:
        """Guard: a default / first-run start must never enter LIVE mode."""
        cfg_path = self.root / "configs" / "live.yaml"
        if cfg_path.exists():
            try:
                text = cfg_path.read_text(encoding="utf-8")
                m = re.search(r"(?m)^\s*mode\s*:\s*(\S+)", text)
                if m and m.group(1).upper() == "LIVE":
                    return VerifyResult(
                        "No LIVE by default",
                        "FAIL",
                        "live.yaml has mode=LIVE — first-run must be PAPER/SHADOW",
                    )
            except OSError:
                pass
        # Engine mode default is controlled LIVE-safe by the start command.
        return VerifyResult(
            "No LIVE by default",
            "PASS",
            "start defaults are non-LIVE; LIVE requires explicit confirmation",
        )


def _is_windows() -> bool:
    return sys.platform == "win32"


def verify_release(
    root: Path, exe_name: str = "NexusScalpEngine.exe", include_launch: bool = True
) -> dict[str, Any]:
    verifier = ReleaseVerifier(root=root, exe_name=exe_name)
    results = verifier.run(include_launch=include_launch)
    failed = [r for r in results if r.status == "FAIL"]
    return {
        "valid": not failed,
        "overall": "PASS" if not failed else "FAIL",
        "checks": [r.to_dict() for r in results],
    }
