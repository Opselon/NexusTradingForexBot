"""PG-BOOT-001 (Lane I) — the released bundle must actually contain psycopg.

The defect (P0, packaging):

    The release build installed ``.[dev,web,release]`` (release.yml) which
    EXCLUDES the ``postgres`` extra.  PyInstaller therefore never saw
    psycopg / psycopg_pool, and the PyInstaller command line had
    ``--collect-submodules`` / ``--hidden-import`` entries for
    uvicorn/fastapi/feedparser/MetaTrader5/torch/polars but NOTHING for
    psycopg.  The imports are runtime-only inside try/except
    (pg_planes.py, postgres_driver.py), so static analysis cannot find them
    either.

    Result: on a fresh install with provider=postgresql persisted, the first
    ``provision_domain()`` raised
    ``RuntimeError('PostgreSQL pooling requires psycopg_pool')`` and the EXE
    died at boot.  Both entry paths hit it (onedir EXE -> packaged_main ->
    cli.main -> engine_boot; onefile CLI -> cli_shim -> cli.main).

Part 1 pins the fixed contract on the BUILD SOURCES (both copies of the
PyInstaller command, the install step and the frozen dependency extra).

Part 2 is REAL BUNDLE EVIDENCE, not a code read: PyInstaller is run with the
production command's collection flags against the real entry point and the
emitted archive is opened to list the modules the bootloader would import.
That is the same analysis PyInstaller performs when emitting the bundle.

Both parts are slow (a real analysis takes ~2-4 min here) — the module is
marked ``slow`` so the fast suite is not blocked by it.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_BUILD_PS1 = _REPO_ROOT / "scripts" / "build" / "build_release.ps1"
_RELEASE_YML = _REPO_ROOT / ".github" / "workflows" / "release.yml"
_PYPROJECT = _REPO_ROOT / "pyproject.toml"

#: The driver + pool modules the frozen bundle must be able to import.
_PG_MODULES = ("psycopg", "psycopg_pool")

#: Every module the two production ``--hidden-import`` lines name.
_PG_HIDDEN = ("psycopg", "psycopg.rows", "psycopg_pool", "psycopg_pool.pool")

_ONEDIR_ENTRY = "packaged_main.py"
_CLI_ENTRY = "cli_shim.py"

pytestmark = pytest.mark.slow


# ---------------------------------------------------------------------------
# Part 1 — the build sources collect psycopg in EVERY PyInstaller invocation
# ---------------------------------------------------------------------------


def _pyinstaller_invocations(path: Path) -> list[list[str]]:
    """Split a PowerShell/CI PyInstaller command block into argv lists.

    Both files express the same command twice (onedir + onefile CLI) with
    PowerShell line-continuation backticks; the argv shape is what matters,
    not the shell syntax.  The block runs from ``PyInstaller`` to the entry
    point module — everything after that is the following PowerShell statement
    (``if ($LASTEXITCODE ...) { Fail ... }``) and never part of the command.
    """

    def _split(block: str) -> list[str]:
        return [t.strip('"') for t in block.replace("`", "").split() if t]

    text = path.read_text(encoding="utf-8")
    blocks: list[list[str]] = []
    for raw in text.split("PyInstaller"):
        argv = _split(raw)
        if not any("packaged_main" in a or "cli_shim" in a for a in argv):
            continue
        # trim the tail after the entry point: the rest is the following
        # PowerShell statement, never the command.
        for i, tok in enumerate(argv):
            if tok.endswith((_ONEDIR_ENTRY, _CLI_ENTRY)):
                argv = argv[: i + 1]
                break
        blocks.append(argv)
    return blocks


def _entries(path: Path) -> list[list[str]]:
    out = _pyinstaller_invocations(path)
    assert out, f"no PyInstaller command parsed from {path}"
    return out


@pytest.mark.parametrize("entry", [_ONEDIR_ENTRY, _CLI_ENTRY])
def test_local_build_script_collects_psycopg(entry: str) -> None:
    """The local orchestrator must collect psycopg for BOTH entry points."""
    argv = next(a for a in _entries(_BUILD_PS1) if entry in " ".join(a))
    joined = " ".join(argv)
    for mod in _PG_MODULES:
        assert f"--collect-submodules {mod}" in joined, (
            f"build_release.ps1 {entry}: --collect-submodules {mod} missing"
        )
        assert f"--hidden-import {mod}" in joined, (
            f"build_release.ps1 {entry}: --hidden-import {mod} missing"
        )


@pytest.mark.parametrize("entry", [_ONEDIR_ENTRY, _CLI_ENTRY])
def test_ci_workflow_collects_psycopg(entry: str) -> None:
    """The CI workflow is a second copy of the same command — it must match.

    Regression: fixing only one of the two makes the CI build diverge from
    the local one (the exact failure mode of the original defect).
    """
    argv = next(a for a in _entries(_RELEASE_YML) if entry in " ".join(a))
    joined = " ".join(argv)
    for mod in _PG_MODULES:
        assert f"--collect-submodules {mod}" in joined
        assert f"--hidden-import {mod}" in joined


def test_psycopg_pool_hidden_import_is_explicit() -> None:
    """psycopg_pool is imported runtime-only, so it can never be inferred.

    ``--hidden-import psycopg_pool.pool`` is what makes the pool's own
    implementation module survive tree-shaking; without it the package
    directory may be collected while the module PyInstaller's loader looks
    up first is missing.
    """
    joined = " ".join(" ".join(a) for a in _entries(_BUILD_PS1))
    assert "--hidden-import psycopg_pool.pool" in joined
    assert "--hidden-import psycopg.rows" in joined
    ci_joined = " ".join(" ".join(a) for a in _entries(_RELEASE_YML))
    assert "--hidden-import psycopg_pool.pool" in ci_joined
    assert "--hidden-import psycopg.rows" in ci_joined


def test_release_extra_pulls_psycopg() -> None:
    """The build's install step must actually install the packages.

    PyInstaller cannot freeze what was never installed. The ``release`` extra
    is what the build consumes (``.[dev,web,release]``), so psycopg must live
    in IT — adding it only to the unused ``postgres`` extra reproduces the
    defect verbatim.
    """
    text = _PYPROJECT.read_text(encoding="utf-8")
    release_block = text.split("release = [", 1)[1].split("\n]", 1)[0]
    assert "psycopg[binary]" in release_block
    assert "psycopg-pool" in release_block

    yml = _RELEASE_YML.read_text(encoding="utf-8")
    # the build job's install line (gates/test jobs legitimately stay lighter)
    build_install = next(
        line for line in yml.splitlines() if "dev,web,release" in line and "pip install" in line
    )
    assert "postgres" in build_install, (
        "the release build's install step must include the postgres extra: "
        f"got {build_install.strip()!r}"
    )


def test_local_build_script_installs_psycopg() -> None:
    """The local orchestrator cannot assume the dev venv already has psycopg."""
    text = _BUILD_PS1.read_text(encoding="utf-8")
    assert "psycopg-pool" in text, "build_release.ps1 must install psycopg-pool"
    assert "psycopg[binary]" in text, "build_release.ps1 must install psycopg[binary]"
    # and it must VERIFY the import before spending an hour on a broken bundle
    assert "import psycopg, psycopg_pool" in text


# ---------------------------------------------------------------------------
# Part 2 — the real PyInstaller bundle really carries psycopg
# ---------------------------------------------------------------------------


def _pyinstaller_available() -> bool:
    try:
        subprocess.run(
            [sys.executable, "-m", "PyInstaller", "--version"],
            capture_output=True,
            check=True,
            timeout=120,
        )
        return True
    except Exception:
        return False


_HAS_PYINSTALLER = _pyinstaller_available()


def _flags(path: Path, entry: str) -> tuple[list[str], list[str]]:
    """Read the production ``--hidden-import``/``--collect-submodules`` flags."""
    argv = next(a for a in _entries(path) if entry in " ".join(a))
    hidden: list[str] = []
    collect: list[str] = []
    for i, tok in enumerate(argv):
        if tok == "--hidden-import" and i + 1 < len(argv):
            hidden.append(argv[i + 1])
        elif tok == "--collect-submodules" and i + 1 < len(argv):
            collect.append(argv[i + 1])
    return hidden, collect


def _build_probe_bundle(workdir: Path, *, entry: str, with_flags: bool) -> Path:
    """Run PyInstaller (analysis + archive emission) for ``entry``.

    The production command's heavy ``--add-data`` payloads (torch/polars and
    the whole frontend dist) are not needed to answer "is psycopg collected":
    they only bloat the archive.  The collection flags under test are applied
    verbatim, so the archive emitted here answers the same question the
    release artifact answers.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    script = workdir / "_build.py"
    script.write_text(
        f"""
import sys, time
sys.setrecursionlimit(20000)
sys.path.insert(0, r"{_REPO_ROOT / "src"}")
import PyInstaller.__main__ as pyim

args = [
    "--noconfirm", "--clean",
    "--workpath", r"{workdir / "work"}",
    "--distpath", r"{workdir / "dist"}",
    "--specpath", r"{workdir / "spec"}",
    "--log-level", "ERROR",
    "--onefile", "--name", "probe",
    # the bundled scientific stack is irrelevant to the psycopg question and
    # only costs build time; excluding them keeps the probe under CI budget.
    "--exclude-module", "torch", "--exclude-module", "polars",
    "--exclude-module", "numpy", "--exclude-module", "pandas",
    "--exclude-module", "MetaTrader5", "--exclude-module", "matplotlib",
    "--exclude-module", "PIL", "--exclude-module", "scipy",
    "--exclude-module", "sklearn", "--exclude-module", "numba",
    r"{_REPO_ROOT / "src" / "nexus_scalp" / "release" / "{entry}"}",
]
if {with_flags!r}:
    for m in {_PG_HIDDEN!r}:
        args += ["--hidden-import", m]
    for m in {_PG_MODULES!r}:
        args += ["--collect-submodules", m]
t0 = time.time()
pyim.run(args)
print("BUILD_ELAPSED", round(time.time() - t0, 1))
""",
        encoding="utf-8",
    )
    subprocess.run(
        [sys.executable, str(script)],
        cwd=str(workdir),
        capture_output=True,
        check=True,
        timeout=900,
    )
    exe = workdir / "dist" / "probe.exe"
    assert exe.is_file(), f"PyInstaller produced no bundle in {workdir / 'dist'}"
    return exe


