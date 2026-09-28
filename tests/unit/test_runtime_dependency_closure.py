"""Runtime dependency closure — the "click is missing" defect class.

Incident (developer + end-user runtime): ``NexusTradingForexBot.py`` imported
``uvicorn`` at module import time and died with a raw
``ModuleNotFoundError: No module named 'click'`` — uvicorn's transitive runtime
requirement. Two things were wrong:

1. Nothing verified the DECLARED runtime dependency closure before the import,
   so an incomplete environment produced an opaque traceback with no repair
   path instead of an actionable diagnosis.
2. The standard integrity tools do not catch it. ``pip check`` / ``uv pip
   check`` compare dist-info METADATA only; a distribution whose ``dist-info``
   survived while its module files were deleted (this environment really had
   ``python_dotenv-1.2.3.dist-info`` with no ``dotenv/`` directory) is reported
   as fully satisfied.

Pinned here (all offline, no network, no installs):

* The closure is DERIVED from the project's declared requirements + installed
  metadata, transitively — never a hard-copied package list. A new requirement
  in ``pyproject.toml`` is verified without touching this file.
* A missing transitive dependency (uvicorn -> click) is reported with the
  parent chain that requires it.
* An installed-but-unimportable distribution is detected even though its
  metadata satisfies the resolver.
* Healthy environments produce NO findings: the probe must never report a
  working installation as broken, or operators will correctly ignore it.
* The startup gate fails CLOSED (non-zero, actionable text, no traceback) and
  never runs auto-repair inside a frozen bundle.
* ``NexusTradingForexBot.py`` runs the gate BEFORE its third-party imports.
"""

from __future__ import annotations

import ast
import importlib.metadata as md
import re
import subprocess
import sys
from importlib.metadata import PackageNotFoundError
from pathlib import Path

import pytest

from nexus_scalp.release import runtime_deps as rdeps

REPO_ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = REPO_ROOT / "NexusTradingForexBot.py"


# ---------------------------------------------------------------------------
# 1. Declaration: the closure is derived, never hand-listed
# ---------------------------------------------------------------------------
def test_closure_is_derived_from_declared_requirements_not_a_hardcoded_list() -> None:
    """Every declared base requirement is reachable through the walk.

    Static pin: the module must not carry its own package list. It reads
    pyproject.toml (source checkout) or installed metadata (packaged runtime)
    and follows each installed distribution's own requires.
    """
    src = Path(rdeps.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)
    # No module-level constant may enumerate requirements (e.g. a list of
    # "click", "uvicorn", ... strings used as the verification input).
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.isupper():
                    assert not isinstance(node.value, (ast.List, ast.Tuple, ast.Set)), (
                        f"{target.id} enumerates values at module level — the "
                        "dependency closure must be derived, not declared here"
                    )
    declared = rdeps.verify_runtime_closure(REPO_ROOT)
    assert declared.checked > 0
    assert "pyproject.toml" in declared.source


def test_every_declared_base_requirement_appears_in_the_walk() -> None:
    """The walk's population is exactly pyproject's [project].dependencies."""
    import tomllib

    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    declared = data["project"]["dependencies"]
    base, source = rdeps._base_requirements(REPO_ROOT)
    assert source.endswith("[project].dependencies")
    # Environment markers legitimately exclude platform-specific pins
    # (MetaTrader5 is win32-only); everything else must be present.
    names = {r.name.lower() for r in base}
    expected = {
        rdeps._parse(raw).name.lower()
        for raw in declared
        if rdeps._marker_applies(rdeps._parse(raw).marker)
    }
    assert expected <= names
    assert len(declared) >= 15, "pyproject dependency list looks truncated"


def test_uvicorn_transitive_click_requirement_is_discovered() -> None:
    """The exact incident edge: uvicorn's own metadata declares click.

    This is why click is NOT pinned by hand in the project — it must be found
    through the installed uvicorn distribution at verification time.
    """
    requires = md.requires("uvicorn") or []
    assert any(r.lower().startswith("click") for r in requires), (
        f"installed uvicorn does not declare click: {requires}"
    )


