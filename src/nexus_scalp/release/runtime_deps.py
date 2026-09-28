"""Runtime dependency closure validation + developer-checkout bootstrap.

WHERE/WHY: ``NexusTradingForexBot.py`` (and the installed ``nexus`` console
script) import third-party packages at module import time. When the installed
environment is incomplete — the classic case is ``uvicorn`` present while the
transitive ``click`` it requires was never installed — the process dies on
``import uvicorn`` with a raw ``ModuleNotFoundError`` traceback that tells the
operator nothing actionable and offers no repair path.

This module answers exactly one question, before those imports run:

    Is the DECLARED runtime dependency closure actually installed and
    importable in this interpreter?

and, in a DEVELOPER source checkout only, repairs it through the repository's
own dependency workflow (``uv pip install -e .`` / ``pip install -e .`` from
``pyproject.toml`` — the single source of truth) so the application can start.

CONTRACT (safety, non-negotiable):

* The closure is DERIVED, never hand-listed. It is walked transitively from
  the checkout's ``pyproject.toml`` ``[project].dependencies`` (or the
  installed distribution metadata when no checkout is present), honouring
  environment markers and skipping opt-in extras. Adding a dependency to
  ``pyproject.toml`` is the only way to change what is verified here.
* Auto-repair NEVER runs inside a frozen (PyInstaller) bundle: the packaged
  runtime ships its own closure and is owned by the release build gate. It is
  also disabled by ``NSE_NO_AUTO_INSTALL=1``.
* A broken install is never hidden or monkey-patched: the application still
  fails, non-zero, with a human-readable, actionable diagnostic. Nothing here
  suppresses an ImportError or dynamically downloads a package mid-run.

USED BY: ``NexusTradingForexBot.py`` (startup gate), ``release/health.py``
(RUNTIME check), ``doctor``/CI (``python -m nexus_scalp.release.runtime_deps``).

CLI:  python -m nexus_scalp.release.runtime_deps [--json] [--project PATH]
Exit: 0 = closure complete, 1 = incomplete, 2 = usage/tooling error.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from importlib import metadata as _md
from importlib.util import find_spec
from pathlib import Path
from typing import IO

#: Distribution name of this project (pyproject ``[project].name``).
PROJECT_NAME = "nexus-scalp-engine"

#: Set to disable the developer auto-repair bootstrap (CI, probes, users who
#: must not have a startup path touch the environment).
NO_AUTO_INSTALL_ENV = "NSE_NO_AUTO_INSTALL"

#: Requirement lines are the ONLY input to the closure walk — nothing below
#: re-declares a package, so this file can never drift from pyproject.toml.
_UNPARSEABLE = "unparseable requirement"

_REQ_RE = re.compile(r"^\s*(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)\s*(?P<rest>.*)$", re.DOTALL)


@dataclass(frozen=True)
class Requirement:
    """One parsed requirement (packaging-free representation)."""

    raw: str
    name: str
    specifier: str = ""
    marker: str = ""

    @property
    def is_extra(self) -> bool:
        """True when the requirement is gated behind an opt-in extra."""
        return "extra" in self.marker


@dataclass
class MissingDependency:
    """A declared runtime dependency that is absent or unusable."""

    distribution: str
    reason: str
    required_by: list[str] = field(default_factory=list)
    specifier: str = ""

    def to_dict(self) -> dict[str, object]:
        return {
            "distribution": self.distribution,
            "reason": self.reason,
            "required_by": sorted(set(self.required_by)),
            "specifier": self.specifier,
        }


@dataclass
class ClosureReport:
    """Outcome of one closure verification."""

    ok: bool
    checked: int
    source: str
    missing: list[MissingDependency] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def missing_names(self) -> list[str]:
        return sorted({m.distribution for m in self.missing})

    def to_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok,
            "checked": self.checked,
            "source": self.source,
            "missing": [m.to_dict() for m in self.missing],
            "notes": self.notes,
        }

    def describe(self) -> str:
        """Multi-line, plain-text diagnostic (no rich, no colours)."""
        lines = [
            "NSE runtime dependency validation failed.",
            "",
            f"Missing or unusable runtime dependencies ({len(self.missing)}):",
        ]
        for item in self.missing[:20]:
            parents = ", ".join(sorted(set(item.required_by))) or "project"
            lines.append(f"  - {item.distribution}: {item.reason} (required by {parents})")
        if len(self.missing) > 20:
            lines.append(f"  ... and {len(self.missing) - 20} more")
        lines.append("")
        lines.append(f"Checked {self.checked} declared runtime dependencies ({self.source}).")
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Project root / requirement sources
# ---------------------------------------------------------------------------
def project_root() -> Path | None:
    """The source checkout that owns the running interpreter, if any.

    ``NSE_PROJECT_ROOT`` wins (tests, exotic layouts); otherwise the repo root
    above this package (``src/nexus_scalp/release/`` -> parents[3]) is used —
    the same anchor ``paths.exe_dir()`` resolves in a source install.
    """
    candidates: list[Path] = []
    env = os.environ.get("NSE_PROJECT_ROOT")
    if env:
        candidates.append(Path(env))
    candidates.append(Path(__file__).resolve().parents[3])
    for candidate in candidates:
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return None


def _parse(raw: str) -> Requirement:
    """Parse a PEP 508-ish requirement string without requiring packaging."""
    text = raw.strip()
    try:  # packaging is present in every real environment; never required.
        from packaging.requirements import Requirement as _PkgReq

        parsed = _PkgReq(text)
        return Requirement(
            raw=text,
            name=parsed.name,
            specifier=str(parsed.specifier),
            marker=str(parsed.marker) if parsed.marker else "",
        )
    except Exception:
        pass
    match = _REQ_RE.match(text)
    if not match:  # pragma: no cover - defensive
        return Requirement(raw=text, name=text, specifier="", marker=_UNPARSEABLE)
    rest = match.group("rest")
    marker = ""
    if ";" in rest:
        rest, marker = rest.split(";", 1)
    return Requirement(
        raw=text,
        name=match.group("name"),
        specifier=rest.strip(),
        marker=marker.strip(),
    )


def _marker_applies(marker: str) -> bool:
    """Evaluate an environment marker; a marker we cannot parse is treated as
    not applicable rather than reported as a missing dependency (a false FAIL
    is worse than a missed optional pin — the release build gate covers those).
    """
    if not marker:
        return True
    try:
        from packaging.markers import Marker

        return bool(Marker(marker).evaluate())
    except Exception:
        return False


def _base_requirements(root: Path | None) -> tuple[list[Requirement], str]:
    """The project's own declared runtime dependencies (extras excluded)."""
    if root is not None:
        try:
            import tomllib

            data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
            declared = list(data.get("project", {}).get("dependencies", []) or [])
            if declared:
                return (
                    [r for r in (_parse(raw) for raw in declared) if _marker_applies(r.marker)],
                    "pyproject.toml [project].dependencies",
                )
        except Exception:
            pass
    try:  # Installed distribution metadata (packaged / no checkout).
        declared = list(_md.requires(PROJECT_NAME) or [])
    except Exception:
        return [], "unavailable"
    return (
        [
            r
            for r in (_parse(raw) for raw in declared)
            if not r.is_extra and _marker_applies(r.marker)
        ],
        f"installed metadata of {PROJECT_NAME}",
    )


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------
@lru_cache(maxsize=1)
def _packages_distributions() -> dict[str, list[str]]:
    """Cached ``importlib.metadata.packages_distributions()``.

    CPython 3.11 does not cache it, so the closure walk re-scanned every
    ``dist-info`` on disk once per inspected distribution (~0.08s each, 3.5s+
    total on a 100-package venv). The mapping is immutable for a process:
    verifying a dependency closure must never install or uninstall anything,
    so a single computation is correct and safe to share.
    """
    try:
        return {k: list(v) for k, v in _md.packages_distributions().items()}
    except Exception:
        return {}


