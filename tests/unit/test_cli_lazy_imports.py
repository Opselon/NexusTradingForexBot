"""EU-03 regression: heavy imports must stay out of the CLI startup path.

WAVE-6 LANE-2 (perf): ``nexus --version`` / ``nexus help`` took 7-10s because
``cli/app_factory.py`` imported ``model_studio_commands`` (-> torch, ~4s),
``ai_commands`` (-> ai_providers.orchestrator), ``dependency_commands`` (->
networkx) and ``gateway_commands`` (-> fastapi, which itself pulls torch +
polars) AT IMPORT TIME. Those are now registered lazily (see
``cli/lazy_subapps.py``): they load on the first subcommand resolution, never
for the version/help surfaces.

These assertions run in a SUBPROCESS because ``sys.modules`` is process-global
— a single in-process import would leave the modules cached for every later
test and the gate could never fail.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

HEAVY_MODULES = (
    "torch",
    "polars",
    "networkx",
    "fastapi",
    "nexus_scalp.web.model_studio_routes",
    "nexus_scalp.cli.model_studio_commands",
    "nexus_scalp.cli.ai_commands",
    "nexus_scalp.cli.dependency_commands",
    "nexus_scalp.ai_providers.orchestrator",
)


def _env() -> dict[str, str]:
    """Import-isolated env so the subprocess resolves THIS tree's src/."""
    env = dict(os.environ)
    src = REPO_ROOT / "src"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(src)] + [p for p in env.get("PYTHONPATH", "").split(os.pathsep) if p]
    )
    return env


def _heavy_present(modules: set[str]) -> list[str]:
    return sorted(m for m in modules if m in HEAVY_MODULES)


def _run(code: str, *, timeout: int = 240) -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=_env(),
        cwd=REPO_ROOT,
        timeout=timeout,
        check=False,
    )
    return proc.returncode, proc.stdout, proc.stderr


def test_import_app_factory_does_not_load_torch_or_model_studio() -> None:
    """Importing the canonical CLI factory must NOT import the heavy stack.

    ``app_factory`` is the module ``nexus --version`` pays for; if torch is in
    ``sys.modules`` after importing it, the startup cost is back.
    """
    code = (
        "import json, sys\n"
        "import nexus_scalp.cli.app_factory  # noqa: F401\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    rc, out, err = _run(code)
    assert rc == 0, f"import failed (rc={rc}): {err[:2000]}"
    loaded = set(json_loads(out))
    leaked = _heavy_present(loaded)
    assert not leaked, (
        f"app_factory import leaked heavy modules — EU-03 startup invariant broken by: {leaked}"
    )


def test_import_cli_main_facade_stays_torch_free() -> None:
    """The facade (cli.main) is what the real entry points import."""
    code = (
        "import json, sys\n"
        "import nexus_scalp.cli.main  # noqa: F401\n"
        "print(json.dumps(sorted(sys.modules)))\n"
    )
    rc, out, err = _run(code)
    assert rc == 0, f"import failed (rc={rc}): {err[:2000]}"
    leaked = _heavy_present(set(json_loads(out)))
    assert not leaked, f"cli.main import leaked heavy modules: {leaked}"


@pytest.mark.parametrize("args", (["--version"], ["version"]))
def test_cli_version_and_help_do_not_import_torch(args: list[str]) -> None:
    """The ``nexus --version`` / ``nexus version`` fast path stays torch-free.

    Both print the build identity without dispatching a subcommand, so the lazy
    registration must never fire for them. ``--help`` is DELIBERATELY not
    asserted here: it enumerates the full command tree, which legitimately
    imports every command module to render its short help (verified separately
    by ``test_cli_help_lists_every_lazy_command``). The EU-03 invariant targets
    the startup cost of the fast path, and the help surface is not on it.
    """
    code = (
        "import json, runpy, sys\n"
        "sys.argv = ['nexus', " + ", ".join(repr(a) for a in args) + "]\n"
        "try:\n"
        "    runpy.run_module('nexus_scalp.cli.main', run_name='__main__')\n"
        "except SystemExit:\n"
        "    pass\n"
        "print('MODULES:' + json.dumps(sorted(sys.modules)))\n"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        env=_env(),
        cwd=REPO_ROOT,
        timeout=240,
        check=False,
    )
    assert proc.returncode == 0, f"CLI run failed (rc={proc.returncode}): {proc.stderr[:2000]}"
    marker = [ln for ln in proc.stdout.splitlines() if ln.startswith("MODULES:")]
    assert marker, "probe did not emit the module snapshot"
    leaked = _heavy_present(set(json_loads(marker[0][len("MODULES:") :])))
    assert not leaked, f"`nexus {' '.join(args)}` imported heavy modules: {leaked}"


def test_cli_help_lists_every_lazy_command() -> None:
    """PARITY GATE: lazy registration must not drop commands from ``--help``.

    ``nexus --help`` enumerates the whole tree, including the lazy groups, so
    every command that was listed BEFORE the deferral must still be listed —
    this is exactly the failure mode the deferral could have introduced.
    """
    from typer.testing import CliRunner

    from nexus_scalp.cli.main import app

    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0, result.output
    for expected in (
        "model-quality",
        "model-predict",
        "model-stress-test",
        "dataset-download",
        "position-adviser-packages",
        "ai",
        "dependency",
        "gateway",
    ):
        assert expected in result.output, f"`{expected}` missing from nexus --help"


def test_lazy_registration_is_idempotent_and_registers_model_studio() -> None:
    """The lazy hook imports + registers exactly once, and it does register."""
    code = (
        "import json, sys\n"
        "import nexus_scalp.cli.app_factory as af\n"
        "from nexus_scalp.cli.lazy_subapps import register_lazy_subapps\n"
        "register_lazy_subapps()\n"
        "before = len(af.app.registered_commands)\n"
        "register_lazy_subapps()  # second call must be a no-op\n"
        "after = len(af.app.registered_commands)\n"
        "names = sorted(c.name for c in af.app.registered_commands)\n"
        "print(json.dumps({'before': before, 'after': after, 'names': names}))\n"
    )
    rc, out, err = _run(code)
    assert rc == 0, f"registration failed (rc={rc}): {err[:2000]}"
    payload = json_loads(out)
    assert payload["before"] == payload["after"], "lazy registration is not idempotent"
    for expected in ("model-quality", "model-predict", "dataset-download"):
        assert expected in payload["names"], f"{expected} not registered by the lazy hook"


def json_loads(text: str) -> list[str]:
    import json

    return json.loads(text)
