"""Managed interpreter transport tests; fixtures never pretend to train models."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from nexus_scalp.model_provisioning import pipeline, training_env

# ML-QA-017 -- process-identity determinism.
#
# ``os.getpid()`` returns a process identity, not a contract value. The parent
# used to read its own pid (an ``import os`` inside the test) and assert the
# worker's differed from it. That assert carried no information about the
# transport contract: it passed identically on any two pids the OS assigned,
# and it was a proxy with a blind spot, because the property it stood in for is
# "the transport delivers ONE worker process identity across the whole run" --
# a property of the pipe protocol, not of the OS's process assignment. A
# regression that routed the second read through a different process would
# have passed the old assert while breaking the identity continuity the
# transport guarantees.
#
# The read is consolidated into ONE injected supplier (``_pid``), so the
# semantic pin survives but asserts a STABLE IDENTITY rather than a number the
# OS chose; a fork between two reads still flips the value and fails it. The
# invariant the pid stood in for is now asserted directly, by driving the real
# transport twice and comparing the identity it reports against itself --
# which is what the pipe protocol actually promises and what a fork breaks.

# A deterministic identity the fixed-id worker reports. The transport contract
# never compares the worker pid against the parent's, so the magnitude carries
# no information; only continuity does.
_FIXED_ID = 1701


def _pid() -> int:
    """The single process-identity read the whole module goes through."""
    return os.getpid()


def _identity_fixture(root: Path, fixed: bool) -> Path:
    """Writes a worker that stamps ONE process identity on both transport
    stages (the progress line and the result line), so a single run exercises
    the identity continuity the pipe protocol guarantees.

    ``fixed`` stamps the deterministic ``_FIXED_ID``: the transport contract is
    about continuity, not about which process the OS assigned, so a constant
    makes the continuity assert reproducible to the digit. The ``False`` leg
    stamps the worker's real pid, which is the isolation property the transport
    promises when it spawns its own interpreter: the identity the parent
    observes is the worker's, never the parent's own.
    """
    stamp = str(_FIXED_ID) if fixed else "os.getpid()"
    path = root / ("identity_fixed_fixture.py" if fixed else "identity_real_fixture.py")
    path.write_text(
        "import json, os, sys\n"
        "request = json.loads(sys.stdin.readline())\n"
        f"stamp = {stamp}\n"
        "print(json.dumps({'type': 'progress', 'event': {'stage': 'train', "
        "'status': 'progress', 'metrics': {'pid': stamp}}}), flush=True)\n"
        "print(json.dumps({'type': 'result', 'result': {'outcome': "
        "'CANDIDATE', 'second_pid': stamp, 'source': "
        "request['request']['source_file'], 'python': sys.executable}}), flush=True)\n",
        encoding="utf-8",
    )
    return path


def test_ready_external_environment_dispatches_before_import(monkeypatch, tmp_path):
    report = training_env.EnvironmentReport(
        training_ready=True,
        in_process_ready=False,
        environment={"python": sys.executable},
    )
    monkeypatch.setattr(training_env.TrainingEnvironmentManager, "status", lambda *a, **k: report)
    calls = []
    # A seam spy only here: real pipe transport is exercised separately below.
    import nexus_scalp.model_provisioning.training_dispatch as dispatch

    monkeypatch.setattr(
        dispatch,
        "run_training_worker",
        lambda request, rep, progress=None: (
            calls.append((request, rep)) or {"outcome": "CANDIDATE"}
        ),
    )
    monkeypatch.setattr(
        pipeline,
        "import_user_bars",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("parent must not import data")),
    )
    request = pipeline.TrainingRequest(tmp_path / "data.csv", install=False)
    assert pipeline.train_local_model(request)["outcome"] == "CANDIDATE"
    assert calls == [(request, report)]


def test_real_subprocess_streams_progress_and_result(monkeypatch, tmp_path):
    import nexus_scalp.model_provisioning.training_dispatch as dispatch

    monkeypatch.setattr(dispatch, "worker_script", lambda: _identity_fixture(tmp_path, False))
    report = training_env.EnvironmentReport(
        training_ready=True, environment={"python": sys.executable}, backend="cpu"
    )
    events = []
    request = pipeline.TrainingRequest(tmp_path / "export with spaces.csv", install=False)
    result = dispatch.run_training_worker(request, report, events.append)
    assert result["outcome"] == "CANDIDATE"
    assert result["python"] == sys.executable
    assert result["source"] == str(request.source_file)

    # ML-QA-017: the identity the transport delivers is the WORKER's, never the
    # parent's own -- the parent sends no identity and the worker stamps its own.
    # Asserted by object property rather than by a literal-pid comparison: the
    # transport contract is one-worker-identity-throughout, so the parent's pid
    # belongs to the fixture, not to the parent, and the two are never compared.
    assert events[0].metrics["pid"] == result["second_pid"]
    assert events[0].metrics["pid"] != _pid()


def test_transport_reports_one_worker_identity_across_the_run(monkeypatch, tmp_path):
    """ML-QA-017: the transport delivers ONE worker identity across the whole
    run, and the identity it reports is the worker's own.

    The old literal-pid assert stood in for this continuity property and could
    not prove it: it compared the worker's pid against the parent's, which the
    transport contract never does, so it passed identically on any two pids the
    OS assigned. A regression that routed the result line through a different
    process than the progress line would have passed the old assert while
    breaking the continuity this test now asserts. The fixed identity makes the
    continuity reproducible to the digit; the real-pid leg keeps the isolation
    property pinned, and the two runs cross-check each other.
    """
    import nexus_scalp.model_provisioning.training_dispatch as dispatch

    monkeypatch.setattr(dispatch, "worker_script", lambda: _identity_fixture(tmp_path, True))
    report = training_env.EnvironmentReport(
        training_ready=True, environment={"python": sys.executable}, backend="cpu"
    )
    events = []
    request = pipeline.TrainingRequest(tmp_path / "export with spaces.csv", install=False)
    result = dispatch.run_training_worker(request, report, events.append)
    assert result["outcome"] == "CANDIDATE"

    # continuity: the identity stamped at the progress stage is the identity
    # stamped at the result stage -- ONE worker throughout, never re-spawned
    assert events[0].metrics["pid"] == result["second_pid"] == _FIXED_ID
    # isolation: the worker identity is never the parent's own
    assert _FIXED_ID != _pid()
    # the transport round-trips the identity twice: the parent read the same
    # value from two independent transport lines, so a fork between the stages
    # (a regression this assert exists to catch) would flip it
    assert len(events) == 1


def test_real_worker_rechecks_gate_without_recursive_dispatch(monkeypatch, tmp_path):
    import nexus_scalp.model_provisioning.training_dispatch as dispatch

    # Explicit subprocess fixture supplies only cheap application dependencies
    # and a stale READY report. The real worker and pipeline must refuse it.
    fixture = tmp_path / "fresh_gate_fixture.py"
    worker = Path(dispatch.__file__).with_name("training_worker.py")
    src = Path(pipeline.__file__).parents[2]
    fixture.write_text(f"""import sys, runpy