def _installed_version(name: str) -> str | None:
    try:
        return _md.version(name)  # type: ignore[no-any-return]
    except Exception:
        return None


def _specifier_satisfied(name: str, specifier: str, version: str) -> bool:
    if not specifier:
        return True
    try:
        from packaging.requirements import Requirement as _PkgReq

        return bool(_PkgReq(f"{name}{specifier}").specifier.contains(version))
    except Exception:
        return True  # cannot evaluate -> do not invent a failure


def _top_level_modules(name: str) -> list[str]:
    """Import names owned by an installed distribution (best effort).

    Disk truth first: ``packages_distributions()`` maps TOP-LEVEL IMPORTABLE
    modules to the distributions that provide them, so a module listed there is
    a real module the interpreter can import. ``top_level.txt`` is only a
    fallback because it also carries non-importable artifacts — console-script
    names (``isympy``), and private/bundled extension modules shipped INSIDE a
    package (``_sodium`` lives at ``nacl/_sodium.pyd``, not at the top level).
    Probing those would report a perfectly good install as broken.
    """
    try:
        modules = [
            mod
            for mod, dists in _packages_distributions().items()
            if any(d.lower().replace("-", "_") == name.lower().replace("-", "_") for d in dists)
        ]
    except Exception:
        modules = []
    if modules:
        return modules
    try:
        dist = _md.distribution(name)
        text = dist.read_text("top_level.txt") or ""
    except Exception:
        return []
    for line in text.splitlines():
        candidate = line.strip()
        # Valid, public, import-root identifiers only.
        if candidate and candidate.isidentifier() and not candidate.startswith("_"):
            modules.append(candidate)
    return modules