# ---------------------------------------------------------------------------
# 2. Detection: missing and unusable dependencies
# ---------------------------------------------------------------------------
def test_missing_transitive_dependency_is_reported_with_its_parent(monkeypatch) -> None:
    """A declared requirement whose distribution is absent is reported."""
    real_version = rdeps._installed_version

    def fake_version(name: str) -> str | None:
        if name.lower().replace("_", "-") == "click":
            return None  # the incident
        return real_version(name)

    monkeypatch.setattr(rdeps, "_installed_version", fake_version)
    report = rdeps.verify_runtime_closure(REPO_ROOT)
    assert not report.ok
    click = [m for m in report.missing if m.distribution.lower() == "click"]
    assert click, f"click absence not detected: {report.to_dict()}"
    assert click[0].reason == "not installed"
    assert "uvicorn" in click[0].required_by, (
        f"the parent chain must name uvicorn, got {click[0].required_by}"
    )


def test_installed_but_unimportable_distribution_is_detected(monkeypatch) -> None:
    """dist-info present, module files gone — invisible to pip check."""
    monkeypatch.setattr(
        rdeps,
        "_top_level_modules",
        lambda name: ["dotenv"] if name.lower().replace("_", "-") == "python-dotenv" else [],
    )
    monkeypatch.setattr(rdeps, "_installed_version", lambda name: "1.2.3")
    monkeypatch.setattr(rdeps, "find_spec", lambda module: None)  # every import fails
    monkeypatch.setattr(rdeps, "_specifier_satisfied", lambda *a, **k: True)
    report = rdeps.verify_runtime_closure(REPO_ROOT)
    assert not report.ok
    dotenv = [m for m in report.missing if m.distribution.lower().startswith("python")]
    assert dotenv, f"unimportable distribution not detected: {report.to_dict()}"
    assert "not importable" in dotenv[0].reason


def test_partially_resolvable_distribution_is_not_a_false_failure(monkeypatch) -> None:
    """A distribution with SOME unresolvable roots stays healthy.

    Real example: torch's metadata names ``functorch`` and sympy's names the
    ``isympy`` console script. Reporting those healthy installs as broken would
    make the gate noise and get it ignored.
    """
    monkeypatch.setattr(rdeps, "_top_level_modules", lambda name: ["torch", "functorch"])
    monkeypatch.setattr(rdeps, "find_spec", lambda module: object() if module == "torch" else None)
    assert rdeps._unimportable_modules("torch") == []


def test_every_root_unresolvable_is_reported(monkeypatch) -> None:
    """Corrupt installation: no import root resolves at all."""
    monkeypatch.setattr(rdeps, "_top_level_modules", lambda name: ["alpha", "beta"])
    monkeypatch.setattr(rdeps, "find_spec", lambda module: None)
    broken = rdeps._unimportable_modules("corrupt-pkg")
    assert broken == ["alpha", "beta"]


def test_report_diagnostic_is_actionable_and_names_the_package() -> None:
    """ "A broken installation should be fixed by the install system" — and the
    operator must be told which dependency and who requires it."""
    report = rdeps.ClosureReport(
        ok=False,
        checked=38,
        source="pyproject.toml [project].dependencies",
        missing=[rdeps.MissingDependency("click", "not installed", ["uvicorn"], ">=8.0")],
    )
    text = report.describe()
    assert "NSE runtime dependency validation failed." in text
    assert "click" in text
    assert "uvicorn" in text
    assert "38" in text


# ---------------------------------------------------------------------------
# 3. No false positives: the real environment must be judged honestly
# ---------------------------------------------------------------------------
def test_real_repository_closure_has_no_phantom_failures() -> None:
    """Fully-installed distributions are never reported as unusable.

    The only allowed outcome here is "ok", or a failure whose every entry is
    independently verifiable by importing the module in a subprocess.
    """
    report = rdeps.verify_runtime_closure(REPO_ROOT)
    for item in report.missing:
        probe = (
            f"import importlib.util,sys;"
            f"sys.exit(0 if importlib.util.find_spec({item.distribution.split('[')[0]!r}) "
            f"else 1)"
        )
        proc = subprocess.run(
            [sys.executable, "-c", probe], capture_output=True, text=True, check=False
        )
        # A missing distribution fails the probe; an unimportable one fails too.
        assert proc.returncode != 0 or item.reason.startswith("version"), (
            f"phantom failure for {item.distribution}: {item.reason} "
            "(the module imports fine in this interpreter)"
        )