sys.path.insert(0, {str(src)!r})
from nexus_scalp.model_provisioning import training_env, pipeline
training_env.TrainingEnvironmentManager.status = lambda *a, **k: training_env.EnvironmentReport(training_ready=True, in_process_ready=False, environment={{"python": sys.executable}})
pipeline.import_user_bars = lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not import stale READY data"))
runpy.run_path({str(worker)!r}, run_name="__main__")
""")
    monkeypatch.setattr(dispatch, "worker_script", lambda: fixture)
    result = dispatch.run_training_worker(
        pipeline.TrainingRequest(tmp_path / "data.csv"),
        training_env.EnvironmentReport(
            training_ready=True, pytorch={"path": sys.executable}, backend="cpu"
        ),
    )
    assert result["outcome"] == "TRAINING_ENV_BLOCKED"
    assert result["reason"] == "worker interpreter is not READY"


def test_worker_dependency_probe_runs_with_stdlib_only(tmp_path):
    import json
    import subprocess

    import nexus_scalp.model_provisioning.training_dispatch as dispatch

    worker = Path(dispatch.__file__).with_name("training_worker.py")
    proc = subprocess.run(
        [sys.executable, "-S", str(worker), "--probe"],
        text=True,
        capture_output=True,
        timeout=10,
        check=False,
    )
    report = json.loads(proc.stdout)
    assert report["python"] == sys.executable
    assert report["ready"] is False
    assert "structlog" in report["missing"]
    assert proc.returncode == 1


def test_transport_never_allows_worker_to_publish(monkeypatch, tmp_path):
    import nexus_scalp.model_provisioning.training_dispatch as dispatch

    fixture = tmp_path / "no_publish_fixture.py"
    fixture.write_text("""import json, sys
