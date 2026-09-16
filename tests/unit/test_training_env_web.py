"""Training wizard contract: opt-in preparation, worker gating and single flight."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nexus_scalp.model_provisioning import dataset_source, pipeline, training_env
from nexus_scalp.web import provisioning_routes as routes


@pytest.fixture
def web(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXUS_IMPORT_ROOTS", str(tmp_path))
    file = tmp_path / "bars.csv"
    file.write_text("time,open\n1,2\n")
    monkeypatch.setattr(routes, "_ACTIVE", None)
    monkeypatch.setattr(routes, "_INSTALL_ACTIVE", False)
    monkeypatch.setattr(routes, "_OFFICIAL_ACTIVE", False)
    monkeypatch.setattr(routes.prov, "write_provisioner_state", lambda *a, **kw: None)
    tasks = []
    monkeypatch.setattr(
        routes.threading,
        "Thread",
        lambda **kw: SimpleNamespace(start=lambda: tasks.append(kw["target"])),
    )
    calls = []
    monkeypatch.setattr(dataset_source, "prepare_training_dataset", lambda **kw: kw["source_file"])
    monkeypatch.setattr(
        pipeline,
        "train_local_model",
        lambda request, **kw: calls.append(request) or {"outcome": "CANDIDATE"},
    )
    app = FastAPI()
    routes.register_provisioning_routes(
        app, lambda **kw: {"success": False, "error": kw}, lambda *a, **kw: None
    )
    return TestClient(app), {"file": str(file), "backend": "cpu"}, tasks, calls


def test_broker_source_prepared_in_worker_with_connected_adapter(web, monkeypatch, tmp_path):
    client, body, tasks, calls = web
    adapter = object()
    client.app.state.training_history_adapter = adapter
    prepared = tmp_path / "prepared.csv"
    received = []

    def prepare(**kw):
        received.append(kw)
        return prepared

    monkeypatch.setattr(dataset_source, "prepare_training_dataset", prepare)
    monkeypatch.setattr(
        training_env.TrainingEnvironmentManager,
        "status",
        lambda *a, **kw: training_env.EnvironmentReport(training_ready=True),
    )
    response = client.post(
        "/api/provisioning/train/start",
        json={"source": "broker", "symbol": "XAUUSD", "timeframe": "M1", "candles": 3000},
    ).json()
    assert response["started"]
    assert received == []
    tasks.pop()()
    assert received[0]["source"] == "broker"
    assert received[0]["adapter"] is adapter
    assert calls[0].source_file == prepared


def test_status_exposes_the_same_resolved_roots_used_for_file_validation(
    web, monkeypatch, tmp_path
):
    import os
    from pathlib import Path

    client, body, tasks, calls = web
    monkeypatch.chdir(tmp_path)
    extra = tmp_path / "operator imports"
    monkeypatch.setenv("NEXUS_IMPORT_ROOTS", os.pathsep.join((str(extra), str(extra))))
    monkeypatch.setattr(
        routes.prov,
        "FirstRunCoordinator",
        lambda: SimpleNamespace(
            slot=lambda: SimpleNamespace(as_dict=lambda: {}), recommended_action=lambda: {}
        ),
    )
    monkeypatch.setattr(routes.prov, "read_provisioner_state", lambda: {})
    response = client.get("/api/provisioning/status").json()
    roots = response["allowed_import_roots"]
    assert roots == [str(extra), str(tmp_path / "data/imports"), str(tmp_path / "data/raw")]
    for root in roots:
        folder = Path(root)
        folder.mkdir(parents=True, exist_ok=True)
        export = folder / "bars.csv"
        export.write_text("time,open\n1,2\n")
        assert routes._allowed_import_path(str(export)) == export
    outside = tmp_path / "outside.csv"
    outside.write_text("time,open\n1,2\n")
    with pytest.raises(ValueError, match="outside allowed"):
        routes._allowed_import_path(str(outside))


def test_broker_candles_required_upfront(web, monkeypatch):
    client, body, tasks, calls = web
    response = client.post("/api/provisioning/train/start", json={"source": "broker"}).json()
    assert response["error"]["code"] == "TRAIN_INPUT_REJECTED"
    assert tasks == [] and calls == []


def test_missing_broker_adapter_reports_actionable_failure(web, monkeypatch):
    client, body, tasks, calls = web

    def missing_broker(**kwargs):
        raise dataset_source.DatasetSourceError("No broker history available; use a file")

    monkeypatch.setattr(dataset_source, "prepare_training_dataset", missing_broker)
    monkeypatch.setattr(
        training_env.TrainingEnvironmentManager,
        "status",
        lambda *a, **kw: training_env.EnvironmentReport(training_ready=True),
    )
    response = client.post(
        "/api/provisioning/train/start", json={"source": "broker", "candles": 3000}
    ).json()
    assert response["started"]
    tasks.pop()()
    result = client.get("/api/provisioning/train/progress").json()["result"]
    assert result["outcome"] == "DATASET_BLOCKED"
    assert "file" in result["reason"]
    assert calls == []


def test_ready_managed_environment_checks_only_in_worker(web, monkeypatch):
    client, body, tasks, calls = web
    probes = []

    def status(self, backend=None):
        probes.append(backend)
        return training_env.EnvironmentReport(training_ready=True, in_process_ready=False)

    monkeypatch.setattr(training_env.TrainingEnvironmentManager, "status", status)
    assert client.post("/api/provisioning/train/start", json=body).json()["started"]
    assert probes == []
    tasks.pop()()
    assert len(calls) == 1
    assert not client.get("/api/provisioning/train/progress").json()["active"]


@pytest.mark.parametrize("consent", [True, False, "true"])
def test_preparation_requires_explicit_boolean_consent(web, monkeypatch, consent):
    client, body, tasks, calls = web
    operations = []
    blocked = training_env.EnvironmentReport(
        checks=[
            training_env.EnvCheck(
                "pytorch", False, "TORCH_NOT_INSTALLED", remedy="Install dependencies"
            )
        ]
    )
    ready = training_env.EnvironmentReport(training_ready=True)

    def status(self, backend=None):
        operations.append("status")
        return ready if "install" in operations else blocked

    def install(self, backend=None):
        operations.append("install")
        return ready

    monkeypatch.setattr(training_env.TrainingEnvironmentManager, "status", status)
    monkeypatch.setattr(training_env.TrainingEnvironmentManager, "install", install)
    assert client.post(
        "/api/provisioning/train/start", json={**body, "prepare_environment": consent}
    ).json()["started"]
    tasks.pop()()
    result = client.get("/api/provisioning/train/progress").json()
    if consent is True:
        assert operations == ["status", "install", "status"]
        assert len(calls) == 1
    else:
        assert operations == ["status"]
        assert calls == []
        assert result["result"]["outcome"] == "TRAINING_ENV_BLOCKED"
        assert result["result"]["report"]["checks"][0]["remedy"] == "Install dependencies"
    assert result["active"] is False


def test_non_csv_input_rejected_without_thread(web):
    client, body, tasks, calls = web
    r = client.post("/api/provisioning/train/start", json={**body, "file": body["file"] + ".exe"})
    assert r.json()["error"]["code"] == "TRAIN_INPUT_REJECTED"
    assert tasks == [] and calls == []
    assert client.get("/api/provisioning/train/progress").json()["active"] is False


def test_second_start_rejected_while_run_active(web):
    client, body, tasks, calls = web
    assert client.post("/api/provisioning/train/start", json=body).json()["started"]
    r = client.post("/api/provisioning/train/start", json=body)
    assert r.json()["error"]["code"] == "TRAIN_ALREADY_RUNNING"
    assert calls == []


@pytest.mark.parametrize("install_first", [True, False])
def test_install_and_training_mutually_exclusive(web, monkeypatch, install_first):
    client, body, tasks, calls = web
    installs = []
    monkeypatch.setattr(
        training_env.TrainingEnvironmentManager,
        "install",
        lambda *a, **kw: installs.append(1) or training_env.EnvironmentReport(),
    )
    if install_first:
        monkeypatch.setattr(routes, "_INSTALL_ACTIVE", True)
        response = client.post("/api/provisioning/train/start", json=body).json()
        assert response["error"]["code"] == "INSTALL_ALREADY_RUNNING"
        assert tasks == []
    else:
        assert client.post("/api/provisioning/train/start", json=body).json()["started"]
        response = client.post(
            "/api/provisioning/environment/install", json={"backend": "cpu"}
        ).json()
        assert response["error"]["code"] == "TRAIN_ALREADY_RUNNING"
        assert installs == []


def test_official_download_mutually_exclusive_with_train_and_install(web, monkeypatch):
    client, body, tasks, calls = web
    downloads = []
    coord = SimpleNamespace(
        slot=lambda: SimpleNamespace(as_dict=lambda: {}),
        download_official=lambda: downloads.append(1) or {"servable": True},
    )
    monkeypatch.setattr(routes.prov, "FirstRunCoordinator", lambda **kw: coord)

    # 1. When training is running, official download is rejected
    assert client.post("/api/provisioning/train/start", json=body).json()["started"]
    res = client.post("/api/provisioning/official").json()
    assert res["error"]["code"] == "TRAIN_ALREADY_RUNNING"
    assert downloads == []

    # Reset active train
    assert routes._ACTIVE is not None
    routes._ACTIVE.done.set()
    tasks.clear()

    # 2. When install is active, official download is rejected
    monkeypatch.setattr(routes, "_INSTALL_ACTIVE", True)
    res = client.post("/api/provisioning/official").json()
    assert res["error"]["code"] == "INSTALL_ALREADY_RUNNING"
    assert downloads == []
    monkeypatch.setattr(routes, "_INSTALL_ACTIVE", False)

    # 3. When official is active:
    monkeypatch.setattr(routes, "_OFFICIAL_ACTIVE", True)
    # 3a. another official call is rejected
    res = client.post("/api/provisioning/official").json()
    assert res["error"]["code"] == "OFFICIAL_ALREADY_RUNNING"
    # 3b. train start is rejected
    res = client.post("/api/provisioning/train/start", json=body).json()
    assert res["error"]["code"] == "OFFICIAL_ALREADY_RUNNING"
    assert tasks == []
    # 3c. env install is rejected
    res = client.post("/api/provisioning/environment/install", json={"backend": "cpu"}).json()
    assert res["error"]["code"] == "OFFICIAL_ALREADY_RUNNING"
    assert downloads == []


@pytest.mark.parametrize("outcome", ["success", "rejected", "unexpected", "constructor"])
def test_official_download_releases_reservation_in_finally(web, monkeypatch, outcome):
    client, body, tasks, calls = web

    def download():
        assert routes._OFFICIAL_ACTIVE is True
        # Exercise the actual reserved route, not only manually set flags.
        for endpoint in ("official", "train/start", "environment/install"):
            response = client.post("/api/provisioning/" + endpoint, json=body).json()
            assert response["error"]["code"] == "OFFICIAL_ALREADY_RUNNING"
        assert tasks == []
        if outcome == "rejected":
            raise routes.OfficialBundleError("CORRUPT", "checksum failed")
        if outcome == "unexpected":
            raise RuntimeError("private failure")
        return {"servable": True}

    def coordinator(**kw):
        assert routes._OFFICIAL_ACTIVE is True
        if outcome == "constructor":
            raise RuntimeError("constructor failure")
        return SimpleNamespace(download_official=download)

    monkeypatch.setattr(routes.prov, "FirstRunCoordinator", coordinator)
    res = client.post("/api/provisioning/official").json()
    assert res["success"] is (outcome == "success")
    if outcome != "success":
        assert res["error"]["code"] == (
            "OFFICIAL_BUNDLE_REJECTED" if outcome == "rejected" else "PROVISIONING_OFFICIAL_ERROR"
        )
    assert routes._OFFICIAL_ACTIVE is False
    assert client.post("/api/provisioning/train/start", json=body).json()["started"]
    assert len(tasks) == 1


def test_engine_without_adapter_cannot_open_an_owned_connection(web):
    client, body, tasks, calls = web
    client.app.state.engine = SimpleNamespace()
    response = client.post(
        "/api/provisioning/train/start", json={"source": "broker", "candles": 3000}
    ).json()
    assert response["error"]["code"] == "TRAIN_INPUT_REJECTED"
    assert "borrowable" in response["error"]["detail"]
    assert tasks == []


def test_first_setup_without_engine_passes_none_to_scoped_history_helper(web, monkeypatch):
    client, body, tasks, calls = web
    received = []
    monkeypatch.setattr(
        training_env.TrainingEnvironmentManager,
        "status",
        lambda *a, **kw: training_env.EnvironmentReport(training_ready=True),
    )
    monkeypatch.setattr(
        dataset_source,
        "prepare_training_dataset",
        lambda **kw: received.append(kw) or body["file"],
    )
    response = client.post(
        "/api/provisioning/train/start", json={"source": "broker", "candles": 3000}
    ).json()
    assert response["started"]
    tasks.pop()()
    assert received[0]["adapter"] is None
    assert received[0]["source"] == "broker"
    assert len(calls) == 1


def test_dataset_cancellation_remains_cancelled(web, monkeypatch):
    client, body, tasks, calls = web
    monkeypatch.setattr(
        training_env.TrainingEnvironmentManager,
        "status",
        lambda *a, **kw: training_env.EnvironmentReport(training_ready=True),
    )

    def cancel(**kw):
        raise pipeline.TrainingCancelledError("cancelled")

    monkeypatch.setattr(dataset_source, "prepare_training_dataset", cancel)
    assert client.post("/api/provisioning/train/start", json=body).json()["started"]
    tasks.pop()()
    response = client.get("/api/provisioning/train/progress").json()
    assert response["result"]["outcome"] == "CANCELLED"
    assert response["events"][-1]["status"] == "cancelled"
    assert not response["active"]
    assert calls == []


def test_install_failure_releases_reservation_for_official(web, monkeypatch):
    client, body, tasks, calls = web

    def fail(*a, **kw):
        assert routes._INSTALL_ACTIVE
        response = client.post("/api/provisioning/official").json()
        assert response["error"]["code"] == "INSTALL_ALREADY_RUNNING"
        raise RuntimeError("install failed")

    monkeypatch.setattr(training_env.TrainingEnvironmentManager, "install", fail)
    monkeypatch.setattr(
        routes.prov,
        "FirstRunCoordinator",
        lambda: SimpleNamespace(download_official=lambda: {"servable": True}),
    )
    response = client.post("/api/provisioning/environment/install", json=body).json()
    assert response["error"]["code"] == "PROVISIONING_INSTALL_ERROR"
    assert not routes._INSTALL_ACTIVE
    assert client.post("/api/provisioning/official").json()["success"]


def test_training_launch_failure_releases_reservation_for_official(web, monkeypatch):
    client, body, tasks, calls = web

    def fail(**kw):
        raise RuntimeError("thread launch failed")

    monkeypatch.setattr(routes.threading, "Thread", fail)
    monkeypatch.setattr(
        routes.prov,
        "FirstRunCoordinator",
        lambda: SimpleNamespace(download_official=lambda: {"servable": True}),
    )
    response = client.post("/api/provisioning/train/start", json=body).json()
    assert response["error"]["code"] == "TRAIN_WORKER_START_ERROR"
    assert routes._ACTIVE is None
    assert client.post("/api/provisioning/official").json()["success"]


@pytest.mark.parametrize("endpoint", ["environment", "environment/install", "train/start"])
@pytest.mark.parametrize("backend", ["auto", "tpu", ["cpu"]])
def test_backend_validation_at_every_endpoint(web, monkeypatch, endpoint, backend):
    client, body, tasks, calls = web
    probes = []

    def report(self, backend=None):
        probes.append(backend)
        return training_env.EnvironmentReport(training_ready=True)

    monkeypatch.setattr(training_env.TrainingEnvironmentManager, "status", report)
    monkeypatch.setattr(training_env.TrainingEnvironmentManager, "install", report)
    if endpoint == "environment":
        response = client.get(
            "/api/provisioning/" + endpoint, params={"backend": str(backend)}
        ).json()
    else:
        response = client.post(
            "/api/provisioning/" + endpoint, json={**body, "backend": backend}
        ).json()
    if backend == "auto":
        assert response["success"]
        if tasks:
            tasks.pop()()
        assert probes == [None]
    else:
        assert not response["success"]
        assert probes == [] and tasks == []


@pytest.mark.parametrize("typed", [True, False])
def test_discovery_error_releases_run_and_reports_environment_failure(web, monkeypatch, typed):
    client, body, tasks, calls = web

    def fail(*a, **kw):
        if typed:
            raise training_env.TrainingEnvironmentError(
                training_env.EnvCode.PYTHON_NOT_FOUND, "No supported Python"
            )
        raise RuntimeError("private file path")

    monkeypatch.setattr(training_env.TrainingEnvironmentManager, "status", fail)
    assert client.post("/api/provisioning/train/start", json=body).json()["started"]
    tasks.pop()()
    response = client.get("/api/provisioning/train/progress").json()
    assert not response["active"]
    assert calls == []
    assert response["events"][-1]["stage"] == "environment"
    assert response["result"]["outcome"] == "TRAINING_ENV_BLOCKED"
    assert "private" not in str(response)
    assert client.post("/api/provisioning/train/start", json=body).json()["started"]


def test_cancel_during_discovery_prevents_install_and_training(web, monkeypatch):
    client, body, tasks, calls = web

    def status(*a, **kw):
        routes._ACTIVE.cancel.set()
        return training_env.EnvironmentReport()

    monkeypatch.setattr(training_env.TrainingEnvironmentManager, "status", status)
    monkeypatch.setattr(
        training_env.TrainingEnvironmentManager,
        "install",
        lambda *a, **kw: pytest.fail("cancelled preparation must not install"),
    )
    assert client.post(
        "/api/provisioning/train/start", json={**body, "prepare_environment": True}
    ).json()["started"]
    tasks.pop()()
    assert calls == []
    assert client.get("/api/provisioning/train/progress").json()["result"]["outcome"] == "CANCELLED"


def test_file_source_uses_same_dataset_preparer(web, monkeypatch, tmp_path):
    client, body, tasks, calls = web
    received = []
    prepared = tmp_path / "normalized.csv"
    monkeypatch.setattr(
        training_env.TrainingEnvironmentManager,
        "status",
        lambda *a, **k: training_env.EnvironmentReport(training_ready=True),
    )
    monkeypatch.setattr(
        dataset_source, "prepare_training_dataset", lambda **kw: received.append(kw) or prepared
    )
    assert client.post("/api/provisioning/train/start", json=body).json()["started"]
    tasks.pop()()
    assert received and received[0]["source"] == "file"
    assert calls[0].source_file == prepared


def test_active_engine_adapter_is_borrowed(web, monkeypatch, tmp_path):
    client, body, tasks, calls = web
    adapter = object()
    client.app.state.engine = SimpleNamespace(adapter=adapter)
    received = []
    monkeypatch.setattr(
        training_env.TrainingEnvironmentManager,
        "status",
        lambda *a, **k: training_env.EnvironmentReport(training_ready=True),
    )
    monkeypatch.setattr(
        dataset_source,
        "prepare_training_dataset",
        lambda **kw: received.append(kw) or tmp_path / "bars.csv",
    )
    client.post("/api/provisioning/train/start", json={"source": "broker", "candles": 3000})
    tasks.pop()()
    assert received[0]["adapter"] is adapter


def test_invalid_dataset_preflight_never_installs(web, monkeypatch):
    client, body, tasks, calls = web
    response = client.post(
        "/api/provisioning/train/start", json={**body, "candles": 1, "prepare_environment": True}
    ).json()
    assert not response["success"]
    assert tasks == []
