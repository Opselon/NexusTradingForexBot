"""PR247 training and PR250 official setup must coexist on the HTTP surface.

Side-effect boundaries are replaced: no downloads, installs, training or broker I/O.
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nexus_scalp.model_provisioning import dataset_source, pipeline, training_env
from nexus_scalp.web import provisioning_routes as routes


@pytest.fixture
def setup_web(monkeypatch, tmp_path):
    monkeypatch.setenv("NEXUS_IMPORT_ROOTS", str(tmp_path))
    file = tmp_path / "bars.csv"
    file.write_text("time,open\n1,2\n")
    monkeypatch.setattr(routes, "_ACTIVE", None)
    monkeypatch.setattr(routes, "_INSTALL_ACTIVE", False)
    monkeypatch.setattr(routes, "_OFFICIAL_ACTIVE", False, raising=False)
    monkeypatch.setattr(routes.prov, "write_provisioner_state", lambda *a, **kw: None)
    tasks = []
    monkeypatch.setattr(
        routes.threading,
        "Thread",
        lambda **kw: SimpleNamespace(start=lambda: tasks.append(kw["target"])),
    )
    monkeypatch.setattr(
        training_env.TrainingEnvironmentManager,
        "status",
        lambda *a, **kw: training_env.EnvironmentReport(
            training_ready=True, in_process_ready=False
        ),
    )
    monkeypatch.setattr(
        training_env.TrainingEnvironmentManager,
        "install",
        lambda *a, **kw: pytest.fail("No consent: must not install dependencies"),
    )
    monkeypatch.setattr(dataset_source, "prepare_training_dataset", lambda **kw: kw["source_file"])
    trained = []
    monkeypatch.setattr(
        pipeline,
        "train_local_model",
        lambda request, **kw: trained.append(request) or {"outcome": "CANDIDATE"},
    )
    app = FastAPI()
    routes.register_provisioning_routes(
        app, lambda **kw: {"success": False, "error": kw}, lambda *a, **kw: None
    )
    return TestClient(app), {"file": str(file), "backend": "cpu"}, tasks, trained


@pytest.mark.parametrize(
    ("code", "message"),
    [
        ("OFFICIAL_MODEL_NOT_PUBLISHED", "not published"),
        ("VERIFICATION_PENDING", "supported runtime"),
    ],
)
def test_official_typed_failure_releases_reservation_for_managed_training(
    setup_web, monkeypatch, code, message
):
    client, body, tasks, trained = setup_web

    def download():
        # Exercise both features while the official route actually owns its lock.
        assert routes._OFFICIAL_ACTIVE
        for endpoint in ("official", "train/start", "environment/install"):
            blocked = client.post("/api/provisioning/" + endpoint, json=body).json()
            assert blocked["error"]["code"] == "OFFICIAL_ALREADY_RUNNING"
        raise routes.OfficialBundleError(code, "private diagnostic /secret/path")

    monkeypatch.setattr(
        routes.prov, "FirstRunCoordinator", lambda: SimpleNamespace(download_official=download)
    )
    result = client.post("/api/provisioning/official", json={}).json()
    assert result["error"]["code"] == "OFFICIAL_BUNDLE_REJECTED"
    assert result["error"]["step"] == code
    assert message in result["error"]["message"]
    assert "private" not in str(result) and "/secret" not in str(result)
    assert not routes._OFFICIAL_ACTIVE
    assert client.post("/api/provisioning/train/start", json=body).json()["started"]
    assert trained == []
    tasks.pop()()
    assert len(trained) == 1
    progress = client.get("/api/provisioning/train/progress").json()
    assert progress["active"] is False
    assert progress["result"]["outcome"] == "CANDIDATE"
    assert any(e["stage"] == "environment" and e["status"] == "done" for e in progress["events"])


def test_engine_guard_does_not_leak_reservation_or_disable_local_training(setup_web, monkeypatch):
    client, body, tasks, trained = setup_web
    client.app.state.engine = object()
    monkeypatch.setattr(
        routes.prov,
        "FirstRunCoordinator",
        lambda: pytest.fail("Engine guard must run before coordinator construction"),
    )
    result = client.post("/api/provisioning/official", json={}).json()
    assert result["error"]["code"] == "MODEL_INSTALL_ENGINE_RUNNING"
    assert not routes._OFFICIAL_ACTIVE
    assert client.post("/api/provisioning/train/start", json=body).json()["started"]
    tasks.pop()()
    assert len(trained) == 1