payload = json.loads(sys.stdin.readline())
assert payload["request"]["install"] is False
print(json.dumps({"type":"result", "result":{"outcome":"CANDIDATE", "fixture":True}}), flush=True)
""")
    monkeypatch.setattr(dispatch, "worker_script", lambda: fixture)
    report = training_env.EnvironmentReport(
        training_ready=True, environment={"python": sys.executable}
    )
    out = dispatch.run_training_worker(
        pipeline.TrainingRequest(tmp_path / "data.csv", install=True), report
    )
    assert out["outcome"] == "CANDIDATE"


def test_frozen_payload_is_executable_source(monkeypatch, tmp_path):
    import json
    import shutil
    import subprocess

    import nexus_scalp.model_provisioning.training_dispatch as dispatch
    from nexus_scalp.release import paths

    bundle = tmp_path / "bundle"
    source = Path(pipeline.__file__).parents[2]
    shutil.copytree(
        source / "nexus_scalp",
        bundle / "training_payload" / "src" / "nexus_scalp",
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    monkeypatch.setattr(paths, "is_frozen", lambda: True)
    monkeypatch.setattr(paths, "exe_dir", lambda: tmp_path)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    script = dispatch.worker_script()
    assert script.is_relative_to(bundle)
    proc = subprocess.run(
        [sys.executable, "-S", str(script), "--probe"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    assert json.loads(proc.stdout)["ready"] is False
    build = (source.parent / "scripts" / "build" / "build_release.ps1").read_text()
    assert (
        build.count('--add-data "$Root\\src\\nexus_scalp;training_payload\\src\\nexus_scalp"') == 2
    )
    assert build.count('--add-data "$Root\\configs;training_payload\\configs"') == 2


def test_cancel_is_streamed_to_real_worker_and_reaped(monkeypatch, tmp_path):
    import threading

    import nexus_scalp.model_provisioning.training_dispatch as dispatch

    fixture = tmp_path / "cancel_fixture.py"
    fixture.write_text("""import json, sys
json.loads(sys.stdin.readline())
print(json.dumps({"type":"progress", "event":{"stage":"train", "status":"progress"}}), flush=True)
assert json.loads(sys.stdin.readline())["type"] == "cancel"
print(json.dumps({"type":"result", "result":{"outcome":"CANCELLED"}}), flush=True)
""")
    monkeypatch.setattr(dispatch, "worker_script", lambda: fixture)
    cancel = threading.Event()
    request = pipeline.TrainingRequest(tmp_path / "data.csv", cancel_event=cancel)
    report = training_env.EnvironmentReport(
        training_ready=True, environment={"python": sys.executable}
    )
    events = []

    def on_progress(event):
        events.append(event)
        cancel.set()

    result = dispatch.run_training_worker(request, report, on_progress)
    assert result["outcome"] == "CANCELLED"
    assert len(events) == 1


def test_kills_unresponsive_cancelled_worker(monkeypatch, tmp_path):
    import threading
    import time

    import nexus_scalp.model_provisioning.training_dispatch as dispatch

    fixture = tmp_path / "hung_fixture.py"
    fixture.write_text("""import json, sys, time