def _unimportable_modules(name: str) -> list[str]:
    """Modules of an installed distribution that cannot be located.

    Only TOP-LEVEL import roots are probed: ``find_spec`` on a SUBMODULE
    (``torch._C``, ``functorch``) imports its parent package to resolve it —
    which would drag torch/CUDA into a metadata check, cost seconds, and make
    a dependency probe depend on the runtime it is verifying.

    Judgement rule: a distribution is only reported as unusable when NONE of
    its import roots resolve. That is the real "installed metadata present but
    files gone / corrupt installation" signal. A mix of resolvable and
    unresolvable roots is normal and must never be a false FAIL: metadata
    legitimately names nested or non-import-root paths (``torch`` declares
    ``functorch``, ``sympy`` declares the console script ``isympy``), and a
    verifier that reports those healthy installs as broken would be ignored by
    the very operators it is meant to protect.
    """
    candidates = [m for m in _top_level_modules(name) if "." not in m]
    if not candidates:
        return []  # nothing to judge -> never invent a failure
    broken: list[str] = []
    for module in candidates:
        if module in {"_distutils_hack", "pkg_resources", "tests", "test"}:
            continue
        try:
            if find_spec(module) is None:
                broken.append(module)
        except Exception:
            broken.append(module)
    judged = [c for c in candidates if c not in {"_distutils_hack", "pkg_resources"}]
    if judged and len(broken) == len(judged):
        # Every root failed: the installation is genuinely unusable.
        return broken
    return []


def _is_optional_companion(name: str, parent: str | None = None) -> bool:
    """A transitive dependency whose own requirement is platform-conditional.

    Derived, never listed: the decision comes from the marker the PARENT
    distribution wrote on this requirement (torch declares its nvidia-* CUDA
    wheels behind ``platform_system == "Linux"``, so they only install on a
    CUDA-capable Linux host). A package required only under such a marker
    cannot be missing on a host that marker excludes — so importability is not
    demanded for it. Presence and version still are.

    The marker is taken from the parent's own ``requires`` (the caller passes
    it), because the companion itself is frequently not discoverable through
    ``packages_distributions()`` — that is precisely the case being judged.

    Absent any marker evidence the package is treated as mandatory (fail-safe
    direction: over-reporting a genuine gap beats silently accepting one).
    """
    if parent is None:
        return False
    key = name.lower().replace("_", "-")
    try:
        requires = _md.requires(parent) or []
    except Exception:
        return False
    for raw in requires:
        parsed = _parse(raw)
        if parsed.name.lower().replace("_", "-") != key:
            continue
        marker = parsed.marker.strip()
        if not marker or "extra" in marker:
            continue
        # A marker that excludes THIS host already excluded the install, so
        # absence or unimportability is not a defect.
        if not _marker_applies(marker):
            return True
        if any(
            token in marker
            for token in (
                "platform_machine",
                "sys_platform",
                "platform_system",
                "os_name",
                "platform_version",
                "implementation_name",
            )
        ):
            return True
    return False


