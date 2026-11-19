"""Runner isolation for module-scoped LiveEngine fixtures.

WHY THIS EXISTS (PR #740, run #3330 — 51 LEGACY_UNVERIFIED errors):
pytest sets up module-scoped fixtures BEFORE function-scoped autouse
fixtures. The repo's runner-isolation fixtures in ``tests/conftest.py``
(``_isolate_runner_home_and_tmpdir``, ``_isolate_settings_db``,
``_isolate_implicit_audit_db``, ``_isolate_paper_persist``) are all
function-scoped autouse, so a module-scoped engine fixture builds its
``LiveEngine`` while HOME is still the REAL runner home and
NEXUS_SETTINGS_DB still points at the REAL shared ``app_settings.db``.

``LiveEngine.__init__`` binds that shared DB at construction
(``SettingsDatabase`` opens its sqlite connection in ``__init__``) and
rehydrates the runtime snapshot from it — and the snapshot's
``model.model_artifact_path`` OVERRIDES the fixture's config path
(``live_engine.py`` BUG-136 rehydration). Any earlier test that applied
a runtime config (``RuntimeConfigStore.apply`` persists the FULL flat
snapshot, including the model path) therefore diverts every later
module-scoped engine at a foreign artifact: in run #3330 all 51 errors
verified the SAME path,
``.../popen-gw2/hotswitch0/model.pt`` — a bare force-fresh mint from
``test_download_ready_hotswitch_hotreload.py`` with no integrity
sidecars — instead of the fixtures' own verified bundles.

This context manager reproduces exactly the conftest isolation
environment, but callable from inside a module-scoped fixture so the
engine is constructed (and lives) hermetically. Hold it for the whole
fixture lifetime (through ``yield``): the engine's settings service is
bound at construction, and a fresh empty settings DB means rehydrate
finds nothing to override the fixture's config path with.

Tests that need to observe what the engine persisted MUST read it
through ``engine.settings_service`` — a fresh ``load_settings_service()``
inside a test body resolves via the per-test env override to a
different, empty DB (see ``test_engine_mode_apply_and_persist``).
"""

from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path

import pytest

#: Env keys the conftest function-scoped isolation fixtures redirect.
#: Kept in sync with ``tests/conftest.py``.
_ENV_KEYS: tuple[str, ...] = (
    "HOME",
    "USERPROFILE",
    "LOCALAPPDATA",
    "APPDATA",
    "TMPDIR",
    "TEMP",
    "TMP",
    "NEXUS_SETTINGS_DB",
    "NEXUS_AUDIT_DB",
    "NSE_DATABASE__SQLITE_PATH",
    "NEXUS_DATA_ROOT",
    "NEXUS_PAPER_PERSIST",
)


@contextlib.contextmanager
def isolated_module_engine_env(
    tmp_path_factory: pytest.TempPathFactory, name: str
) -> Iterator[Path]:
    """Isolate HOME / settings DB / audit DB / data root for a module fixture.

    Mirrors the four function-scoped autouse isolation fixtures in
    ``tests/conftest.py`` (same keys, same layout), rooted at a fresh
    ``tmp_path_factory.mktemp(name)`` dir. Restores every env var and
    ``tempfile.tempdir`` on exit. Yields the run dir.
    """
    run_dir = tmp_path_factory.mktemp(name)
    home_dir = run_dir / "home"
    tmp_dir = run_dir / "tmp"
    home_dir.mkdir()
    tmp_dir.mkdir()
    saved = {key: os.environ.get(key) for key in _ENV_KEYS}
    previous_tempdir = tempfile.tempdir
    os.environ.update(
        {
            "HOME": str(home_dir),
            "USERPROFILE": str(home_dir),
            "LOCALAPPDATA": str(home_dir),
            "APPDATA": str(home_dir),
            "TMPDIR": str(tmp_dir),
            "TEMP": str(tmp_dir),
            "TMP": str(tmp_dir),
            "NEXUS_SETTINGS_DB": str(run_dir / "app_settings.db"),
            "NEXUS_AUDIT_DB": str(run_dir / "audit.db"),
            "NSE_DATABASE__SQLITE_PATH": str(run_dir / "audit.db"),
            "NEXUS_DATA_ROOT": str(run_dir),
        }
    )
    if os.environ.get("NEXUS_PAPER_PERSIST", "") == "":
        os.environ["NEXUS_PAPER_PERSIST"] = "1"
    tempfile.tempdir = str(tmp_dir)
    try:
        yield run_dir
    finally:
        tempfile.tempdir = previous_tempdir
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