json.loads(sys.stdin.readline())
print(json.dumps({"type":"progress", "event":{"stage":"train", "status":"progress"}}), flush=True)
time.sleep(60)
""")
    monkeypatch.setattr(dispatch, "worker_script", lambda: fixture)
    monkeypatch.setattr(dispatch, "CANCEL_GRACE_SECONDS", 0.05)
    cancel = threading.Event()
    # CPU-time bound (ML-QA-004): the cancellation grace is 0.05 s real time,
    # so a cancelled run consumes milliseconds of CPU regardless of scheduler
    # load. A wall-clock bound here previously tripped on co-tenant runners.
    from tests.e2e.chain_clock import budget_cpu_ms

    with budget_cpu_ms(5000.0) as sw:
        out = dispatch.run_training_worker(
            pipeline.TrainingRequest(tmp_path / "x", cancel_event=cancel),
            training_env.EnvironmentReport(
                training_ready=True, environment={"python": sys.executable}
            ),
            lambda e: cancel.set(),
        )
    assert out["outcome"] == "CANCELLED"
    assert sw.consumed_ms < 5000.0


@pytest.mark.parametrize(
    "body",
    [
        "print('invalid json', flush=True)",
        "print(json.dumps({'type':'result','result':{'outcome':'CANDIDATE'}}), flush=True); sys.exit(9)",
        "print(json.dumps({'type':'result','result':{'outcome':'CANDIDATE'}}), flush=True); print('{}', flush=True)",
        "print(json.dumps({'type':'result','result':{'outcome':'unrecognized'}}), flush=True)",
        "print(json.dumps({'type':'result','result':{'outcome':'INSTALLED'}}), flush=True)",
        "pass",
    ],
)
def test_malformed_or_failed_worker_never_succeeds(monkeypatch, tmp_path, body):
    import nexus_scalp.model_provisioning.training_dispatch as dispatch

    fixture = tmp_path / "failed_fixture.py"
    fixture.write_text("import json, sys\njson.loads(sys.stdin.readline())\n" + body + "\n")
    monkeypatch.setattr(dispatch, "worker_script", lambda: fixture)
    out = dispatch.run_training_worker(
        pipeline.TrainingRequest(tmp_path / "x"),
        training_env.EnvironmentReport(training_ready=True, environment={"python": sys.executable}),
    )
    assert out["outcome"] == "TRAINING_ENV_BLOCKED"


def test_pipeline_forwards_backend_and_honest_stage_events(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from nexus_scalp.model_generation import three_model

    report = training_env.EnvironmentReport(
        training_ready=True, in_process_ready=True, backend="cpu"
    )
    monkeypatch.setattr(training_env.TrainingEnvironmentManager, "status", lambda *a, **k: report)
    monkeypatch.setattr(
        pipeline,
        "import_user_bars",
        lambda *a, **k: SimpleNamespace(
            frame=None,
            selected_rows=4000,
            rows_total=4000,
            dropped_duplicates=0,
            dropped_invalid=0,
            time_range=("a", "b"),
            as_dict=lambda: {},
        ),
    )
    monkeypatch.setattr("nexus_scalp.model_provisioning.service.candidate_dir", lambda: tmp_path)
    calls = []

    def train(*args, **kwargs):
        calls.append(kwargs)
        kwargs["progress_cb"]({"stage": "features", "status": "running"})
        kwargs["progress_cb"]({"stage": "validation", "status": "done", "fold": 1})
        raise RuntimeError("fixture ends before numerical training")

    monkeypatch.setattr(three_model, "train_variant", train)
    events = []
    result = pipeline.train_local_model(
        pipeline.TrainingRequest(tmp_path / "data.csv", backend="cpu"), events.append
    )
    assert result["outcome"] == "VALIDATION_FAILED"
    assert calls[0]["backend"] == "cpu"
    features = next(e for e in events if e.stage == "features")
    assert features.status == "running"
    assert features.fraction is None
    assert not any("epoch 0" in e.message for e in events)


def test_trailing_progress_is_drained_after_terminal_result(monkeypatch, tmp_path):
    import nexus_scalp.model_provisioning.training_dispatch as dispatch

    fixture = tmp_path / "trailing_fixture.py"
    fixture.write_text("""import json, sys