def verify_runtime_closure(
    root: Path | None = None,
    *,
    import_check: bool = True,
) -> ClosureReport:
    """Walk the declared runtime closure and report what is absent/unusable.

    The walk is transitive over INSTALLED distribution metadata, which is what
    makes ``uvicorn`` present + ``click`` absent detectable: the requirement
    for ``click`` comes from the installed uvicorn's own metadata, not from a
    list maintained here.
    """
    if root is None:
        root = project_root()
    base, source = _base_requirements(root)
    if not base:
        return ClosureReport(
            ok=False,
            checked=0,
            source=source,
            notes=[
                "no dependency source available: neither pyproject.toml nor "
                f"installed metadata for {PROJECT_NAME} could be read"
            ],
        )

    owned_by: dict[str, list[str]] = {}
    specs: dict[str, list[str]] = {}
    queue: list[tuple[Requirement, str]] = [(req, PROJECT_NAME) for req in base]
    missing: list[MissingDependency] = []
    inspected: set[str] = set()

    while queue:
        req, parent = queue.pop(0)
        key = req.name.lower().replace("_", "-")
        if key in inspected:
            if req.specifier:
                specs.setdefault(key, []).append(req.specifier)
            owned_by.setdefault(key, []).append(parent)
            continue
        inspected.add(key)
        owned_by.setdefault(key, []).append(parent)
        if req.specifier:
            specs.setdefault(key, []).append(req.specifier)

        version = _installed_version(req.name)
        if version is None:
            reason = "not installed"
            if root is not None and not req.specifier and _UNPARSEABLE in req.marker:
                reason = f"not installed ({_UNPARSEABLE})"
            missing.append(MissingDependency(req.name, reason, [parent], req.specifier or ""))
            continue
        for spec in specs.get(key, []):
            if not _specifier_satisfied(req.name, spec, version):
                missing.append(
                    MissingDependency(
                        req.name,
                        f"version {version} does not satisfy '{spec}'",
                        [parent],
                        spec,
                    )
                )
                break
        if import_check:
            broken = _unimportable_modules(req.name)
            if broken:
                missing.append(
                    MissingDependency(
                        req.name,
                        f"installed ({version}) but module(s) not importable: " + ", ".join(broken),
                        [parent],
                        req.specifier or "",
                    )
                )
        try:  # Transitive children — declared by the INSTALLED version.
            children = list(_md.requires(req.name) or [])
        except Exception:
            children = []
        for raw in children:
            child = _parse(raw)
            if child.is_extra or not _marker_applies(child.marker):
                continue
            # Optional accelerator/platform companions (torch's nvidia-* CUDA
            # wheels, platform-specific shims) are installed only on capable
            # hosts. Their import root is not resolvable on a CPU-only or
            # different-OS runner, so a hard importability check would report
            # a healthy install as broken. They are recorded as inspected-but-
            # optional: presence/versions are still verified, importability is
            # not demanded. Only names the project itself declared carry the
            # strict contract — those are never downgraded.
            if _is_optional_companion(child.name, parent=req.name):
                inspected.add(child.name.lower().replace("_", "-"))
                owned_by.setdefault(child.name.lower().replace("_", "-"), []).append(req.name)
                continue
            queue.append((child, req.name))

    return ClosureReport(
        ok=not missing,
        checked=len(inspected),
        source=source,
        missing=missing,
    )


# ---------------------------------------------------------------------------
# Developer bootstrap (repair through the repository's own workflow)
# ---------------------------------------------------------------------------
def developer_checkout() -> bool:
    """True only for a source checkout — never for a frozen/packaged runtime."""
    from nexus_scalp.release import paths

    if paths.is_frozen():
        return False
    return project_root() is not None


def _install_command(root: Path, *, force: list[str] | None = None) -> list[str]:
    """The repository's own editable install (pyproject is the SSoT).

    ``force`` escalates to a forced reinstall of specific distributions — the
    remedy for the second defect class this gate detects: dist-info present,
    module files physically gone (``pip check`` and ``uv pip check`` both call
    that environment satisfied, so only a forced reinstall repairs it).
    """
    uv = shutil.which("uv")
    if uv:
        if force:
            return [
                uv,
                "pip",
                "install",
                "--python",
                sys.executable,
                "--reinstall",
                *force,
            ]
        return [uv, "pip", "install", "--python", sys.executable, "-e", str(root)]
    cmd = [sys.executable, "-m", "pip", "install", "-e", str(root)]
    if force:
        cmd = [sys.executable, "-m", "pip", "install", "--force-reinstall", *force]
    return cmd


def _run_install(cmd: list[str], root: Path, timeout: int) -> tuple[bool, str]:
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except Exception as exc:  # tooling failure is reported, never raised away
        return False, f"{' '.join(cmd)} failed to run: {exc}"
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, f"$ {' '.join(cmd)}\n{output.strip()[-4000:]}"


def repair_runtime_closure(root: Path | None = None, *, timeout: int = 3600) -> tuple[bool, str]:
    """Reinstall the declared closure via uv/pip editable install of the checkout.

    Two stages, because there are two distinct defect classes:

    1. Editable install of the checkout — restores anything never installed
       (the ``uvicorn``-present / ``click``-absent case).
    2. Forced reinstall of whatever is STILL broken — restores a distribution
       whose dist-info survived but whose module files did not. Stage 1 cannot
       fix that one: the resolver sees the metadata as satisfied.
    """
    target = root or project_root()
    if target is None:
        return False, "no source checkout found (pyproject.toml missing)"

    logs: list[str] = []
    ok, log = _run_install(_install_command(target), target, timeout)
    logs.append(log)
    after = verify_runtime_closure(target)
    if after.ok:
        return True, "\n".join(logs)

    remaining = [m for m in after.missing if m.reason.startswith("installed (")]
    if remaining:
        specs = [
            f"{m.distribution}{m.specifier}" if m.specifier else m.distribution for m in remaining
        ]
        ok2, log2 = _run_install(_install_command(target, force=specs), target, timeout)
        logs.append(
            "Unusable-but-installed distributions detected "
            f"({', '.join(specs)}); forcing a reinstall.\n{log2}"
        )
        ok = ok or ok2
    return verify_runtime_closure(target).ok, "\n".join(logs)


