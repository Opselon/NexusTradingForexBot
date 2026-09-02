"""Client E2E pytest wrapper: runs the golden journeys when an engine is live.

Offline-safe: the whole module SKIPS when NSE_E2E_BASE is not reachable, so CI
and unit runs stay green without a running engine. When NSE_E2E_BASE is set and
reachable, executes each journey script in-process and asserts its recorded
checks all passed (evidence JSON in tests/e2e_client/evidence/).
"""
import importlib
import json
import os
import socket
import sys
from pathlib import Path
from urllib.parse import urlparse

import pytest

HERE = Path(__file__).resolve().parent
os.environ.setdefault("NSE_E2E_BASE", "http://127.0.0.1:8081")

BASE = os.environ["NSE_E2E_BASE"]
HOST = urlparse(BASE).hostname or "127.0.0.1"
PORT = urlparse(BASE).port or 80

_JOURNEYS = ["j1_golden", "j2_mode_switch", "j3_signal_decisions",
             "j4_resilience", "j6_responsive_a11y", "j7_localization"]


def _engine_reachable() -> bool:
    try:
        with socket.create_connection((HOST, PORT), timeout=1.5):
            return True
    except OSError:
        return False


requires_engine = pytest.mark.skipif(
    not _engine_reachable(), reason=f"no live engine at {BASE} (offline-safe skip)"
)


@requires_engine
@pytest.mark.parametrize("module_name", _JOURNEYS)
def test_client_journey(module_name: str) -> None:
    """Run one golden journey and require every recorded check to pass."""
    sys.path.insert(0, str(HERE))
    mod = importlib.import_module(module_name)
    mod.run()
    results_path = sorted(HERE.glob("evidence/results_*.json"))[-1]
    data = json.loads(results_path.read_text(encoding="utf-8"))
    failures = []
    for journey, body in data["journeys"].items():
        for chk in body.get("checks", []):
            if not chk.get("ok"):
                failures.append(f"{journey}:{chk['key']}")
    assert not failures, f"journey checks failed: {failures}"
