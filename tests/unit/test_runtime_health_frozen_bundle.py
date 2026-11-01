"""Regression tests: health RUNTIME check must not false-fail inside a frozen bundle.

Context: a PyInstaller bundle ships its dependency closure inside the
archive, but the dist-info metadata that ``verify_runtime_closure()`` walks
is only partially visible to ``importlib.metadata`` there. Running the
metadata walk inside a frozen app reported a FALSE "dependency closure
incomplete" and told a brand-new end user to reinstall an installation
that was actually complete (observed on the v9.0.18 clean-room install:
RUNTIME FAIL listing MetaTrader5, PyNaCl, Pygments, annotated-types,
fastapi, feedparser — every one of them importable from the bundle).

The startup gate (``run_startup_dependency_gate``) already skips when
frozen; the health RUNTIME check must do the same.
"""

from __future__ import annotations

import sys
from unittest import mock

from nexus_scalp.release import health as health_mod
from nexus_scalp.release import paths as paths_mod


def _health_engine() -> health_mod.HealthEngine:
    return health_mod.HealthEngine()


def test_runtime_check_passes_when_frozen_even_if_metadata_walk_fails(monkeypatch):
    """Frozen bundle with unusable dist metadata => RUNTIME must still PASS."""
    monkeypatch.setattr(paths_mod, "is_frozen", lambda: True)
    # health.check_runtime imports paths lazily; patch the module attribute it
    # reads so the frozen branch is taken regardless of the real environment.
    monkeypatch.setattr(paths_mod.sys, "frozen", True, raising=False)

    engine = _health_engine()
    entry = engine.check_runtime()

    assert entry.category == "RUNTIME"
    assert entry.verdict == "PASS", (
        f"frozen bundle reported RUNTIME={entry.verdict} "
        f"({entry.reason}) — the metadata walk must be skipped when frozen"
    )
    assert "dependency closure incomplete" not in entry.reason


def test_runtime_check_still_fails_on_broken_source_checkout(monkeypatch):
    """Non-frozen runtime with an incomplete closure => RUNTIME must FAIL.

    Guarantees the frozen-skip did not neuter the original defect class this
    check was built for (uvicorn present / click absent on a dev checkout).
    """
    monkeypatch.setattr(paths_mod, "is_frozen", lambda: False)

    from nexus_scalp.release import runtime_deps

    broken = runtime_deps.ClosureReport(
        ok=False,
        checked=3,
        source="test",
        notes=[],
        missing=[
            runtime_deps.MissingDependency("click", "not installed", ["uvicorn"], ""),
        ],
    )
    with mock.patch.object(runtime_deps, "verify_runtime_closure", return_value=broken):
        engine = _health_engine()
        entry = engine.check_runtime()

    assert entry.category == "RUNTIME"
    assert entry.verdict == "FAIL"
    assert "dependency closure incomplete" in entry.reason
    assert "click" in entry.reason


def test_startup_dependency_gate_skips_when_frozen(monkeypatch):
    """The startup gate must not attempt repair inside a frozen bundle."""
    monkeypatch.setattr(paths_mod, "is_frozen", lambda: True)
    from nexus_scalp.release import runtime_deps

    with mock.patch.object(
        runtime_deps, "verify_runtime_closure", side_effect=AssertionError("must not run")
    ) as probe:
        runtime_deps.run_startup_dependency_gate()

    probe.assert_not_called()


def test_paths_is_frozen_uses_sys_frozen(monkeypatch):
    """Sanity: is_frozen() reflects sys.frozen, not an env var or heuristic."""
    monkeypatch.setattr(paths_mod.sys, "frozen", True, raising=False)
    assert paths_mod.is_frozen() is True
    monkeypatch.setattr(paths_mod.sys, "frozen", False, raising=False)
    assert paths_mod.is_frozen() is False
    monkeypatch.delattr(paths_mod.sys, "frozen", raising=False)
    assert paths_mod.is_frozen() is False