def test_healthy_environment_reports_ok(monkeypatch) -> None:
    """Everything installed and satisfiable -> ok, no notes, no findings."""
    monkeypatch.setattr(rdeps, "_installed_version", lambda name: "9.9.9")
    monkeypatch.setattr(rdeps, "_unimportable_modules", lambda name: [])
    monkeypatch.setattr(rdeps, "_specifier_satisfied", lambda *a, **k: True)
    monkeypatch.setattr(
        rdeps, "_base_requirements", lambda root: ([rdeps._parse("click>=8.0")], "test")
    )
    monkeypatch.setattr(rdeps, "_md", _EmptyMetadata())
    report = rdeps.verify_runtime_closure(REPO_ROOT)
    assert report.ok, report.to_dict()
    assert report.missing == []


class _EmptyMetadata:
    """Stand-in for importlib.metadata: no transitive children."""

    def requires(self, name: str) -> list[str]:
        return []

    def version(self, name: str) -> str:
        return "9.9.9"


def test_missing_dependency_source_fails_closed(monkeypatch) -> None:
    """No pyproject and no installed metadata must NOT report success."""
    monkeypatch.setattr(rdeps, "_base_requirements", lambda root: ([], "unavailable"))
    report = rdeps.verify_runtime_closure(None)
    assert not report.ok
    assert "no dependency source available" in " ".join(report.notes)


# ---------------------------------------------------------------------------
# 4. The launcher gate: fail closed, before the imports
# ---------------------------------------------------------------------------
def test_launcher_runs_the_dependency_gate_before_third_party_imports() -> None:
    """Source-order pin: the gate precedes `import uvicorn`."""
    src = LAUNCHER.read_text(encoding="utf-8")
    gate_at = src.index("run_startup_dependency_gate")
    uvicorn_at = src.index("\nimport uvicorn")
    assert gate_at < uvicorn_at, "the gate must run BEFORE the third-party imports"
    assert "NSE_NO_AUTO_INSTALL" in src  # documented escape hatch


def test_gate_fails_closed_with_a_readable_diagnostic(monkeypatch, capsys) -> None:
    """Incomplete closure -> SystemExit(1), actionable text, no traceback."""
    from nexus_scalp.release import paths

    monkeypatch.setattr(paths, "is_frozen", lambda: False)
    monkeypatch.setattr(rdeps, "developer_checkout", lambda: False)
    monkeypatch.setattr(
        rdeps,
        "verify_runtime_closure",
        lambda *a, **k: rdeps.ClosureReport(
            ok=False,
            checked=38,
            source="pyproject.toml",
            missing=[rdeps.MissingDependency("click", "not installed", ["uvicorn"])],
        ),
    )
    with pytest.raises(SystemExit) as excinfo:
        rdeps.run_startup_dependency_gate(stream=sys.stdout)
    assert excinfo.value.code == 1
    out = capsys.readouterr().out
    assert "click" in out
    assert "incomplete or corrupted" in out
    assert "Traceback" not in out


def test_accelerator_companions_are_optional_but_click_is_not(monkeypatch) -> None:
    """Platform-conditional transitive deps must not be false failures.

    torch declares its nvidia-* CUDA wheels behind ``platform_system ==
    "Linux"``, so they install only on a CUDA Linux host. On a CPU-only or
    non-Linux runner their import root is unresolvable — reporting that as a
    broken install would fail CI on every non-GPU host while the real click
    gap (a mandatory transitive requirement) stayed silent.
    """

    def _fake_requires(name: str) -> list[str]:
        if name == "torch":
            return [
                "nvidia-cusparselt-cu13==0.4.0; platform_system == 'Linux'",
                "triton>=3.0; platform_system == 'Linux'",
                "nvidia-cudnn-cu13; platform_machine == 'x86_64'",
                "filelock",  # marker-free -> mandatory
            ]
        if name == "uvicorn":
            return ["click>=8", "h11>=0.8"]
        raise PackageNotFoundError(name)

    monkeypatch.setattr(rdeps._md, "requires", _fake_requires)
    # This host is not the Linux/CUDA build the markers gate on, so these
    # companions are optional HERE — but only because the marker says so.
    assert rdeps._is_optional_companion("nvidia-cusparselt-cu13", parent="torch")
    assert rdeps._is_optional_companion("triton", parent="torch")
    assert rdeps._is_optional_companion("nvidia-cudnn-cu13", parent="torch")
    # A marker-free or plain transitive requirement is NEVER downgraded.
    assert not rdeps._is_optional_companion("filelock", parent="torch")
    assert not rdeps._is_optional_companion("click", parent="uvicorn")
    assert not rdeps._is_optional_companion("h11", parent="uvicorn")


