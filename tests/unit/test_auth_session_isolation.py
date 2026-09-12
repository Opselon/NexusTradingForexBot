r"""AUTH-SESSION-ISOLATION regression net (P1 test-integrity, 2026-09-12).

Pins the isolation guarantees introduced by tests/conftest.py +
test_alt_ui_runtime_contract.py + test_node_runtime_role.py so the
collection-order flip cannot silently return:

1. SOURCE PIN: the historically offending modules must never mutate
   ``os.environ`` at module (import) scope — pytest imports every collected
   module BEFORE the first test runs, so an import-time setdefault poisons
   the whole session regardless of CLI order (that is the bug class). A
   static AST check is order-independent and cannot be masked by the conftest
   scrub itself.

2. CROSS-TEST SCRUB PIN: a test that leaks the auth env directly through
   os.environ (the historic in-body write with no restore) must not carry it
   into the next test — the conftest snapshot/restore hooks undo it. Order in
   THIS file is load-bearing (leak -> scrub-check); pytest runs same-file
   tests top-to-bottom.

3. DPAPI PIN: SecureSecretStore's default root must NOT resolve to the
   operator's real user-data root during a test session, so an env-less
   create_app() can never persist a generated token into the machine-wide
   %LOCALAPPDATA%\NexusScalpEngine\secrets.enc.

4. CONTRACT PIN: with the auth env scrubbed (the default per-test state),
   create_app must install the WEB-AUTH-P0 middleware — an unauthenticated
   state-mutating request gets 401 — proving isolation restores the REAL auth
   posture instead of hiding the middleware.
"""

from __future__ import annotations

import ast
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from nexus_scalp.web.server import create_app

REPO_ROOT = Path(__file__).resolve().parents[2]

#: Modules that historically leaked WEB-AUTH-P0 env across tests.
AUTH_OFFENDER_MODULES = (
    REPO_ROOT / "tests" / "unit" / "test_alt_ui_runtime_contract.py",
    REPO_ROOT / "tests" / "unit" / "test_node_runtime_role.py",
)


def _import_time_env_mutations(path: Path) -> list[str]:
    """Names of os.environ mutations that execute at module import scope
    (top-level statements only — inside functions/fixtures monkeypatch or
    try/finally restore is fine and not a session leak)."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[str] = []
    for node in tree.body:  # top-level ONLY
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Subscript) and ast.unparse(tgt.value).startswith(
                    "os.environ"
                ):
                    offenders.append(f"{path.name}:{node.lineno} os.environ[...] = ...")
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call = node.value
            fname = ast.unparse(call.func)
            if fname.startswith("os.environ") and (
                "setdefault" in fname or "__setitem__" in fname or "pop" in fname
            ):
                offenders.append(f"{path.name}:{node.lineno} {fname}")
        elif isinstance(node, (ast.If, ast.For, ast.Try)):
            # top-level control flow that reaches an os.environ write is
            # still import-scope: recurse one level into its body.
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assign) and any(
                    isinstance(t, ast.Subscript) and ast.unparse(t.value).startswith("os.environ")
                    for t in sub.targets
                ):
                    offenders.append(f"{path.name}:{sub.lineno} os.environ[...] (import-scope)")
    return offenders


@pytest.mark.parametrize("path", AUTH_OFFENDER_MODULES, ids=lambda p: p.name)
def test_no_import_time_auth_env_mutation(path: Path) -> None:
    offenders = _import_time_env_mutations(path)
    assert not offenders, (
        "AUTH-SESSION-ISOLATION regression: import-scope os.environ mutation "
        f"in {path.name} leaks NSE_WEB_AUTH_* into the whole pytest session "
        f"(collection-order flip): {offenders}"
    )


def test_auth_env_leak_simulator() -> None:
    """Deliberately reproduce the historic leak pattern (direct os.environ
    write, no restore) so the *next* test proves the conftest scrub/restore
    catches it. In-test visibility is expected: the hooks scrub before setup
    and restore after teardown — mid-test writes are the test's own business
    (monkeypatch semantics)."""
    os.environ["NSE_WEB_AUTH_DISABLE"] = "1"
    os.environ["NSE_WEB_AUTH_TOKEN"] = "leak-probe-token"
    assert os.environ.get("NSE_WEB_AUTH_DISABLE") == "1"


def test_auth_env_leak_was_scrubbed_by_conftest() -> None:
    """Runs after test_auth_env_leak_simulator: the conftest snapshot/restore
    hooks must have removed the un-restored direct writes."""
    assert "NSE_WEB_AUTH_DISABLE" not in os.environ, (
        "AUTH-SESSION-ISOLATION: NSE_WEB_AUTH_* survived a test teardown — "
        "cross-test env leakage is back (conftest scrub hooks broken/removed)"
    )
    assert "NSE_WEB_AUTH_TOKEN" not in os.environ


def test_secret_store_root_is_isolated_from_operator_machine(tmp_path: Path) -> None:
    """An env-less token resolution must never write the real DPAPI keystore:
    the session-scoped conftest guard redirects SecureSecretStore's root away
    from the operator's machine-wide %LOCALAPPDATA% user-data dir."""
    from nexus_scalp.release.paths import app_data_root
    from nexus_scalp.settings.secret_store import SecureSecretStore

    store_root = SecureSecretStore().root
    assert store_root != app_data_root(), (
        "SecureSecretStore resolves to the operator's real user-data root "
        f"({app_data_root()}) — an env-less create_app() in tests would "
        "persist a generated web-auth token into machine-wide secrets.enc"
    )
    basetemp = tmp_path.parents[1]  # <basetemp>/test_x0/<name> -> pytest run tmp
    assert str(store_root).startswith(str(basetemp)), (
        f"isolated secret-store root {store_root} escaped the pytest tmp tree {basetemp}"
    )


def test_scrubbed_env_keeps_auth_contract_enforced() -> None:
    """With the auth env scrubbed (default per-test state — including an
    ambient NSE_WEB_AUTH_DISABLE exported into the session, which the scrub
    removes), create_app must install the WEB-AUTH-P0 middleware:
    unauthenticated state mutation -> 401 (no red-inverted fake-green)."""
    assert "NSE_WEB_AUTH_DISABLE" not in os.environ  # conftest scrub in effect
    app = create_app(engine_ref=None)
    client = TestClient(app)
    r = client.post("/api/replay/toggle", json={"active": True, "speed": 1})
    assert r.status_code == 401, (
        "auth contract silently off (fake-green path): middleware not "
        f"installed, got {r.status_code}"
    )
