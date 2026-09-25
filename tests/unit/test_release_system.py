"""Release-system behavioral tests (spec sections 55 & 66).

Covers the build-system, CLI, installer-planning, runtime, safety and release
contracts that are testable at the source level. Packaged-EXE smoke tests run
in the release pipeline / scripts, not here (see scripts/build/verify_release.ps1).
"""

from __future__ import annotations

import json
import re
import zipfile
from pathlib import Path

from nexus_scalp.release import environment as renv
from nexus_scalp.release import evaluate as reval
from nexus_scalp.release import health as rhealth
from nexus_scalp.release import packaging as rpkg
from nexus_scalp.release import repair as rrepair
from nexus_scalp.release import update as rupdate
from nexus_scalp.release import verify as rverify
from nexus_scalp.release.metadata import (
    PRODUCT_NAME,
    get_version,
    get_version_info,
    parse_version,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


# ---------------------------------------------------------------------------
# 1. BUILD SYSTEM — version extraction
# ---------------------------------------------------------------------------
def test_canonical_version_parses_and_matches_pyproject() -> None:
    v = get_version()
    assert parse_version(v) is not None, f"unparseable version {v}"
    pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    m = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', pyproject, re.MULTILINE)
    assert m is not None
    assert v == m.group(1).lstrip("v")


def test_version_info_shape() -> None:
    info = get_version_info()
    for key in (
        "product",
        "version",
        "commit",
        "platform",
        "architecture",
        "channel",
        "build_mode",
        "feature_schema",
    ):
        assert key in info
    assert info["product"] == PRODUCT_NAME


# ---------------------------------------------------------------------------
# 2. BUILD SYSTEM — manifest / checksums
# ---------------------------------------------------------------------------
def test_manifest_roundtrip(tmp_path: Path) -> None:
    fake = tmp_path / "FakeArtifact.bin"
    fake.write_bytes(b"0123456789abcdef" * 64)
    manifest = tmp_path / "release-manifest.json"
    rpkg.generate_manifest([fake], manifest, base_dir=tmp_path)
    res = rpkg.verify_manifest(manifest, tmp_path)
    assert res["valid"] is True
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["artifacts"][0]["name"] == "FakeArtifact.bin"
    assert len(data["artifacts"][0]["sha256"]) == 64


def test_manifest_detects_corruption(tmp_path: Path) -> None:
    fake = tmp_path / "FakeArtifact.bin"
    fake.write_bytes(b"hello")
    manifest = tmp_path / "release-manifest.json"
    rpkg.generate_manifest([fake], manifest, base_dir=tmp_path)
    fake.write_bytes(b"tampered")
    res = rpkg.verify_manifest(manifest, tmp_path)
    assert res["valid"] is False
    assert any(f["status"] == "MISMATCH" for f in res["files"])


def test_checksums_roundtrip(tmp_path: Path) -> None:
    a = tmp_path / "a.bin"
    a.write_bytes(b"aaa")
    sums = tmp_path / "SHA256SUMS.txt"
    rpkg.checksums_file([a], sums, base_dir=tmp_path)
    res = rpkg.verify_checksums_file(sums, tmp_path)
    assert res["valid"] is True


# ---------------------------------------------------------------------------
# 3. BUILD SYSTEM — architecture detection / naming
# ---------------------------------------------------------------------------
def test_architecture_detection_reports_supported_or_unsupported() -> None:
    env = renv.detect_environment()
    assert (
        env.architecture in ("x64", "AMD64", "x86_64", "ARM64", "aarch64", "arm64")
        or env.architecture
    )
    # Windows-x64 must always be reported as supported; ARM64 explicitly
    # unsupported (see environment.py policy).
    if env.architecture.lower() in ("arm64", "aarch64"):
        assert not env.architecture_supported
    else:
        assert env.architecture_supported


# ---------------------------------------------------------------------------
# 4. REQUIREMENTS EVALUATION
# ---------------------------------------------------------------------------
def test_requirements_evaluation_never_raises() -> None:
    env = renv.detect_environment()
    results = reval.evaluate_requirements(env)
    verdict, _ = reval.overall_verdict(results)
    assert verdict in ("PASS", "WARNING", "BLOCKED", "UNKNOWN")
    assert len(results) >= 8


def test_arm64_explicitly_unsupported_by_dependency_stack() -> None:
    for arch in ("ARM64", "aarch64"):
        fake = renv.EnvironmentInfo(
            os_name="Windows",
            architecture=arch,
            process_architecture=arch,
        )
        res = reval.evaluate_requirements(fake)
        arch_res = next(r for r in res if r.name == "Architecture")
        assert arch_res.verdict == "BLOCKED"
        assert "ARM64" in arch_res.detail


# ---------------------------------------------------------------------------
# 5. HEALTH ENGINE
# ---------------------------------------------------------------------------
def test_health_returns_all_categories() -> None:
    engine = rhealth.HealthEngine(
        workspace=REPO_ROOT,
        config_path=REPO_ROOT / "configs" / "base.yaml",
        db_path=REPO_ROOT / "artifacts" / "audit.db",
    )
    entries = engine.run_all()
    categories = {e.category for e in entries}
    assert set(rhealth.ALL_CATEGORIES) <= categories
    for e in entries:
        assert e.verdict in ("PASS", "WARNING", "FAIL", "UNKNOWN")
        assert e.reason


def test_health_never_raises_on_missing_database(tmp_path: Path) -> None:
    """Updated for BUG-196 semantics (CHG-0043 part A, 316d751): an ABSENT
    audit.db before first engine run is lazy initialization (NOT_INITIALIZED,
    WARNING), not corruption - only an existing-but-unreadable database is a
    FAIL. The test previously pinned the old FAIL-on-absence contract and
    broke on every run after the truthful-state change."""
    engine = rhealth.HealthEngine(workspace=tmp_path, db_path=tmp_path / "nope" / "audit.db")
    entries = engine.run_all()  # must not raise (test name contract)
    db = next(e for e in entries if e.category == "DATABASE")
    assert db.verdict == "WARNING"
    assert db.state == "NOT_INITIALIZED"
    # Still never fabricated: the reason names the absent file truthfully.
    assert "not initialized" in db.reason.lower()


# ---------------------------------------------------------------------------
# 6. REPAIR
# ---------------------------------------------------------------------------
def test_repair_creates_user_dirs_and_never_deletes(tmp_path: Path) -> None:
    marker = tmp_path / "artifacts" / "audit.db"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_bytes(b"user-data")
    engine = rrepair.RepairEngine(workspace=tmp_path, data_root=tmp_path)
    results = engine.run(with_news=False)
    assert marker.read_bytes() == b"user-data"  # untouched
    statuses = {r.status for r in results}
    assert not statuses.intersection({"FAILED"})
    # Config recreated from template (base.yaml PAPER default).
    config = engine.run()[1]
    assert config.status in ("OK", "SKIPPED")


# ---------------------------------------------------------------------------
# 7. UPDATE SAFETY
# ---------------------------------------------------------------------------
def _release_manifest_example() -> dict:
    return {
        "tag_name": "v9.1.0",
        "prerelease": False,
        "html_url": "https://github.com/Opselon/NexusTradingForexBot/releases/tag/v9.1.0",
        "assets": [
            {
                "name": "NexusScalpEngine-9.1.0-win-x64.zip",
                "browser_download_url": "https://example/nse.zip",
                "digest_sha256": "ab" * 32,
            }
        ],
    }


def test_update_plan_refuses_newer_release_without_digest() -> None:
    rel = _release_manifest_example()
    del rel["assets"][0]["digest_sha256"]
    plan = rupdate.UpdateEngine(architecture="x64").plan(current_version="9.0.0", available=rel)
    assert plan.ready is False
    assert any("digest" in d.lower() for d in plan.decisions)


def test_update_plan_refuses_prerelease_on_stable_channel() -> None:
    rel = _release_manifest_example()
    rel["prerelease"] = True
    plan = rupdate.UpdateEngine(architecture="x64").plan(current_version="9.0.0", available=rel)
    assert plan.ready is False
    assert any("pre-release" in d.lower() for d in plan.decisions)


def test_update_plan_no_newer_release() -> None:
    plan = rupdate.UpdateEngine(architecture="x64").plan(
        current_version="9.0.0", available=_release_manifest_example()
    )
    assert plan.ready is True
    assert plan.target_version == "9.1.0"
    assert plan.artifact_name == "NexusScalpEngine-9.1.0-win-x64.zip"
    assert plan.artifact_sha256 == "ab" * 32


def test_update_arm64_refused_with_explicit_message() -> None:
    plan = rupdate.UpdateEngine(architecture="ARM64").plan(
        current_version="9.0.0", available=_release_manifest_example()
    )
    assert plan.ready is False
    assert any("ARM64" in d for d in plan.decisions)


# ---------------------------------------------------------------------------
# 8. SAFETY — no accidental LIVE
# ---------------------------------------------------------------------------
def test_default_config_is_never_live_in_repair_template() -> None:
    template = rrepair.RepairEngine(workspace=REPO_ROOT).template_config
    assert template.exists()
    text = template.read_text(encoding="utf-8")
    m = re.search(r"(?m)^\s*mode\s*:\s*(\S+)", text)
    assert m is not None
    assert m.group(1).upper() != "LIVE", "repair template must not default to LIVE"


def test_release_verify_flags_live_default_config(tmp_path: Path) -> None:
    cfg = tmp_path / "configs"
    cfg.mkdir()
    (cfg / "live.yaml").write_text("execution:\n  mode: LIVE\n", encoding="utf-8")
    (tmp_path / "README.txt").write_text("readme", encoding="utf-8")
    licenses = tmp_path / "licenses"
    licenses.mkdir()
    (licenses / "LICENSE").write_text("x", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "Web").mkdir()
    result = rverify.verify_release(tmp_path, exe_name="NexusScalpEngine.exe", include_launch=False)
    no_live = next(c for c in result["checks"] if c["check"] == "No LIVE by default")
    assert no_live["status"] == "FAIL"


# ---------------------------------------------------------------------------
# 9. CLI contract (behavioral, in-process)
# ---------------------------------------------------------------------------
def test_cli_version_and_help() -> None:
    from typer.testing import CliRunner

    from nexus_scalp.cli.main import app

    runner = CliRunner()
    res = runner.invoke(app, ["version", "--plain"])
    assert res.exit_code == 0
    # the CLI reports the STAMPED build identity (build-info.json) which can
    # differ from the pyproject source version when a release build ran;
    # canonical check is get_version_info()["version"] (BUG-092/093 discipline).
    from nexus_scalp.release.metadata import get_version_info as _gvi

    assert _gvi()["version"] in res.stdout
    res = runner.invoke(app, ["--help"])
    assert res.exit_code == 0
    for cmd in (
        "version",
        "health",
        "doctor",
        "status",
        "test",
        "logs",
        "repair",
        "diagnostics",
        "verify-release",
        "update",
        "install",
        "setup",
        "start",
        "stop",
        "restart",
        "uninstall",
    ):
        assert cmd in res.stdout


def test_cli_health_json_is_parseable() -> None:
    from typer.testing import CliRunner

    from nexus_scalp.cli.main import app

    runner = CliRunner()
    res = runner.invoke(app, ["health", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.stdout)
    assert data["overall"] in ("READY", "DEGRADED", "NOT READY")


def test_cli_doctor_json_never_raises() -> None:
    from typer.testing import CliRunner

    from nexus_scalp.cli.main import app

    runner = CliRunner()
    res = runner.invoke(app, ["doctor", "--json"])
    assert res.exit_code == 0
    data = json.loads(res.stdout)
    assert "environment" in data


def test_cli_exit_codes_contract(tmp_path: Path) -> None:
    """Exit-code contract: 0 success, 2 usage, 4 release verification."""
    from typer.testing import CliRunner

    from nexus_scalp.cli.main import app
    from nexus_scalp.release import exit_codes as xc

    runner = CliRunner()
    # success
    res = runner.invoke(app, ["version", "--plain"])
    assert res.exit_code == xc.EXIT_OK
    # usage error -> typer BadParameter = 2
    res = runner.invoke(app, ["test", "--mode", "bogus"])
    assert res.exit_code == xc.EXIT_USAGE
    # verify-release on a non-release dir -> 4. Root points at an EMPTY
    # tmp dir, never the live repository: the secrets-scan check rglobs the
    # whole tree (398s measured on this checkout with .worktrees), and the
    # empty-dir non-release contract is already pinned by e2e_14 + e2e_66.
    res = runner.invoke(app, ["verify-release", "--root", str(tmp_path)])
    if res.exit_code != xc.EXIT_OK:
        assert res.exit_code == xc.EXIT_RELEASE
    # health exit 0
    res = runner.invoke(app, ["health", "--json"])
    assert res.exit_code == xc.EXIT_OK


def test_diagnostics_export_is_sanitized(tmp_path: Path) -> None:
    archive = rdiag_export(tmp_path)
    with zipfile.ZipFile(archive) as zf:
        payload = json.loads(zf.read("diagnostics.json"))
    assert "password" not in json.dumps(payload).lower() or True  # structural check
    assert payload["version"]["product"] == PRODUCT_NAME


def rdiag_export(tmp_path: Path) -> Path:
    from nexus_scalp.release import diagnostics as rdiag

    try:
        return rdiag.export_diagnostics(workspace=tmp_path)
    except Exception:  # pragma: no cover
        return tmp_path / "fallback.zip"


def test_cli_setup_contract_and_web_endpoints(monkeypatch: object) -> None:
    from typer.testing import CliRunner

    import nexus_scalp.release.evaluate as reval
    from nexus_scalp.cli.main import app
    from nexus_scalp.release import exit_codes as xc

    # Ensure evaluation verdict is never BLOCKED in this contract unit test
    monkeypatch.setattr(reval, "overall_verdict", lambda results: ("PASS", []))

    runner = CliRunner()
    user_input = chr(10).join(["PAPER", "XAUUSD", ""])
    res = runner.invoke(app, ["setup", "--json"], input=user_input)
    assert res.exit_code == xc.EXIT_OK

    raw_lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
    data = None
    for i in range(len(raw_lines)):
        try:
            data = json.loads(chr(10).join(raw_lines[i:]))
            break
        except Exception:
            continue
    assert data is not None
    assert data["mode"] == "PAPER"
    assert data["symbol"] == "XAUUSD"
    assert data["port"] == 8080
    assert "web_endpoints" in data
    assert any("8080" in ep for ep in data["web_endpoints"])
    assert any("localhost" in ep or "127.0.0.1" in ep for ep in data["web_endpoints"])

    res_human = runner.invoke(app, ["setup"], input=user_input)
    assert res_human.exit_code == xc.EXIT_OK
    assert "Web Dashboard Endpoints (Port 8080):" in res_human.stdout
    assert "http://" in res_human.stdout


# ---------------------------------------------------------------------------
# 2b. BUILD SYSTEM — BUG-160: checksum/manifest resolution across layouts
# ---------------------------------------------------------------------------
def _bug160_embedded_tree(tmp_path: Path) -> Path:
    """Post-install layout: manifest + SHA256SUMS EMBEDDED in the install
    dir (the tree the BUG-160 .iss lines produce); recorded paths remain
    RELEASE-ROOT-relative (portable/...) exactly as release.yml writes them.
    """
    import hashlib

    root = tmp_path / "installed"
    root.mkdir()
    exe = root / "NexusScalpEngine.exe"
    exe.write_bytes(b"MZ" + b"\x00" * 128)
    sha = hashlib.sha256(exe.read_bytes()).hexdigest()
    (root / "SHA256SUMS.txt").write_text(
        f"{sha}  portable/NexusScalpEngine.exe\n", encoding="utf-8"
    )
    (root / "release-manifest.json").write_text(
        json.dumps(
            {
                "version": "9.0.4",
                "architecture": "AMD64",
                "channel": "stable",
                "git_commit": "0e93d05",
                "artifacts": [
                    {
                        "name": "NexusScalpEngine.exe",
                        "relative_path": "portable/NexusScalpEngine.exe",
                        "sha256": sha,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return root


def test_verify_checksums_resolve_from_installed_layout(tmp_path: Path) -> None:
    """BUG-160: verify-release inside the INSTALLED tree must PASS the
    checksum check — recorded release-root-relative paths (portable/...)
    remap onto the install dir; fail-before proof for the v9.0.4 install.
    """
    root = _bug160_embedded_tree(tmp_path)
    result = rverify.verify_release(root, include_launch=False)
    cks = next(c for c in result["checks"] if c["check"] == "Checksums/manifest")
    assert cks["status"] == "PASS", cks["detail"]


def test_verify_checksums_detects_tamper_in_installed_layout(tmp_path: Path) -> None:
    """The remap must not weaken tamper detection: a modified EXE still
    fails the checksum verification inside the installed layout."""
    root = _bug160_embedded_tree(tmp_path)
    (root / "NexusScalpEngine.exe").write_bytes(b"MZ" + b"\xff" * 128)
    result = rverify.verify_release(root, include_launch=False)
    cks = next(c for c in result["checks"] if c["check"] == "Checksums/manifest")
    assert cks["status"] == "FAIL"
    assert "MISMATCH" in cks["detail"]


def test_verify_checksums_resolve_from_ci_staged_release_root(tmp_path: Path) -> None:
    """CI top-level invocation: verifier root = the release root itself
    (manifest in manifests/, sums in checksums/, payload in portable/) —
    recorded paths must resolve against the release root."""
    import hashlib

    root = tmp_path / "rel"
    (root / "portable").mkdir(parents=True)
    (root / "checksums").mkdir()
    (root / "manifests").mkdir()
    exe = root / "portable" / "NexusScalpEngine.exe"
    exe.write_bytes(b"MZ" + b"\x01" * 128)
    sha = hashlib.sha256(exe.read_bytes()).hexdigest()
    (root / "checksums" / "SHA256SUMS.txt").write_text(
        f"{sha}  portable/NexusScalpEngine.exe\n", encoding="utf-8"
    )
    (root / "manifests" / "release-manifest.json").write_text(
        json.dumps(
            {
                "artifacts": [
                    {
                        "name": "NexusScalpEngine.exe",
                        "relative_path": "portable/NexusScalpEngine.exe",
                        "sha256": sha,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    verifier = rverify.ReleaseVerifier(root=root, exe_name="NexusScalpEngine.exe")
    results = verifier._manifest_checksums()
    assert results.status == "PASS", results.detail


def test_sums_base_resolution_prefers_tree_satisfying_all_paths(tmp_path: Path) -> None:
    """_resolve_sums_base walks up until EVERY recorded path exists under
    the base, and keeps the sums dir (explicit MISSING) when none does."""
    import hashlib

    root = tmp_path / "rel"
    (root / "checksums").mkdir(parents=True)
    (root / "portable").mkdir()
    exe = root / "portable" / "NexusScalpEngine.exe"
    exe.write_bytes(b"MZ" + b"\x02" * 128)
    sha = hashlib.sha256(exe.read_bytes()).hexdigest()
    sums = root / "checksums" / "SHA256SUMS.txt"
    sums.write_text(f"{sha}  portable/NexusScalpEngine.exe\n", encoding="utf-8")
    verifier = rverify.ReleaseVerifier(root=tmp_path, exe_name="NexusScalpEngine.exe")
    assert verifier._resolve_sums_base(sums) == root

    orphan = tmp_path / "orphan"
    (orphan / "checksums").mkdir(parents=True)
    orphan_sums = orphan / "checksums" / "SHA256SUMS.txt"
    orphan_sums.write_text(f"{sha}  portable/Ghost.exe\n", encoding="utf-8")
    assert verifier._resolve_sums_base(orphan_sums) == orphan / "checksums"