def test_real_torch_cuda_companions_classified_if_torch_installed() -> None:
    """Ground the synthetic test in the actual torch metadata when present."""
    try:
        requires = rdeps._md.requires("torch") or []
    except Exception:
        pytest.skip("torch not installed in this environment")
    cuda = [
        rdeps._parse(raw).name.lower().replace("_", "-")
        for raw in requires
        if "nvidia" in raw.lower()
    ]
    if not cuda:
        pytest.skip("torch wheel variant exposes no nvidia-* requirements")
    for name in cuda:
        assert rdeps._is_optional_companion(name, parent="torch")


def test_no_marker_evidence_stays_mandatory(monkeypatch) -> None:
    """Fail-safe: unreadable parent metadata -> the package stays required."""
    assert not rdeps._is_optional_companion("anything", parent=None)

    def _broken_requires(name: str) -> list[str]:
        raise OSError("metadata unreadable")

    monkeypatch.setattr(rdeps._md, "requires", _broken_requires)
    assert not rdeps._is_optional_companion("click", parent="uvicorn")


def test_gate_never_installs_inside_a_frozen_bundle(monkeypatch) -> None:
    """The packaged runtime ships its closure; the release build gate owns it."""
    from nexus_scalp.release import paths

    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    called: list[int] = []
    monkeypatch.setattr(rdeps, "verify_runtime_closure", lambda *a, **k: called.append(1) or None)
    monkeypatch.setattr(rdeps, "repair_runtime_closure", lambda *a, **k: called.append(2))
    rdeps.run_startup_dependency_gate(stream=sys.stdout)
    assert called == [], "the frozen path must not verify or install anything"


def test_gate_repairs_a_developer_checkout_and_reverifies(monkeypatch) -> None:
    """Developer checkout: verify -> repair -> re-verify -> start."""
    from nexus_scalp.release import paths

    monkeypatch.setattr(paths, "is_frozen", lambda: False)
    monkeypatch.setattr(rdeps, "developer_checkout", lambda: True)
    monkeypatch.delenv(rdeps.NO_AUTO_INSTALL_ENV, raising=False)
    states = iter(
        [
            rdeps.ClosureReport(
                ok=False,
                checked=38,
                source="pyproject.toml",
                missing=[rdeps.MissingDependency("click", "not installed", ["uvicorn"])],
            ),
            rdeps.ClosureReport(ok=True, checked=47, source="pyproject.toml"),
        ]
    )
    monkeypatch.setattr(rdeps, "verify_runtime_closure", lambda *a, **k: next(states))
    monkeypatch.setattr(
        rdeps, "repair_runtime_closure", lambda *a, **k: (True, "$ uv pip install -e .")
    )
    rdeps.run_startup_dependency_gate(stream=sys.stdout)  # must NOT raise


def test_gate_honours_the_no_auto_install_escape_hatch(monkeypatch) -> None:
    """NSE_NO_AUTO_INSTALL=1 -> never touches the environment, still fails."""
    from nexus_scalp.release import paths

    monkeypatch.setattr(paths, "is_frozen", lambda: False)
    monkeypatch.setattr(rdeps, "developer_checkout", lambda: True)
    monkeypatch.setenv(rdeps.NO_AUTO_INSTALL_ENV, "1")
    monkeypatch.setattr(
        rdeps,
        "verify_runtime_closure",
        lambda *a, **k: rdeps.ClosureReport(
            ok=False,
            checked=38,
            source="pyproject.toml",
            missing=[rdeps.MissingDependency("click", "not installed", ["uvicorn"])],
        ),
    )

    def _forbidden(*a, **k):
        raise AssertionError("auto-install ran despite NSE_NO_AUTO_INSTALL=1")

    monkeypatch.setattr(rdeps, "repair_runtime_closure", _forbidden)
    with pytest.raises(SystemExit):
        rdeps.run_startup_dependency_gate(stream=sys.stdout)


