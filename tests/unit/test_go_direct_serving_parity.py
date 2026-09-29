"""Unit parity suite: Go direct serving vs Python reference app (Wave 4).

Validates byte/schema parity according to Master Spec §8 & §9 for Candidate routes:
- GET /api/v1/features/contract
- GET /api/v1/features/groups
- GET /api/v1/config/schema
- GET /api/v1/system/capabilities
- GET /api/v1/system/version

Verifies that Go direct handlers return contract-identical envelopes, schemas,
and invariant values while asserting proper routing headers:
  X-NSE-Routing-Target: go-direct
  X-NSE-Routing-Reason: stateless-2xx
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from nexus_scalp.web.api_v1_wiring import create_v1_app

REPO_ROOT = Path(__file__).resolve().parents[2]
GO_API_DIR = REPO_ROOT / "go-api"


def _find_go_binary() -> str | None:
    """Locate go.exe from toolchain or environment."""
    candidates = [
        os.environ.get("GOROOT", "") + "/bin/go.exe",
        "C:/Users/Capsizer/go-toolchain/go/bin/go.exe",
        shutil.which("go.exe") or "",
        shutil.which("go") or "",
    ]
    for c in candidates:
        if c and Path(c).is_file():
            return str(Path(c).resolve())
    return None


def _get_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _safe_unlink(path: Path) -> None:
    for _ in range(20):
        try:
            if path.exists():
                path.unlink()
            return
        except OSError:
            time.sleep(0.1)


GO_SERVER_SOURCE = r"""package main

import (
	"flag"
	"fmt"
	"net/http"

	"github.com/Opselon/NexusTradingForexBot/go-api/internal/api/handlers"
	"github.com/Opselon/NexusTradingForexBot/go-api/internal/web"
)

