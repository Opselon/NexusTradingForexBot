"""Exact-environment contract regressions; never download heavyweight wheels."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from nexus_scalp.model_provisioning import training_env as te


def test_resolve_python_skips_unsupported_default(monkeypatch, tmp_path):
    manager = te.TrainingEnvironmentManager(workspace=tmp_path)
    monkeypatch.delenv(te.TRAINING_PYTHON_ENV, raising=False)
    monkeypatch.setattr(te, "is_interpreter_process", lambda: True)
    monkeypatch.setattr(te.shutil, "which", lambda exe: exe)
    visited = []

    def probe(exe):
        visited.append(exe)
        minor = 11 if exe == "python3" else 10
        return {"found": True, "minor": minor, "version": f"3.{minor}.0"}

    monkeypatch.setattr(te, "probe_python_interpreter", probe)
    executable, check = manager.resolve_python()
    assert check.ok
    assert executable == "python3"
    assert "python3" in visited


def test_resolve_python_reports_unsupported_when_no_candidate_works(monkeypatch, tmp_path):
    manager = te.TrainingEnvironmentManager(workspace=tmp_path)
    monkeypatch.delenv(te.TRAINING_PYTHON_ENV, raising=False)
    monkeypatch.setattr(te, "is_interpreter_process", lambda: True)
    monkeypatch.setattr(te.shutil, "which", lambda exe: exe)
    monkeypatch.setattr(te, "probe_python_interpreter", lambda exe: {"found": True, "minor": 10})
    _, check = manager.resolve_python()
    assert not check.ok
    assert check.code == te.EnvCode.PYTHON_VERSION_UNSUPPORTED


def _target_manager(monkeypatch, tmp_path, torch="2.13.0+cpu", minor=11):
    manager = te.TrainingEnvironmentManager(workspace=tmp_path)
    target = str(tmp_path / "training-env" / "bin" / "python")
    monkeypatch.setattr(
        manager, "resolve_python", lambda: (sys.executable, te.EnvCheck("python", True))
    )
    monkeypatch.setattr(
        manager,
        "resolve_env",
        lambda exe: (
            target,
            {"found": True, "path": str(tmp_path / "training-env"), "type": "managed-venv"},
        ),
    )
    monkeypatch.setattr(
        te,
        "probe_python_interpreter",
        lambda exe: {
            "found": True,
            "minor": minor,
            "version": f"3.{minor}.1",
            "pip": True,
            "torch": torch,
        },
    )
    monkeypatch.setattr(
        te,
        "detect_nvidia_gpu",
        lambda: {"available": True, "driver_version": "580.0", "name": "fixture"},
    )
    monkeypatch.setattr(manager, "run_subprocess_smoke", lambda *a: te.EnvCheck("smoke", True))
    return manager, target


@pytest.mark.parametrize(
    "backend,build",
    [("cpu", "2.13.0+cu126"), ("cpu", "2.13.0"), ("cuda", "2.13.0+cu128"), ("cuda", "2.13.0+cpu")],
)
def test_status_rejects_wrong_exact_local_build(monkeypatch, tmp_path, backend, build):
    manager, _ = _target_manager(monkeypatch, tmp_path, torch=build)
    report = manager.status(backend)
    assert not report.training_ready
    assert te.EnvCode.TORCH_LOCAL_TAG_MISMATCH in [c.code for c in report.failing()]


def test_install_uses_canonical_string_pins_and_target_interpreter(monkeypatch, tmp_path):
    manager = te.TrainingEnvironmentManager(workspace=tmp_path)
    report = te.EnvironmentReport(
        environment={"found": True, "path": str(tmp_path)},
        pytorch={"path": "/validated/env/python"},
    )
    monkeypatch.setattr(
        manager, "resolve_python", lambda: (sys.executable, te.EnvCheck("python", True))
    )
    monkeypatch.setattr(manager, "status", lambda backend=None: report)
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(te.subprocess, "run", run)
    manager.install("cpu")
    assert calls[0][:3] == ["/validated/env/python", "-m", "pip"]
    assert "torch==2.13.0+cpu" in calls[0]
    assert "--extra-index-url" not in calls[0]
    assert calls[0][calls[0].index("--index-url") + 1] == "https://download.pytorch.org/whl/cpu"


def _ready_probe(monkeypatch, tmp_path, *, backend="cpu", version="2.13.0+cpu", minor=11):
    manager = te.TrainingEnvironmentManager(workspace=tmp_path)
    monkeypatch.setattr(
        manager, "resolve_python", lambda: (sys.executable, te.EnvCheck("python", True))
    )
    monkeypatch.setattr(
        manager,
        "resolve_env",
        lambda exe: (sys.executable, {"found": True, "path": str(tmp_path), "type": "venv"}),
    )
    monkeypatch.setattr(
        te,
        "probe_python_interpreter",
        lambda exe: {
            "found": True,
            "version": f"3.{minor}.1",
            "minor": minor,
            "pip": True,
            "torch": version,
        },
    )
    monkeypatch.setattr(
        te,
        "detect_nvidia_gpu",
        lambda: {"available": True, "driver_version": "1.0", "name": "test fixture"},
    )
    monkeypatch.setattr(manager, "run_in_process_smoke", lambda backend: te.EnvCheck("smoke", True))
    return manager


def test_target_venv_python_version_is_a_readiness_gate(monkeypatch, tmp_path):
    manager = _ready_probe(monkeypatch, tmp_path, minor=10)
    report = manager.status("cpu")
    assert not report.training_ready
    assert any(c.code == "PYTHON_VERSION_UNSUPPORTED" for c in report.failing())


@pytest.mark.parametrize(
    ("backend", "version"), [("cpu", "2.13.0+cu126"), ("cuda", "2.13.0+cu128")]
)
def test_exact_variant_not_just_any_cuda(monkeypatch, tmp_path, backend, version):
    manager = _ready_probe(monkeypatch, tmp_path, backend=backend, version=version)
    report = manager.status(backend)
    assert not report.training_ready
    assert any(c.code == "TORCH_LOCAL_TAG_MISMATCH" for c in report.failing())


def test_target_report_uses_real_target_facts(monkeypatch, tmp_path):
    manager, target = _target_manager(monkeypatch, tmp_path)
    report = manager.status("cpu")
    assert report.python == {"found": True, "version": "3.11.1", "path": target}
    assert report.environment["python"] == target


@pytest.mark.parametrize("backend", ["metal", "", "gpu"])
def test_invalid_backend_is_a_typed_contract_failure(monkeypatch, tmp_path, backend):
    manager, _ = _target_manager(monkeypatch, tmp_path)
    with pytest.raises(te.TrainingEnvironmentError) as error:
        manager.status(backend)
    assert error.value.code == te.EnvCode.CONTRACT_INVALID


@pytest.mark.parametrize("minor,found", [(11, False), (None, True)])
def test_broken_target_is_not_a_found_environment(monkeypatch, tmp_path, minor, found):
    manager, target = _target_manager(monkeypatch, tmp_path)
    monkeypatch.setattr(
        te,
        "probe_python_interpreter",
        lambda exe: {
            "found": found,
            "minor": minor,
            "version": None,
            "pip": True,
            "torch": "2.13.0+cpu",
        },
    )
    report = manager.status("cpu")
    assert not report.training_ready
    assert not report.environment["found"]
    assert any(c.code == "PYTHON_NOT_FOUND" for c in report.failing())


def test_install_never_falls_back_to_application_python(monkeypatch, tmp_path):
    manager = te.TrainingEnvironmentManager(workspace=tmp_path)
    monkeypatch.setattr(
        manager, "resolve_python", lambda: (sys.executable, te.EnvCheck("python", True))
    )
    monkeypatch.setattr(
        manager, "status", lambda **kw: te.EnvironmentReport(environment={"found": True})
    )
    calls = []
    monkeypatch.setattr(te.subprocess, "run", lambda *a, **kw: calls.append(a))
    with pytest.raises(te.TrainingEnvironmentError):
        manager.install("cpu")
    assert not calls


def test_invalid_explicit_venv_never_reuses_active_python(monkeypatch, tmp_path):
    manager = te.TrainingEnvironmentManager(workspace=tmp_path)
    monkeypatch.setenv(te.TRAINING_ENV_DIR_ENV, str(tmp_path / "missing"))
    executable, info = manager.resolve_env(sys.executable)
    assert executable is None
    assert not info["found"]


def test_install_includes_locked_application_stack(monkeypatch, tmp_path):
    manager = te.TrainingEnvironmentManager(workspace=tmp_path)
    report = te.EnvironmentReport(environment={"found": True, "python": "/env/python"})
    monkeypatch.setattr(
        manager, "resolve_python", lambda: (sys.executable, te.EnvCheck("python", True))
    )
    monkeypatch.setattr(manager, "status", lambda **kw: report)
    calls = []

    def run(args, **kw):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(te.subprocess, "run", run)
    manager.install("cpu")
    assert len(calls) == 2
    app = calls[1]
    assert app[:3] == ["/env/python", "-m", "pip"]
    assert "--require-virtualenv" in app
    assert app[app.index("--index-url") + 1] == "https://pypi.org/simple"
    assert "structlog==26.1.0" in app
    assert "pydantic==2.13.5" in app
    assert "polars==1.44.2" in app
    assert "numpy==2.4.6 ; python_full_version < '3.12'" in app
    lock = (Path(__file__).parents[2] / "requirements.lock").read_text().lower()
    for requirement in manager.contract["runtime"]["packages"]:
        # Lock normalizes names to lowercase (pyyaml); compare case-insensitively.
        name, _, spec = requirement.partition("==")
        assert f"{name.lower()}=={spec.lower()}" in lock


def test_torch_smoke_alone_cannot_make_target_ready(monkeypatch, tmp_path):
    manager, target = _target_manager(monkeypatch, tmp_path)
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(
            args, 1, json.dumps({"ready": False, "missing": {"polars": "ModuleNotFoundError"}}), ""
        )

    monkeypatch.setattr(te.subprocess, "run", run)
    report = manager.status("cpu")
    assert not report.training_ready
    assert any(c.stage == "application" and c.remedy for c in report.failing())
    assert calls[0][0][0] == target
    assert calls[0][0][-1] == "--probe"
    assert json.loads(calls[0][1]["input"])["packages"] == manager.contract["runtime"]["packages"]


def test_real_worker_rejects_wrong_runtime_pin():
    from nexus_scalp.model_provisioning.training_dispatch import worker_script

    proc = subprocess.run(
        [sys.executable, "-B", str(worker_script()), "--requirements-stdin", "--probe"],
        input=json.dumps({"packages": ["structlog==0.0.0"]}),
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    data = json.loads(proc.stdout)
    assert proc.returncode == 1
    assert data["ready"] is False
    assert data["mismatched"]["structlog"]["required"] == "0.0.0"


@pytest.mark.parametrize("in_process", [False, True])
@pytest.mark.parametrize(
    "returncode,body,ready",
    [
        (0, {"ready": True}, True),
        (1, {"ready": True}, False),
        (0, {"ready": "true"}, False),
        (0, {}, False),
        (0, [], False),
        (0, "invalid-json", False),
        (1, {"ready": False, "mismatched": {"numpy": "wrong version"}}, False),
    ],
)
def test_application_probe_controls_both_readiness_paths(
    monkeypatch, tmp_path, in_process, returncode, body, ready
):
    manager = (
        _ready_probe(monkeypatch, tmp_path)
        if in_process
        else _target_manager(monkeypatch, tmp_path)[0]
    )
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        stdout = body if isinstance(body, str) else json.dumps(body)
        return subprocess.CompletedProcess(args, returncode, stdout, "")

    monkeypatch.setattr(te.subprocess, "run", run)
    report = manager.status("cpu")
    assert report.training_ready is ready
    assert report.in_process_ready is (ready and in_process)
    assert calls[0][0] == report.environment["python"]


@pytest.mark.parametrize("minor,expected", [(11, "2.4.6"), (12, "2.5.3"), (13, "2.5.3")])
def test_stdlib_probe_evaluates_runtime_markers_in_target(monkeypatch, minor, expected):
    import runpy

    from nexus_scalp.model_provisioning.training_dispatch import worker_script

    probe = runpy.run_path(str(worker_script()))["dependency_probe"]
    namespace = probe.__globals__
    monkeypatch.setattr(namespace["sys"], "version_info", (3, minor, 0))
    monkeypatch.setattr(namespace["importlib"], "import_module", lambda name: object())
    monkeypatch.setattr(namespace["importlib"].metadata, "version", lambda name: expected)
    report = probe(
        [
            "numpy==2.4.6 ; python_full_version < '3.12'",
            "numpy==2.5.3 ; python_full_version >= '3.12'",
        ]
    )
    assert report["ready"] is True
    assert report["versions"] == {"numpy": expected}


def test_real_worker_checks_actual_trainer_import(tmp_path):
    from nexus_scalp.model_provisioning.training_dispatch import worker_script

    launcher = tmp_path / "broken_trainer_fixture.py"
    launcher.write_text(
        "import builtins, runpy, sys\n"
        "original = builtins.__import__\n"
        "def guarded(name, *args, **kwargs):\n"
        "    if name == 'nexus_scalp.training.walk_forward_trainer':\n"
        "        raise ImportError('test fixture: trainer cannot load')\n"
        "    return original(name, *args, **kwargs)\n"
        "builtins.__import__ = guarded\n"
        f"sys.argv = [{str(worker_script())!r}, '--requirements-stdin', '--probe']\n"
        f"runpy.run_path({str(worker_script())!r}, run_name='__main__')\n"
    )
    proc = subprocess.run(
        [sys.executable, "-B", str(launcher)],
        input=json.dumps({"packages": []}),
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    data = json.loads(proc.stdout)
    assert proc.returncode == 1
    assert not data["ready"]
    assert data["missing"]["nexus_scalp.model_generation.three_model"] == "ImportError"


def test_real_worker_reports_missing_distribution():
    from nexus_scalp.model_provisioning.training_dispatch import worker_script

    proc = subprocess.run(
        [sys.executable, "-B", str(worker_script()), "--requirements-stdin", "--probe"],
        input=json.dumps({"packages": ["nexus-nonexistent-test-distribution==0.0.0"]}),
        text=True,
        capture_output=True,
        timeout=60,
        check=False,
    )
    data = json.loads(proc.stdout)
    assert proc.returncode == 1
    assert data["missing"]["nexus-nonexistent-test-distribution"] == "PackageNotFoundError"
    assert data["ready"] is False


def test_frozen_probe_uses_payload_and_cleans_loader_environment(monkeypatch, tmp_path):
    from nexus_scalp.model_provisioning import training_dispatch
    from nexus_scalp.release import paths

    manager, target = _target_manager(monkeypatch, tmp_path)
    script = tmp_path / "training_payload" / "training_worker.py"
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.setattr(training_dispatch, "worker_script", lambda: script)
    for key in ("PYTHONHOME", "PYTHONPATH", "_MEIPASS2", "LD_LIBRARY_PATH"):
        monkeypatch.setenv(key, "/frozen/loader")
    monkeypatch.setenv("LD_LIBRARY_PATH_ORIG", "/original/libs")
    calls = []

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0, '{"ready": true}', "")

    monkeypatch.setattr(te.subprocess, "run", run)
    assert manager.status("cpu").training_ready
    args, kwargs = calls[0]
    assert args == [target, "-B", str(script), "--requirements-stdin", "--probe"]
    assert not any(key in kwargs["env"] for key in ("PYTHONHOME", "PYTHONPATH", "_MEIPASS2"))
    assert kwargs["env"]["LD_LIBRARY_PATH"] == "/original/libs"
    assert kwargs["env"]["PYTHONDONTWRITEBYTECODE"] == "1"


@pytest.mark.parametrize(
    "failure", [FileNotFoundError("payload missing"), subprocess.TimeoutExpired("probe", 180)]
)
def test_probe_failure_is_actionable_and_not_ready(monkeypatch, tmp_path, failure):
    manager, _ = _target_manager(monkeypatch, tmp_path)

    def run(*args, **kwargs):
        raise failure

    monkeypatch.setattr(te.subprocess, "run", run)
    report = manager.status("cpu")
    assert not report.training_ready
    assert any(c.code == "APPLICATION_NOT_READY" and c.remedy for c in report.failing())


def test_old_driver_cannot_pass_ready(monkeypatch, tmp_path):
    manager = _ready_probe(monkeypatch, tmp_path, backend="cuda", version="2.13.0+cu126")
    report = manager.status("cuda")
    assert not report.training_ready
    assert any(c.code == "DRIVER_TOO_OLD" for c in report.failing())