def _bundle_pg_modules(exe: Path) -> set[str]:
    """The psycopg-family modules present in an EMITTED PyInstaller bundle.

    Reads the artifact PyInstaller actually produced:

    * the ``CArchive`` — extension modules, native DLLs and dist-info
      entries (``psycopg_binary/_psycopg.pyd``, ``pq.pyd``, ``libpq*.dll``);
    * the ``PYZ`` member — the pure-python module table the bootloader
      resolves ``import`` against at runtime.
    """
    script = (
        "import re\n"
        "from PyInstaller.archive.readers import CArchiveReader\n"
        f"c = CArchiveReader(r'{exe}')\n"
        "found = set()\n"
        "# 1. CArchive: extension modules + native libs (the parts that make\n"
        "#    `import psycopg` work at all: _psycopg.pyd, pq.pyd, libpq).\n"
        "for name in c.toc:\n"
        "    n = str(name).replace(chr(92), '/')\n"
        "    head = n.split('/')[0]\n"
        "    if head.startswith('psycopg'):\n"
        "        found.add(head)\n"
        "    if 'psycopg' in n:\n"
        "        found.add(n)\n"
        "# 2. PYZ: the pure-python module table the bootloader resolves\n"
        "#    imports against.  Its name table is a flat string block near\n"
        "#    the end of the archive.\n"
        "for name, value in c.toc.items():\n"
        "    if value[4] != 'z':\n"
        "        continue\n"
        "    data = c.extract(str(name))\n"
        "    tail = data[-3000000:]\n"
        "    for m in re.finditer(rb'(psycopg[a-zA-Z0-9_]*(?:\\.[a-zA-Z0-9_]+)*)', tail):\n"
        "        found.add(m.group(0).decode('ascii', 'ignore'))\n"
        "print('PG_COUNT', len(found))\n"
        "for f in sorted(found):\n"
        "    print('PG:', f)\n"
    )
    res = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        check=True,
        timeout=300,
    )
    out: set[str] = set()
    for line in res.stdout.splitlines():
        if line.startswith("PG:"):
            out.add(line[len("PG:") :].strip())
    return out