func main() {
	addr := flag.String("addr", ":8087", "listen address")
	flag.Parse()

	c := handlers.NewContracts(nil)
	sd := handlers.NewSystemDirect(nil)
	ps := web.NewPlatformStatic(nil)

	mux := http.NewServeMux()
	mux.HandleFunc("/api/v1/features/contract", c.FeatureContract)
	mux.HandleFunc("/api/v1/features/groups", c.FeatureGroups)
	mux.HandleFunc("/api/v1/config/schema", c.ConfigSchema)
	mux.HandleFunc("/api/v1/system/capabilities", sd.Capabilities)
	mux.HandleFunc("/api/v1/system/version", sd.Version)
	mux.HandleFunc("/openapi.json", ps.ServeOpenAPI)
	mux.HandleFunc("/docs", ps.ServeDocs)
	mux.HandleFunc("/redoc", ps.ServeReDoc)

	fmt.Printf("READY on %s\n", *addr)
	http.ListenAndServe(*addr, mux)
}
"""


@pytest.fixture(scope="module")
def go_direct_server() -> Generator[str, None, None]:
    """Compile and run ephemeral Go direct server, yielding base URL."""
    go_bin = _find_go_binary()
    if not go_bin:
        pytest.skip("Go compiler not found on system")

    port = _get_free_port()
    tmp_dir = Path(tempfile.gettempdir())
    tmp_exe = tmp_dir / f"wave4_direct_parity_{port}.exe"
    src_file = GO_API_DIR / f"parity_server_{port}.go"

    src_file.write_text(GO_SERVER_SOURCE, encoding="utf-8")
    env = os.environ.copy()
    env.update(
        {
            "GOROOT": "C:/Users/Capsizer/go-toolchain/go",
            "GOPATH": "C:/Users/Capsizer/go",
            "GOMODCACHE": "C:/Users/Capsizer/go/pkg/mod",
            "GOFLAGS": "-mod=mod",
            "GOOS": "windows",
        }
    )

    build_res = subprocess.run(
        [go_bin, "build", "-o", str(tmp_exe), src_file.name],
        cwd=str(GO_API_DIR),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    _safe_unlink(src_file)

    if build_res.returncode != 0:
        pytest.fail(f"Go direct server build failed: {build_res.stderr}")

    proc = subprocess.Popen(
        [str(tmp_exe), f"-addr=127.0.0.1:{port}"],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    base_url = f"http://127.0.0.1:{port}"
    ready = False
    for _ in range(50):
        try:
            req = urllib.request.Request(f"{base_url}/api/v1/features/contract")
            with urllib.request.urlopen(req, timeout=1) as resp:
                if resp.status == 200:
                    ready = True
                    break
        except Exception:
            time.sleep(0.1)

    if not ready:
        proc.terminate()
        proc.wait()
        _safe_unlink(tmp_exe)
        pytest.fail("Go direct server failed to become ready")

    yield base_url

    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
    _safe_unlink(tmp_exe)


@pytest.fixture(scope="module")
def py_client() -> TestClient:
    """FastAPI TestClient referencing embedded v1 app."""
    return TestClient(create_v1_app())


def _get_go(
    base_url: str, path: str, headers: dict[str, str] | None = None
) -> tuple[int, dict[str, str], dict[str, Any]]:
    req = urllib.request.Request(f"{base_url}{path}")
    if headers:
        for k, v in headers.items():
            req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=5) as resp:
        resp_headers = {k.lower(): v for k, v in resp.headers.items()}
        body = json.loads(resp.read().decode("utf-8"))
        return resp.status, resp_headers, body


def test_features_contract_parity(go_direct_server: str, py_client: TestClient) -> None:
    path = "/api/v1/features/contract"
    py_resp = py_client.get(path)
    assert py_resp.status_code == 200
    py_body = py_resp.json()

    go_status, go_headers, go_body = _get_go(go_direct_server, path)
    assert go_status == 200

    # Routing headers
    assert go_headers.get("x-nse-routing-target") == "go-direct"
    assert go_headers.get("x-nse-routing-reason") == "stateless-2xx"
    assert "application/json" in go_headers.get("content-type", "")

    # Envelope keys
    assert set(go_body.keys()) == set(py_body.keys()) == {"data", "meta"}
    assert "request_id" in go_body["meta"]
    assert "generated_at" in go_body["meta"]

    # Data schema parity
    go_data, py_data = go_body["data"], py_body["data"]
    assert set(go_data.keys()) == set(py_data.keys())

    # Exact field comparisons
    assert go_data["schema_id"] == py_data["schema_id"] == "scalp_v3"
    assert go_data["feature_count"] == py_data["feature_count"] == 70
    assert go_data["feature_schema_hash"] == py_data["feature_schema_hash"] == "235b8fccc96b7e0e"
    assert go_data["registry_canonical"] is py_data["registry_canonical"] is True
    assert go_data["first_10_names"] == py_data["first_10_names"]
    assert set(go_data["groups"].keys()) == set(py_data["groups"].keys())
    for g_key in ("base_0_49", "news_50_59", "liquidity_60_69"):
        assert go_data["groups"][g_key]["count"] == py_data["groups"][g_key]["count"]


def test_features_groups_parity(go_direct_server: str, py_client: TestClient) -> None:
    path = "/api/v1/features/groups"
    py_resp = py_client.get(path)
    assert py_resp.status_code == 200
    py_body = py_resp.json()

    go_status, go_headers, go_body = _get_go(go_direct_server, path)
    assert go_status == 200
    assert go_headers.get("x-nse-routing-target") == "go-direct"

    # Envelope
    assert set(go_body.keys()) == set(py_body.keys()) == {"data", "meta"}

    go_data, py_data = go_body["data"], py_body["data"]
    assert set(go_data.keys()) == set(py_data.keys()) == {"dimension", "families"}
    assert go_data["dimension"] == py_data["dimension"] == 70

    go_fams, py_fams = go_data["families"], py_data["families"]
    assert set(go_fams.keys()) == set(py_fams.keys()) == {"base", "liquidity", "news"}
    for fam in ("base", "liquidity", "news"):
        assert go_fams[fam]["count"] == py_fams[fam]["count"]
        assert go_fams[fam]["names"] == py_fams[fam]["names"]


def test_config_schema_parity(go_direct_server: str, py_client: TestClient) -> None:
    path = "/api/v1/config/schema"
    py_resp = py_client.get(path)
    assert py_resp.status_code == 200
    py_body = py_resp.json()

    go_status, go_headers, go_body = _get_go(go_direct_server, path)
    assert go_status == 200
    assert go_headers.get("x-nse-routing-target") == "go-direct"

    # Envelope
    assert set(go_body.keys()) == set(py_body.keys()) == {"data", "meta"}

    go_data, py_data = go_body["data"], py_body["data"]
    assert set(go_data.keys()) == set(py_data.keys()) == {"sections", "count", "at"}
    assert go_data["count"] == py_data["count"] == 14

    go_secs = go_data["sections"]
    py_secs = py_data["sections"]
    assert len(go_secs) == len(py_secs) == 14

    for i in range(14):
        gs, ps = go_secs[i], py_secs[i]
        assert gs["name"] == ps["name"]
        assert gs["required"] == ps["required"]
        assert gs["type"] == ps["type"]
        if "fields" in ps:
            assert gs.get("fields") == ps["fields"]


def test_system_capabilities_parity(go_direct_server: str, py_client: TestClient) -> None:
    path = "/api/v1/system/capabilities"
    py_resp = py_client.get(path)
    assert py_resp.status_code == 200
    py_body = py_resp.json()

    go_status, go_headers, go_body = _get_go(go_direct_server, path)
    assert go_status == 200
    assert go_headers.get("x-nse-routing-target") == "go-direct"

    # Envelope
    assert set(go_body.keys()) == set(py_body.keys()) == {"data", "meta"}

    go_data, py_data = go_body["data"], py_body["data"]
    expected_keys = {
        "api_version",
        "domain_count",
        "domains",
        "endpoint_count",
        "generated_at",
        "pagination",
        "read_only",
        "spec",
    }
    assert set(go_data.keys()) == set(py_data.keys()) == expected_keys

    # Field-level assertions
    assert go_data["api_version"] == py_data["api_version"] == "v1"
    assert go_data["spec"] == py_data["spec"] == "docs/api/API_PLATFORM_V1.md"
    assert go_data["read_only"] is py_data["read_only"] is True
    assert go_data["domain_count"] == py_data["domain_count"] == 19
    assert go_data["domains"] == py_data["domains"]

    # Pagination capability model
    assert go_data["pagination"]["model"] == py_data["pagination"]["model"] == "page"
    assert go_data["pagination"]["max_page_size"] == py_data["pagination"]["max_page_size"] == 200


def test_system_version_parity(go_direct_server: str, py_client: TestClient) -> None:
    path = "/api/v1/system/version"
    py_resp = py_client.get(path)
    assert py_resp.status_code == 200
    py_body = py_resp.json()

    go_status, go_headers, go_body = _get_go(go_direct_server, path)
    assert go_status == 200
    assert go_headers.get("x-nse-routing-target") == "go-direct"

    # Envelope
    assert set(go_body.keys()) == set(py_body.keys()) == {"data", "meta"}

    go_data, py_data = go_body["data"], py_body["data"]
    # Invariant product metadata must match
    assert go_data["product"] == py_data["product"] == "NexusScalpEngine"
    assert go_data["product_display"] == py_data["product_display"] == "Nexus Trading Forex Bot"
    assert go_data["version"] == py_data["version"] == "9.0.14"
    assert isinstance(go_data["channel"], str) and len(go_data["channel"]) > 0
    assert isinstance(go_data["commit"], str)
    assert len(go_data["commit"]) > 0


def test_platform_static_parity(go_direct_server: str) -> None:
    """Platform static docs (Swagger/ReDoc) direct serving availability."""
    for path in ("/docs", "/redoc"):
        req = urllib.request.Request(f"{go_direct_server}{path}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
            content = resp.read()
            assert len(content) > 0


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main(["-v", __file__, *sys.argv[1:]]))
