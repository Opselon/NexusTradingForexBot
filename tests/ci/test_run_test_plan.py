"""Regression tests for scripts/ci/run_test_plan.py.

The cold-build path of ensure_native_engine is the one CI actually takes
on a fresh runner, and it is the one local runs never exercise (the
persisted binary under ~/.nse-native verifies and returns early).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts" / "ci"))

import run_test_plan as rtp

REPO = Path(__file__).resolve().parents[2]
HEAD = "a" * 40


# ----------------------------------------------------------------------
# Shared scaffolding: one engine build per module + scratch repos.
# ----------------------------------------------------------------------
@pytest.fixture(scope="module")
def engine_binary(tmp_path_factory):
    """Build the native engine once from a copy of the sources."""
    if shutil.which("cmake") is None:
        pytest.skip("cmake unavailable")
    d = tmp_path_factory.mktemp("nse-engine")
    shutil.copytree(
        REPO / "scripts" / "ci" / "native",
        d / "scripts" / "ci" / "native",
        ignore=shutil.ignore_patterns("build", "artifacts"),
    )
    binary = rtp.ensure_native_engine(str(d), str(d / "persist"))
    assert Path(binary).is_file()
    assert rtp.engine_version(binary) == rtp.ENGINE_VERSION
    assert rtp.selftest_ok(binary)
    return binary


def _write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _scratch_repo(root: Path, files: dict[str, str]) -> Path:
    """Minimal NSE-shaped repo: ast_extract.py is copied in so the
    production extraction helper works against the scratch root."""
    for rel, text in files.items():
        _write(root, rel, text)
    (root / "scripts" / "ci").mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        REPO / "scripts" / "ci" / "ast_extract.py", root / "scripts" / "ci" / "ast_extract.py"
    )
    return root


def _extract(root: Path) -> Path:
    meta_path = root / "meta.json"
    meta = rtp.extract_ast_metadata(str(root), str(meta_path))
    assert meta is not None
    return meta_path


def _plan_ids(plan: dict) -> list[str]:
    return [t["id"] for t in plan["selected_tests"]]


def _git(root: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@t", *args],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def test_ensure_native_engine_builds_from_cold_persist_dir():
    """Regression (2026-11-09): the cmake configure call returned a
    (proc, elapsed) tuple and the code read .returncode off the tuple —
    AttributeError — so the OS lane's SKEW step died before planning.
    It hid locally because the warm persisted binary short-circuits the
    build branch entirely. This test forces the cold path."""
    if shutil.which("cmake") is None:
        pytest.skip("cmake unavailable")
    repo = Path(__file__).resolve().parents[2]
    native_src = repo / "scripts" / "ci" / "native"
    assert (native_src / "CMakeLists.txt").is_file()
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        # Scratch repo root holding a copy of the native sources only;
        # the persist dir starts empty, so no binary can short-circuit.
        # Exclude build/ + artifacts/: a copied CMakeCache.txt points at
        # the original source dir and cmake refuses to reconfigure.
        shutil.copytree(
            native_src,
            d / "scripts" / "ci" / "native",
            ignore=shutil.ignore_patterns("build", "artifacts"),
        )
        persist = d / "persist"
        binary = rtp.ensure_native_engine(str(d), str(persist))
        assert Path(binary).is_file()
        assert rtp.engine_version(binary) == rtp.ENGINE_VERSION
        assert rtp.selftest_ok(binary)


# ----------------------------------------------------------------------
# Task 1: the AST metadata path is production (not the line scanner).
# ----------------------------------------------------------------------
def test_c_plan_metadata_selects_fixture_only_dependency(tmp_path, engine_binary):
    """A test that depends on a module ONLY through a conftest fixture is
    selected via the metadata path and NOT via the old line-scan path.
    This is the discriminating proof that production planning uses the
    AST metadata graph."""
    repo = _scratch_repo(
        tmp_path,
        {
            "src/nexus_scalp/leaf.py": "def value():\n    return True\n",
            "tests/unit/conftest.py": (
                "import pytest\nfrom nexus_scalp.leaf import value\n\n\n"
                "@pytest.fixture\ndef leaf_fx():\n    return value()\n"
            ),
            "tests/unit/test_via_fixture.py": (
                "def test_via_fixture(leaf_fx):\n    assert leaf_fx\n"
            ),
            "tests/unit/test_direct.py": (
                "from nexus_scalp.leaf import value\n\n\ndef test_direct():\n    assert value()\n"
            ),
            "tests/critical_suite.txt": "tests/unit/test_direct.py\n",
            "tests/fast_suite.txt": "tests/unit/test_via_fixture.py\n",
        },
    )
    meta = _extract(repo)
    changed = ["src/nexus_scalp/leaf.py"]

    with_meta = rtp.c_plan(
        engine_binary, str(repo), changed, HEAD, "pull_request", False, metadata_path=str(meta)
    )
    assert with_meta["metadata_used"] is True
    assert with_meta["selection_mode"] == "targeted"
    assert with_meta["fallback_reason"] is None
    assert "tests/unit/test_via_fixture.py" in _plan_ids(with_meta)
    assert "tests/unit/test_direct.py" in _plan_ids(with_meta)

    without_meta = rtp.c_plan(engine_binary, str(repo), changed, HEAD, "pull_request", False)
    assert without_meta["metadata_used"] is False
    assert "tests/unit/test_via_fixture.py" not in _plan_ids(without_meta)
    assert "tests/unit/test_direct.py" in _plan_ids(without_meta)


def test_c_plan_metadata_selects_dynamic_import_only_dependency(tmp_path, engine_binary):
    """A test that depends on a module ONLY through importlib.import_module
    (literal target) is selected via metadata and not via line-scan."""
    repo = _scratch_repo(
        tmp_path,
        {
            "src/nexus_scalp/leaf.py": "def value():\n    return True\n",
            "src/nexus_scalp/loader.py": (
                "import importlib\n\n\n"
                "def load():\n"
                '    return importlib.import_module("nexus_scalp.leaf")\n'
            ),
            "tests/unit/test_loader.py": (
                "from nexus_scalp.loader import load\n\n\n"
                "def test_loader():\n    assert load().value()\n"
            ),
            "tests/critical_suite.txt": "tests/unit/test_loader.py\n",
            "tests/fast_suite.txt": "",
        },
    )
    meta = _extract(repo)
    changed = ["src/nexus_scalp/leaf.py"]
    with_meta = rtp.c_plan(
        engine_binary, str(repo), changed, HEAD, "pull_request", False, metadata_path=str(meta)
    )
    assert with_meta["selection_mode"] == "targeted"
    assert with_meta["fallback_reason"] is None
    assert "tests/unit/test_loader.py" in _plan_ids(with_meta)
    assert "static_dependency" in next(
        t["reason"] for t in with_meta["selected_tests"] if t["id"] == "tests/unit/test_loader.py"
    )
    # Without metadata the loader's dynamic import is invisible, so the
    # leaf has no mapping at all and the plan expands conservatively.
    without_meta = rtp.c_plan(engine_binary, str(repo), changed, HEAD, "pull_request", False)
    assert without_meta["selection_mode"] == "expanded"
    assert without_meta["fallback_reason"] is not None
    assert "missing test mapping" in without_meta["fallback_reason"]


def test_c_plan_metadata_init_relative_import_no_expansion(tmp_path, engine_binary):
    """Regression: `from .sub import x` inside a package __init__.py used
    to resolve against the PARENT package (nexus_scalp.paths instead of
    nexus_scalp.release.paths) and flipped the whole metadata graph to
    relative_unresolved, so every metadata plan expanded. The plan must
    stay targeted with no fallback."""
    repo = _scratch_repo(
        tmp_path,
        {
            "src/nexus_scalp/pkg/__init__.py": "from .sub import helper\n",
            "src/nexus_scalp/pkg/sub.py": "def helper():\n    return True\n",
            "tests/unit/test_pkg.py": (
                "from nexus_scalp.pkg.sub import helper\n\n\n"
                "def test_helper():\n    assert helper()\n"
            ),
            "tests/critical_suite.txt": "tests/unit/test_pkg.py\n",
            "tests/fast_suite.txt": "",
        },
    )
    meta = _extract(repo)
    plan = rtp.c_plan(
        engine_binary,
        str(repo),
        ["src/nexus_scalp/pkg/sub.py"],
        HEAD,
        "pull_request",
        False,
        metadata_path=str(meta),
    )
    assert plan["selection_mode"] == "targeted"
    assert plan["fallback_reason"] is None
    assert "tests/unit/test_pkg.py" in _plan_ids(plan)


def test_c_plan_rejects_malformed_metadata(tmp_path, engine_binary):
    """Malformed metadata fails closed: the engine exits non-zero and
    c_plan raises instead of silently planning from the line scanner."""
    repo = _scratch_repo(tmp_path, {"tests/critical_suite.txt": "", "tests/fast_suite.txt": ""})
    bad = repo / "bad.json"
    bad.write_text('{"not": "json', encoding="utf-8")
    with pytest.raises(RuntimeError):
        rtp.c_plan(
            engine_binary,
            str(repo),
            ["src/nexus_scalp/leaf.py"],
            HEAD,
            "pull_request",
            False,
            metadata_path=str(bad),
        )


# ----------------------------------------------------------------------
# Task 2: hub bounding — bounded one-hop, capped at 25 direct tests.
# ----------------------------------------------------------------------
def test_hub_one_hop_capped(tmp_path, engine_binary):
    """The audit's hub scenario, at production scale: a small hub (27
    importers, 3 of them tests) must contribute its direct test importers
    via the bounded one-hop; an oversized hub (26 direct tests) must stay
    a hard boundary; a test reaching a hub only via a non-test module
    must stay unselected. Mode stays targeted throughout."""
    files: dict[str, str] = {
        "src/nexus_scalp/leaf_a.py": "def a():\n    return True\n",
        "src/nexus_scalp/leaf_b.py": "def b():\n    return True\n",
        "src/nexus_scalp/hub_small.py": "from nexus_scalp.leaf_a import a\n",
        "src/nexus_scalp/hub_big.py": "from nexus_scalp.leaf_b import b\n",
        "src/nexus_scalp/mid.py": "from nexus_scalp.hub_small import a\n",
        "tests/unit/test_direct_a.py": "from nexus_scalp.leaf_a import a\n\n\ndef test_a():\n    assert a()\n",
        "tests/unit/test_direct_b.py": "from nexus_scalp.leaf_b import b\n\n\ndef test_b():\n    assert b()\n",
        "tests/unit/test_via_mid.py": "from nexus_scalp.mid import a\n\n\ndef test_via_mid():\n    assert a()\n",
        "tests/critical_suite.txt": "",
        "tests/fast_suite.txt": "",
    }
    # hub_small: 24 stub modules + 3 tests = 27 importers (a hub), but
    # only 3 direct tests -> one-hop applies.
    for i in range(24):
        files[f"src/nexus_scalp/stub_{i:02d}.py"] = "from nexus_scalp.hub_small import a\n"
    for i in range(3):
        files[f"tests/integration/test_small_{i}.py"] = (
            f"from nexus_scalp.hub_small import a\n\n\ndef test_small_{i}():\n    assert a()\n"
        )
    # hub_big: 26 direct tests -> one-hop capped out.
    for i in range(26):
        files[f"tests/integration/test_big_{i:02d}.py"] = (
            f"from nexus_scalp.hub_big import b\n\n\ndef test_big_{i:02d}():\n    assert b()\n"
        )
    repo = _scratch_repo(tmp_path, files)
    meta = _extract(repo)
    plan = rtp.c_plan(
        engine_binary,
        str(repo),
        ["src/nexus_scalp/leaf_a.py", "src/nexus_scalp/leaf_b.py"],
        HEAD,
        "pull_request",
        False,
        metadata_path=str(meta),
    )
    assert plan["selection_mode"] == "targeted"
    assert plan["fallback_reason"] is None
    selected = set(_plan_ids(plan))
    assert "tests/unit/test_direct_a.py" in selected
    assert "tests/unit/test_direct_b.py" in selected
    for i in range(3):
        assert f"tests/integration/test_small_{i}.py" in selected
    assert "tests/unit/test_via_mid.py" not in selected
    for i in range(26):
        assert f"tests/integration/test_big_{i:02d}.py" not in selected


# ----------------------------------------------------------------------
# Task 3: pyproject.toml semantic scope + canonical invalidation.
# ----------------------------------------------------------------------
PYPROJECT_BASE = """\
[build-system]
requires = ["setuptools>=68.0.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "nexus-scalp-engine"
version = "9.0.24"
dependencies = ["pydantic>=2.6.0,<3.0.0", "torch>=2.2.0,<3.0.0"]

[tool.mypy]
python_version = "3.11"

[tool.ruff]
line-length = 100

[tool.pytest.ini_options]
minversion = "8.0"
addopts = "-ra -q"
"""


def _pyproject_repo(tmp_path: Path, mutated: str, tag: str) -> tuple[Path, str, str]:
    repo = tmp_path / f"pyproj-{tag}"
    repo.mkdir()
    _write(repo, "pyproject.toml", PYPROJECT_BASE)
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    _write(repo, "pyproject.toml", mutated)
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "change")
    head = _git(repo, "rev-parse", "HEAD")
    return repo, base, head


def test_pyproject_scope_classification(tmp_path):
    cases = [
        (PYPROJECT_BASE.replace('python_version = "3.11"', 'python_version = "3.12"'), "mypy"),
        (PYPROJECT_BASE.replace("line-length = 100", "line-length = 120"), "lint"),
        (PYPROJECT_BASE.replace('addopts = "-ra -q"', 'addopts = "-ra -q -x"'), "pytest"),
        (PYPROJECT_BASE.replace('"torch>=2.2.0,<3.0.0"', '"torch>=2.3.0,<3.0.0"'), "deps"),
        (PYPROJECT_BASE.replace('version = "9.0.24"', 'version = "9.0.25"'), "meta"),
        (
            PYPROJECT_BASE + '\n[project.scripts]\nnexus = "nexus_scalp.cli.main:app"\n',
            "scripts",
        ),
        (
            PYPROJECT_BASE.replace('addopts = "-ra -q"', 'addopts = "-ra -q -x"').replace(
                'python_version = "3.11"', 'python_version = "3.12"'
            ),
            "mypy,pytest",
        ),
        (PYPROJECT_BASE + "\n[tool.unknown]\nfoo = 1\n", "unknown"),
        (PYPROJECT_BASE.replace('version = "9.0.24"', '# bump\nversion = "9.0.24"'), ""),
        # [tool.uv] resolution keys change what gets installed -> deps.
        (
            PYPROJECT_BASE + '\n[tool.uv]\nconstraint-dependencies = ["filelock==4.2.9"]\n',
            "deps",
        ),
        # [tool.uv] cache-keys only changes uv's cache fingerprint -> meta.
        (PYPROJECT_BASE + '\n[tool.uv]\ncache-keys = ["pyproject.toml"]\n', "meta"),
        # An unrecognised uv key fails closed to unknown.
        (PYPROJECT_BASE + "\n[tool.uv]\nsome-future-key = true\n", "unknown"),
        # Pure formatters: no CI gate reads their config -> meta.
        (PYPROJECT_BASE + "\n[tool.black]\nline-length = 100\n", "meta"),
        (PYPROJECT_BASE + '\n[tool.isort]\nprofile = "black"\n', "meta"),
        # Mixed mypy + uv-constraint change -> both scopes, sorted.
        (
            PYPROJECT_BASE.replace('python_version = "3.11"', 'python_version = "3.12"')
            + '\n[tool.uv]\nconstraint-dependencies = ["filelock==4.2.9"]\n',
            "deps,mypy",
        ),
    ]
    for idx, (mutated, want) in enumerate(cases):
        repo, base, head = _pyproject_repo(tmp_path, mutated, str(idx))
        assert rtp.classify_pyproject_diff(str(repo), base, head) == want


def test_mypy_only_pyproject_selects_tooling_suite(tmp_path, engine_binary):
    """A mypy-only pyproject.toml change must NOT trigger the full
    critical suite: it selects the tests/ci integrity suite instead.
    A pytest-scope change (or an absent/unclassified scope) DOES
    globally invalidate (fail closed)."""
    repo = _scratch_repo(
        tmp_path,
        {
            "pyproject.toml": PYPROJECT_BASE,
            "tests/ci/test_tooling.py": "def test_tooling():\n    assert True\n",
            "tests/critical_suite.txt": "tests/ci/test_tooling.py\ntests/unit/test_other.py\n",
            "tests/fast_suite.txt": "tests/unit/test_other.py\n",
            "tests/unit/test_other.py": "def test_other():\n    assert True\n",
        },
    )
    mypy = rtp.c_plan(
        engine_binary,
        str(repo),
        ["pyproject.toml"],
        HEAD,
        "pull_request",
        False,
        pyproject_scope="mypy",
    )
    assert mypy["selection_mode"] == "targeted"
    assert mypy["fallback_reason"] is None
    assert _plan_ids(mypy) == ["tests/ci/test_tooling.py"]
    assert "mypy" in mypy["notes"][0]

    for scope in ("pytest", "deps", "build", "scripts", "unknown", None):
        plan = rtp.c_plan(
            engine_binary,
            str(repo),
            ["pyproject.toml"],
            HEAD,
            "pull_request",
            False,
            pyproject_scope=scope,
        )
        assert plan["selection_mode"] == "expanded", scope
        assert plan["fallback_reason"] is not None
        assert "pyproject.toml" in plan["fallback_reason"]


# ----------------------------------------------------------------------
# End to end: run_test_plan.py receives --metadata in production.
# ----------------------------------------------------------------------
def test_run_test_plan_end_to_end_metadata(tmp_path, engine_binary, capsys, monkeypatch):
    """A real run_test_plan.py invocation on a sample change: the binary
    receives --metadata, the plan reports metadata_used=true, and the
    fixture-only test is selected and executed by pytest."""
    repo = _scratch_repo(
        tmp_path,
        {
            # NOTE: the __init__.py is load-bearing, not decoration. Without
            # it nexus_scalp is a namespace package in the scratch repo, and
            # the CI venv installs the REAL repo editable — its regular
            # nexus_scalp package (no leaf module) shadows the scratch one
            # and the inner pytest fails with ModuleNotFoundError. With the
            # __init__.py the scratch package is regular too and wins via
            # the scratch pyproject's pythonpath = ["src"].
            "src/nexus_scalp/__init__.py": "",
            "src/nexus_scalp/leaf.py": "def value():\n    return True\n",
            "tests/unit/conftest.py": (
                "import pytest\nfrom nexus_scalp.leaf import value\n\n\n"
                "@pytest.fixture\ndef leaf_fx():\n    return value()\n"
            ),
            "tests/unit/test_via_fixture.py": (
                "def test_via_fixture(leaf_fx):\n    assert leaf_fx\n"
            ),
            "tests/critical_suite.txt": "tests/unit/test_via_fixture.py\n",
            "tests/fast_suite.txt": "",
            "pyproject.toml": '[tool.pytest.ini_options]\npythonpath = ["src"]\n',
        },
    )
    _git(repo, "init", "-q")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    _write(repo, "src/nexus_scalp/leaf.py", "def value():\n    return True  # one-line change\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "leaf change")
    head = _git(repo, "rev-parse", "HEAD")

    out_dir = repo / "ci-results" / "test-plan"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "run_test_plan.py",
            "--repo-root",
            str(repo),
            "--base",
            base,
            "--head",
            head,
            "--event",
            "pull_request",
            "--mode",
            "targeted",
            "--native-binary",
            engine_binary,
            "--xdist",
            "0",
            "--out-dir",
            str(out_dir),
        ],
    )
    assert rtp.main() == 0
    out = capsys.readouterr().out
    assert "C17 METADATA:" in out
    assert "C17 METADATA USED: True" in out
    plan = json.loads((out_dir / "plan.json").read_text(encoding="utf-8"))
    assert plan["metadata_used"] is True
    assert "tests/unit/test_via_fixture.py" in _plan_ids(plan)
    manifest = json.loads((out_dir / "execution-manifest.json").read_text(encoding="utf-8"))
    assert manifest["metadata_schema"] == "nse-ast-meta-v1"
    assert manifest["metadata_files"] > 0
    assert (out_dir / "ast-metadata.json").is_file()
