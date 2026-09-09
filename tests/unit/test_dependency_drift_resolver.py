"""Regression suite for the dependency drift resolver (scripts/ci/check_dependency_drift.py).

Guards the EXACT failure class that kept the `Dependency Lock & Migration Safety`
workflow red through runs #985-#989:

  * uv pip compile prunes ``python_full_version`` marker arms below every
    interpreter visible to the resolver. A host with only 3.12+ installed
    (ubuntu-24.04 runners) silently produced a fresh lock WITHOUT the
    ``< '3.12'`` arms (numpy 2.4.6, tomli) — flagging the committed universal
    lock as stale even with the uv version pinned.
  * A pyproject dependency bump (ruff 0.16.5 -> 0.16.6, dependabot) without a
    matching lock regeneration must stay a detected drift, never a silent pass.

The resolver test reads the SCRIPT source (no network, no uv required); the
drift-detection test exercises ``main()`` end-to-end against a tampered copy
of the committed lock using a stubbed compile step (deterministic, offline).
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "ci" / "check_dependency_drift.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("check_dependency_drift_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# Resolver pins the lowest supported interpreter (CI #989 root cause)
# ---------------------------------------------------------------------------


def test_uv_compile_pins_python_version_floor() -> None:
    """The uv pip compile invocation MUST carry --python-version 3.11.

    Without it the resolution depends on whatever interpreters the executing
    host happens to have — the arm-pruning class that broke CI.
    """
    src = SCRIPT.read_text(encoding="utf-8")
    compile_argv_start = src.index('"uv",')
    compile_argv_end = src.index("--output-file", compile_argv_start)
    argv_block = src[compile_argv_start:compile_argv_end]
    assert "--python-version" in argv_block, (
        "check_dependency_drift._run_uv_compile no longer pins --python-version; "
        "the resolver will prune <3.12 marker arms on 3.12-only hosts (CI #989)"
    )
    # The pin must be the project floor, not a moving target.
    m = re.search(r'"--python-version",\s*\n\s*"([0-9.]+)"', argv_block)
    assert m is not None, "--python-version flag present but value unparsable"
    assert m.group(1) == "3.11"

    # And the floor must match pyproject requires-python (single source of truth).
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    req = re.search(r'requires-python\s*=\s*"([^"]+)"', pyproject)
    assert req is not None, "pyproject lost requires-python"
    floor = re.search(r">=\s*([0-9.]+)", req.group(1))
    assert floor is not None, f"unsupported requires-python form: {req.group(1)}"
    assert floor.group(1) == m.group(1), (
        f"resolver pin {m.group(1)} != requires-python floor {floor.group(1)}"
    )


# ---------------------------------------------------------------------------
# Drift detection still fails loudly on a stale lock (offline, stubbed uv)
# ---------------------------------------------------------------------------


def _write_lock(tmp: Path, ruff_version: str) -> None:
    body = (
        f"ruff=={ruff_version} \\\n"
        "    --hash=sha256:0000000000000000000000000000000000000000000000000000000000000000\n"
        "numpy==2.4.6 ; python_full_version < '3.12' \\\n"
        "    --hash=sha256:0000000000000000000000000000000000000000000000000000000000000000\n"
        "numpy==2.5.3 ; python_full_version >= '3.12' \\\n"
        "    --hash=sha256:0000000000000000000000000000000000000000000000000000000000000000\n"
    )
    (tmp / "requirements.lock").write_text(mod_for_headers.LOCK_HEADER + body, encoding="utf-8")
    (tmp / "requirements.txt").write_text(
        mod_for_headers.REQS_HEADER + f"ruff=={ruff_version}\n"
        "numpy==2.4.6 ; python_full_version < '3.12'\n"
        "numpy==2.5.3 ; python_full_version >= '3.12'\n",
        encoding="utf-8",
    )


mod_for_headers = _load_module()  # header constants needed by _write_lock


def test_stale_lock_is_detected_not_silently_accepted(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A pyproject bump without regen (ruff 0.16.5 in lock vs 0.16.6 wanted)
    must exit 1 with the regen instruction — the guard's whole point."""
    mod = mod_for_headers
    _write_lock(tmp_path, ruff_version="0.16.5")
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod, "LOCK", tmp_path / "requirements.lock")
    monkeypatch.setattr(mod, "REQS", tmp_path / "requirements.txt")
    # Fresh compile wants ruff 0.16.6 while the committed lock says 0.16.5.
    fresh = (
        "ruff==0.16.6 \\\n"
        "    --hash=sha256:0000000000000000000000000000000000000000000000000000000000000000\n"
        "numpy==2.4.6 ; python_full_version < '3.12' \\\n"
        "    --hash=sha256:0000000000000000000000000000000000000000000000000000000000000000\n"
        "numpy==2.5.3 ; python_full_version >= '3.12' \\\n"
        "    --hash=sha256:0000000000000000000000000000000000000000000000000000000000000000\n"
    )
    monkeypatch.setattr(mod, "_run_uv_compile", lambda: fresh)

    rc = mod.main([])
    assert rc == 1
    # Do NOT leave the artifacts thinking they are clean: regen hint present.


def test_in_sync_lock_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = mod_for_headers
    _write_lock(tmp_path, ruff_version="0.16.6")
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(mod, "LOCK", tmp_path / "requirements.lock")
    monkeypatch.setattr(mod, "REQS", tmp_path / "requirements.txt")
    fresh = (
        "ruff==0.16.6 \\\n"
        "    --hash=sha256:0000000000000000000000000000000000000000000000000000000000000000\n"
        "numpy==2.4.6 ; python_full_version < '3.12' \\\n"
        "    --hash=sha256:0000000000000000000000000000000000000000000000000000000000000000\n"
        "numpy==2.5.3 ; python_full_version >= '3.12' \\\n"
        "    --hash=sha256:0000000000000000000000000000000000000000000000000000000000000000\n"
    )
    monkeypatch.setattr(mod, "_run_uv_compile", lambda: fresh)

    rc = mod.main([])
    assert rc == 0