def test_repair_uses_the_repositorys_own_install_workflow(monkeypatch) -> None:
    """Repair is `uv pip install -e <checkout>` (pyproject is the SSoT)."""
    captured: dict[str, list[str]] = {}

    class _Proc:
        returncode = 0
        stdout = "installed"
        stderr = ""

    def fake_run(cmd, **kwargs):
        captured["cmd"] = list(cmd)
        captured["cwd"] = kwargs.get("cwd")
        return _Proc()

    monkeypatch.setattr(rdeps.shutil, "which", lambda name: "uv")
    monkeypatch.setattr(rdeps.subprocess, "run", fake_run)
    monkeypatch.setattr(
        rdeps, "verify_runtime_closure", lambda *a, **k: rdeps.ClosureReport(True, 1, "x")
    )
    ok, log = rdeps.repair_runtime_closure(REPO_ROOT)
    assert ok
    assert captured["cmd"][:4] == ["uv", "pip", "install", "--python"]
    assert "-e" in captured["cmd"] and str(REPO_ROOT) in captured["cmd"]
    assert str(captured["cwd"]) == str(REPO_ROOT)
    assert "uv pip install" in log


def test_repair_escalates_to_force_reinstall_for_unusable_installs(monkeypatch) -> None:
    """Stage 2: an installed-but-unusable distribution is force-reinstalled."""
    commands: list[list[str]] = []
    reports = iter(
        [
            rdeps.ClosureReport(
                ok=False,
                checked=47,
                source="pyproject.toml",
                missing=[
                    rdeps.MissingDependency(
                        "python-dotenv", "installed (1.2.3) but module(s) not importable: dotenv"
                    )
                ],
            ),
            rdeps.ClosureReport(ok=True, checked=47, source="pyproject.toml"),
        ]
    )

    class _Proc:
        returncode = 0
        stdout = "ok"
        stderr = ""

    monkeypatch.setattr(rdeps.shutil, "which", lambda name: "uv")
    monkeypatch.setattr(
        rdeps.subprocess, "run", lambda cmd, **k: commands.append(list(cmd)) or _Proc()
    )
    monkeypatch.setattr(rdeps, "verify_runtime_closure", lambda *a, **k: next(reports))
    ok, log = rdeps.repair_runtime_closure(REPO_ROOT)
    assert ok
    assert any("--reinstall" in c and "python-dotenv" in c for c in commands), commands
    assert "forcing a reinstall" in log


def test_repair_logs_and_reports_a_tooling_failure(monkeypatch) -> None:
    """A failed install is reported, never swallowed."""
    monkeypatch.setattr(rdeps.shutil, "which", lambda name: "uv")
    monkeypatch.setattr(
        rdeps.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
    )
    monkeypatch.setattr(
        rdeps, "verify_runtime_closure", lambda *a, **k: rdeps.ClosureReport(False, 0, "x")
    )
    ok, log = rdeps.repair_runtime_closure(REPO_ROOT)
    assert not ok
    assert "boom" in log


def test_no_module_installs_packages_merely_by_being_imported() -> None:
    """Forbidden pattern guard: no runtime module may auto-install at import time.

    The invariant is precise, not a text search: executing pip at a module's
    TOP LEVEL means merely importing it mutates the environment — the exact
    "silently repair the installation" hack that makes a broken install
    unfalsifiable. Calls inside functions are normal and correct (model
    provisioning installs pinned training deps into a separate interpreter;
    the dependency gate runs only when the launcher explicitly invokes it).

    Prose is not inspected: operator guidance such as "run pip install
    'nexus[postgres]'" is correct and must not trip this guard.
    """
    install_call = re.compile(
        r"(?:subprocess\.(?:run|Popen|call|check_call|check_output)|os\.system|os\.popen)$"
    )
    offenders: list[str] = []
    for path in (REPO_ROOT / "src" / "nexus_scalp").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        # Module-level statements only: skip every function/class body.
        top_level = [
            stmt
            for stmt in tree.body
            if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        ]
        module = ast.Module(body=top_level, type_ignores=[])
        for node in ast.walk(module):
            if not isinstance(node, ast.Call):
                continue
            func = ast.unparse(node.func) if hasattr(ast, "unparse") else ""
            if not install_call.search(func):
                continue
            argv = ast.unparse(node).lower()
            if "'pip'" in argv or '"pip"' in argv or "pip install" in argv:
                offenders.append(path.relative_to(REPO_ROOT).as_posix())
                break
    assert not offenders, (
        f"these modules execute pip at import time (a silent environment mutation): {offenders}"
    )
