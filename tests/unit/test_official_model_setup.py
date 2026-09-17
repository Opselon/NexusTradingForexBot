"""First Setup integration boundaries; no remote publication or live engine."""

import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from nexus_scalp.model_provisioning import OfficialBundleError
from nexus_scalp.model_provisioning import service as prov
from nexus_scalp.web import provisioning_routes as routes


def test_unpublished_official_has_actionable_safe_message(monkeypatch):
    def absent(self):
        raise OfficialBundleError("OFFICIAL_MODEL_NOT_PUBLISHED", "private diagnostic")

    monkeypatch.setattr(prov.FirstRunCoordinator, "download_official", absent)
    monkeypatch.setattr(prov, "write_provisioner_state", lambda *a, **kw: None)
    app = FastAPI()
    routes.register_provisioning_routes(
        app, lambda **kw: {"success": False, "error": kw}, lambda *a, **kw: None
    )
    with TestClient(app) as client:
        body = client.post("/api/provisioning/official", json={}).json()
    assert body["success"] is False
    assert body["error"]["step"] == "OFFICIAL_MODEL_NOT_PUBLISHED"
    assert "not published" in body["error"]["message"].lower()
    assert "private diagnostic" not in str(body)


def test_wizard_inline_javascript_compiles(tmp_path):
    from html.parser import HTMLParser

    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js unavailable")
    html = Path("Web/first_setup.html").read_text(encoding="utf-8")

    class ScriptCollector(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.scripts: list[str] = []
            self._in_script = False
            self._chunks: list[str] = []

        def handle_starttag(self, tag, attrs):
            if tag == "script":
                self._in_script = True
                self._chunks = []

        def handle_endtag(self, tag):
            if tag == "script" and self._in_script:
                self._in_script = False
                self.scripts.append("".join(self._chunks))

        def handle_data(self, data):
            if self._in_script:
                self._chunks.append(data)

    collector = ScriptCollector()
    collector.feed(html)
    collector.close()
    scripts = collector.scripts
    assert scripts
    path = tmp_path / "wizard.cjs"
    path.write_text("\n".join(scripts), encoding="utf-8")
    result = subprocess.run(
        [node, "--check", str(path)], capture_output=True, text=True, check=False
    )

    assert result.returncode == 0, result.stderr


def test_official_route_refuses_when_engine_attached(monkeypatch):
    calls = []

    def forbidden(self):
        calls.append(True)
        return {"servable": True}

    monkeypatch.setattr(prov.FirstRunCoordinator, "download_official", forbidden)
    app = FastAPI()
    app.state.engine = object()
    routes.register_provisioning_routes(
        app, lambda **kw: {"success": False, "error": kw}, lambda *a, **kw: None
    )
    with TestClient(app) as client:
        body = client.post("/api/provisioning/official", json={}).json()
    assert body["error"]["code"] == "MODEL_INSTALL_ENGINE_RUNNING"


def test_wizard_distinguishes_configured_source_from_publication():
    html = Path("Web/first_setup.html").read_text(encoding="utf-8")
    assert "publication not checked" in html
    assert "works without PyTorch installed" not in html
    assert "install includes the inference runtime" in html


def test_coordinator_uses_transaction_result_and_cleans_download(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from nexus_scalp.model_provisioning import official as off

    stage = tmp_path / "download"
    stage.mkdir()
    manifest = {"bundle_id": "fixture", "model_version": "1.0.0"}
    verified = SimpleNamespace(dir=stage, manifest=manifest, bundle_id="fixture")
    source = SimpleNamespace(download_and_verify=lambda **kw: verified)
    monkeypatch.setattr(prov, "write_provisioner_state", lambda *a, **kw: None)
    monkeypatch.setattr(prov, "serving_model_path", lambda: tmp_path / "slot" / "model.pt")
    monkeypatch.setattr(
        off,
        "install_verified_bundle",
        lambda *a, **kw: {
            "installed": True,
            "servable": True,
            "bundle_id": "fixture",
            "model_version": "1.0.0",
            "model_sha256": "a" * 64,
        },
    )
    # Transaction already reverified under its lock. A post-lock second probe
    # can race another writer and cannot roll back; coordinator must use receipt.
    from nexus_scalp.release import bootstrap as rb

    monkeypatch.setattr(rb, "bundle_status", lambda *a: {"state": "INVALID"})
    result = prov.FirstRunCoordinator(official=source).download_official()
    assert result["servable"] is True
    assert result["model_version"] == "1.0.0"
    assert not stage.exists()


def test_coordinator_install_failure_cleans_download(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from nexus_scalp.model_provisioning import official as off

    stage = tmp_path / "download"
    stage.mkdir()
    verified = SimpleNamespace(dir=stage, bundle_id="fixture")
    source = SimpleNamespace(download_and_verify=lambda **kw: verified)
    states = []
    monkeypatch.setattr(prov, "write_provisioner_state", lambda *a, **kw: states.append(a[0]))
    monkeypatch.setattr(prov, "serving_model_path", lambda: tmp_path / "slot" / "model.pt")

    def refuse(*a, **kw):
        raise OfficialBundleError("INSTALL_FAILED", "test")

    monkeypatch.setattr(off, "install_verified_bundle", refuse)
    with pytest.raises(OfficialBundleError):
        prov.FirstRunCoordinator(official=source).download_official()
    assert not stage.exists()
    assert states[-1] == "rejected"