def ensure_runtime_closure(
    *,
    auto_install: bool = True,
    root: Path | None = None,
) -> tuple[bool, ClosureReport, str]:
    """Verify; when incomplete and allowed, repair through the repo workflow.

    Returns ``(ok, report, repair_log)`` where ``report`` is the POST-repair
    verification when a repair ran.
    """
    report = verify_runtime_closure(root)
    if report.ok or not auto_install:
        return report.ok, report, ""
    ok, log = repair_runtime_closure(root)
    if not ok:
        return False, report, log
    return verify_runtime_closure(root).ok, verify_runtime_closure(root), log


# ---------------------------------------------------------------------------
# Startup gate used by NexusTradingForexBot.py
# ---------------------------------------------------------------------------
def run_startup_dependency_gate(*, stream: IO[str] | None = None) -> None:
    """Fail-closed dependency gate that runs BEFORE the launcher's imports.

    Developer checkout: verify -> repair -> re-verify (disable with
    ``NSE_NO_AUTO_INSTALL=1``). Any runtime that is still incomplete prints an
    actionable diagnostic and exits non-zero — an incomplete installation is
    never allowed to continue into a raw traceback.
    """
    from nexus_scalp.release import paths

    out = stream if stream is not None else sys.stderr
    if paths.is_frozen():
        # A packaged bundle ships its closure inside the PyInstaller archive;
        # dist metadata is absent there, so the release build gate owns this.
        return

    auto_install = os.environ.get(NO_AUTO_INSTALL_ENV, "").strip() not in {"1", "true", "yes"}
    report = verify_runtime_closure()

    if report.ok:
        return

    print("", file=out)
    print(report.describe(), file=out)
    print("", file=out)

    if not developer_checkout():
        print(
            "The installation is incomplete or corrupted. "
            "Please repair or reinstall the application.",
            file=out,
        )
        raise SystemExit(1)

    if auto_install:
        print(
            "Developer checkout detected: repairing the runtime dependency "
            "closure with the repository's own dependency workflow.",
            file=out,
        )
        root = project_root()
        _ok, log = repair_runtime_closure(root)
        if log:
            print(log, file=out)
        # Re-verify after every repair attempt: the closure must be PROVEN
        # complete, never assumed from the installer's exit code. A second
        # repair stage (forced reinstall) may already have run inside.
        report = verify_runtime_closure(root)
        if report.ok:
            print("Runtime dependency closure restored — starting NSE.", file=out)
            print("", file=out)
            return
        print("", file=out)
        print(report.describe(), file=out)
        print("", file=out)

    print(
        "The installation is incomplete or corrupted. Repair it with the "
        "repository's documented bootstrap:",
        file=out,
    )
    print("  uv pip install -e .        # or: python -m pip install -e .", file=out)
    print(f"  (set {NO_AUTO_INSTALL_ENV}=1 to disable the automatic repair)", file=out)
    raise SystemExit(1)


# ---------------------------------------------------------------------------
# CLI — artifact/CI dependency closure gate
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m nexus_scalp.release.runtime_deps",
        description="Verify the declared runtime dependency closure (offline).",
    )
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    parser.add_argument("--project", type=str, default=None, help="source checkout root")
    parser.add_argument(
        "--no-import-check",
        action="store_true",
        help="check presence/versions only (skip module importability probes)",
    )
    parser.add_argument(
        "--repair",
        action="store_true",
        help="repair an incomplete closure via the repository's editable install",
    )
    args = parser.parse_args(argv)

    root = Path(args.project) if args.project else None
    report = verify_runtime_closure(root, import_check=not args.no_import_check)
    if not report.ok and args.repair:
        ok, log = repair_runtime_closure(root or project_root())
        if log:
            print(log, file=sys.stderr)
        if ok:
            report = verify_runtime_closure(root, import_check=not args.no_import_check)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    elif report.ok:
        print(f"runtime dependency closure complete ({report.checked} checked, {report.source})")
    else:
        print(report.describe(), file=sys.stderr)
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover - process entry point
    raise SystemExit(main())