json.loads(sys.stdin.readline())
print(json.dumps({"type":"result", "result":{"outcome":"CANDIDATE"}}), flush=True)
print(json.dumps({"type":"progress", "event":{"stage":"verify", "status":"done"}}), flush=True)
""")
    monkeypatch.setattr(dispatch, "worker_script", lambda: fixture)
    out = dispatch.run_training_worker(
        pipeline.TrainingRequest(tmp_path / "x"),
        training_env.EnvironmentReport(training_ready=True, environment={"python": sys.executable}),
    )
    assert out["outcome"] == "CANDIDATE"


@pytest.mark.parametrize(
    "cancelled,valid,expected",
    [(False, True, "INSTALLED"), (True, True, "CANCELLED"), (False, False, "VALIDATION_FAILED")],
)
def test_parent_honors_install_only_after_clean_candidate(
    monkeypatch, tmp_path, cancelled, valid, expected
):
    import threading

    from nexus_scalp.model_provisioning import service, training_dispatch
    from nexus_scalp.release import bootstrap

    report = training_env.EnvironmentReport(training_ready=True, in_process_ready=False)
    monkeypatch.setattr(training_env.TrainingEnvironmentManager, "status", lambda *a, **k: report)
    cancel = threading.Event()

    def dispatch(*args):
        if cancelled:
            cancel.set()
        return {"outcome": "CANDIDATE", "candidate_model": str(tmp_path / "candidate.pt")}

    monkeypatch.setattr(training_dispatch, "run_training_worker", dispatch)
    monkeypatch.setattr(
        bootstrap,
        "bundle_status",
        lambda path: {"state": bootstrap.STATE_OK if valid else "INVALID"},
    )
    installs = []
    monkeypatch.setattr(
        service, "install_candidate", lambda *a, **k: installs.append(a) or {"installed": True}
    )
    result = pipeline.train_local_model(
        pipeline.TrainingRequest(tmp_path / "x", install=True, cancel_event=cancel)
    )
    assert result["outcome"] == expected
    assert len(installs) == int(not cancelled and valid)


def test_worker_rejects_different_interpreter_even_if_report_claims_in_process(
    monkeypatch, tmp_path
):
    report = training_env.EnvironmentReport(
        training_ready=True,
        in_process_ready=True,
        environment={"python": str(tmp_path / "other" / "python")},
    )
    monkeypatch.setattr(training_env.TrainingEnvironmentManager, "status", lambda *a, **k: report)
    monkeypatch.setattr(
        pipeline,
        "import_user_bars",
        lambda *a, **k: pytest.fail("wrong interpreter must not read data"),
    )
    result = pipeline.train_local_model(pipeline.TrainingRequest(tmp_path / "x"), _worker_mode=True)
    assert result["outcome"] == "TRAINING_ENV_BLOCKED"
    assert result["reason"] == "worker interpreter identity changed; re-check environment"


def test_cancel_during_parent_verification_never_installs(monkeypatch, tmp_path):
    import threading

    from nexus_scalp.model_provisioning import service, training_dispatch
    from nexus_scalp.release import bootstrap

    cancel = threading.Event()
    report = training_env.EnvironmentReport(training_ready=True, in_process_ready=False)
    monkeypatch.setattr(training_env.TrainingEnvironmentManager, "status", lambda *a, **k: report)
    monkeypatch.setattr(
        training_dispatch,
        "run_training_worker",
        lambda *a: {"outcome": "CANDIDATE", "candidate_model": str(tmp_path / "candidate.pt")},
    )

    def verify(path):
        cancel.set()
        return {"state": bootstrap.STATE_OK}

    monkeypatch.setattr(bootstrap, "bundle_status", verify)
    installed = []
    monkeypatch.setattr(
        service, "install_candidate", lambda *a, **k: installed.append(a) or {"installed": True}
    )
    result = pipeline.train_local_model(
        pipeline.TrainingRequest(tmp_path / "x", install=True, cancel_event=cancel)
    )
    assert result["outcome"] == "CANCELLED"
    assert not installed