@pytest.mark.skipif(not _HAS_PYINSTALLER, reason="PyInstaller not installed")
def test_real_bundle_carries_psycopg(tmp_path: Path) -> None:
    """The emitted PyInstaller bundle must carry psycopg + psycopg_pool.

    Builds a real (slimmed) onefile bundle from the production entry point
    with the production collection flags, then opens the emitted archive: a
    module present in the CArchive/PyZ is a module the bootloader can import.
    A missing psycopg here IS the P0 defect — the EXE would die on a
    provider=postgresql boot with
    ``RuntimeError('PostgreSQL pooling requires psycopg_pool')``.
    """
    hidden, collect = _flags(_BUILD_PS1, _ONEDIR_ENTRY)
    for mod in _PG_HIDDEN:
        assert mod in hidden, f"build_release.ps1 lost --hidden-import {mod}"
    for mod in _PG_MODULES:
        assert mod in collect, f"build_release.ps1 lost --collect-submodules {mod}"

    work = tmp_path / "with_flags"
    exe = _build_probe_bundle(work, entry=_ONEDIR_ENTRY, with_flags=True)
    found = _bundle_pg_modules(exe)
    assert found, "the built bundle contains no psycopg modules at all (P0 regression)"

    for mod in _PG_MODULES:
        family = {f for f in found if f == mod or f.startswith(mod + ".")}
        assert family, (
            f"{mod} is NOT in the emitted PyInstaller bundle — the EXE would "
            "die on a provider=postgresql boot"
        )
    # the pool's implementation module is what the runtime-only import pulls:
    # the package root alone is not enough.
    assert any(f == "psycopg_pool.pool" for f in found), (
        "psycopg_pool.pool missing — the runtime-only pool import would fail"
    )
    assert "psycopg.rows" in found, "psycopg.rows missing (the row factory the pool uses)"
    # the C extension that makes `import psycopg` work without a compiler
    assert any(f.startswith("psycopg_binary") for f in found), (
        "psycopg_binary (the C extension) is missing — psycopg would be importable in name only"
    )


@pytest.mark.skipif(not _HAS_PYINSTALLER, reason="PyInstaller not installed")
def test_bundle_without_flags_drops_the_pool(tmp_path: Path) -> None:
    """Negative control: the collection flags are load-bearing.

    Without the production ``--collect-submodules`` / ``--hidden-import``
    set, the bundle the entry point alone implies carries no psycopg_pool:
    the pool is imported runtime-only inside try/except, so neither the
    graph nor a hook can infer it.  If this ever stops being true the flags
    have become redundant; until then removing them reopens the P0 defect.
    """
    exe = _build_probe_bundle(tmp_path / "no_flags", entry=_ONEDIR_ENTRY, with_flags=False)
    found = _bundle_pg_modules(exe)

    assert not any(f == "psycopg_pool" or f.startswith("psycopg_pool.") for f in found), (
        "psycopg_pool is collected WITHOUT the production flags — the flags "
        "are no longer the thing making the pool survive the freeze"
    )


# ---------------------------------------------------------------------------
# Part 3 — the driver is importable in THIS environment (the runtime half)
# ---------------------------------------------------------------------------


def test_psycopg_pool_importable_in_dev_env() -> None:
    """The dev venv the release build runs from must have the pool.

    ``build_release.ps1`` now asserts this before it spends an hour on a
    bundle; mirror that here so a dev environment without the postgres extra
    fails loudly at test time rather than at release time.
    """
    import psycopg
    import psycopg_pool

    from nexus_scalp.database.config import DatabaseConfig
    from nexus_scalp.database.drivers import driver_available

    assert driver_available(DatabaseConfig.for_postgres(domain="audit")) is True
