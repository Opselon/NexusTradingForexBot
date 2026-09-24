"""tests/unit/test_update_helpers_secrets_scan.py

Positive regression test for the release secrets scanner
(scripts/build/update_helpers.py action_scan_tree).

Scope (CONTRACT: precision, never weakening). The scanner must STILL catch a
realistic high-entropy credential, while no longer flagging a screaming-case
snake-case VALUE — which is a constant / env-var NAME, e.g. the inherited
``SECRET_ENV_API_KEY = "NSE_GATEWAY_API_KEY"`` in
``src/nexus_scalp/gateway/server.py`` that turned the release secrets gate
red on pristine base (it had never been run end-to-end before this wave).

Bot-token and private-key patterns are not exercised here beyond their
unchanged nature; the pin below asserts the api[_-]?key path specifically
because that is the one this lane made more precise.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

MODULE_PATH = Path(__file__).resolve().parents[2] / "scripts" / "build" / "update_helpers.py"


@pytest.fixture(scope="session")
def helpers_module() -> Any:
    assert MODULE_PATH.is_file(), f"missing helper at {MODULE_PATH}"
    spec = importlib.util.spec_from_file_location("update_helpers", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_scanner_still_catches_a_realistic_high_entropy_key(
    tmp_path: Path, helpers_module: Any
) -> None:
    """Positive pin (mandatory for a precision change): the scanner must
    fail loud on a realistic api key. This MUST stay red for any edit that
    actually weakens detection."""
    root = tmp_path / "portable"
    root.mkdir(parents=True, exist_ok=True)
    (root / "gateway.py").write_text(
        'api_key = "sk_live_9f8e7d6c5b4a3210abcdef"\n', encoding="utf-8"
    )
    rc = helpers_module.action_scan_tree([str(root)])
    assert rc != 0, "a realistic high-entropy api key MUST fail the secrets scan"
    assert helpers_module._looks_like_a_constant_name("sk_live_9f8e7d6c5b4a3210abcdef") is False


def test_scanner_ignores_a_screaming_case_env_var_name(tmp_path: Path, helpers_module: Any) -> None:
    """The inherited false positive: a VALUE that is a screaming-case
    snake identifier is a constant/env-var NAME, not a secret."""
    root = tmp_path / "portable"
    root.mkdir(parents=True, exist_ok=True)
    (root / "server.py").write_text(
        'SECRET_ENV_API_KEY = "NSE_GATEWAY_API_KEY"\n', encoding="utf-8"
    )
    assert helpers_module._looks_like_a_constant_name("NSE_GATEWAY_API_KEY") is True
    rc = helpers_module.action_scan_tree([str(root)])
    assert rc == 0, "screaming-case snake-case values are names, not secrets"


def test_scanner_catches_a_screaming_case_value_without_underscore(
    tmp_path: Path, helpers_module: Any
) -> None:
    """The precision fix is NARROW: an all-caps value with NO underscore is
    not a snake identifier and must still fail."""
    root = tmp_path / "portable"
    root.mkdir(parents=True, exist_ok=True)
    (root / "gateway.py").write_text('api_key = "SUPERSECRETPASSWORD123"\n', encoding="utf-8")
    assert helpers_module._looks_like_a_constant_name("SUPERSECRETPASSWORD123") is False
    rc = helpers_module.action_scan_tree([str(root)])
    assert rc != 0, "all-caps values without an underscore are still secrets"


def test_scanner_ignores_base_gateway_server_literal(tmp_path: Path, helpers_module: Any) -> None:
    """The exact inherited base line that red-flagged the release secrets
    gate must now scan clean (byte-for-byte the base file's lines)."""
    root = tmp_path / "portable"
    root.mkdir(parents=True, exist_ok=True)
    body = 'SECRET_ENV_API_KEY = "NSE_GATEWAY_API_KEY"\nDEFAULT_API_KEY = "default_local_key"\n'
    (root / "server.py").write_text(body, encoding="utf-8")
    rc = helpers_module.action_scan_tree([str(root)])
    assert rc == 0, "inherited base gateway/server.py placeholder constants must scan clean"


def test_scanner_still_catches_a_real_bot_token(tmp_path: Path, helpers_module: Any) -> None:
    """The bot-token pattern is untouched and must still fire."""
    root = tmp_path / "portable"
    root.mkdir(parents=True, exist_ok=True)
    (root / "live.yaml").write_text(
        'bot_token: "123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw"\n',
        encoding="utf-8",
    )
    rc = helpers_module.action_scan_tree([str(root)])
    assert rc != 0, "a real telegram bot token MUST fail the secrets scan"


def test_scanner_reports_a_missing_root(helpers_module: Any) -> None:
    """Sanity: the gate still returns non-zero when the tree is absent."""
    rc = helpers_module.action_scan_tree(["no-such-tree-xyz"])
    assert rc != 0


def test_scanner_catches_a_colon_shaped_secret(tmp_path: Path, helpers_module: Any) -> None:
    """A ``key: <value>`` colon-shaped line has no constant declaration to
    match, so it can never be a self-named constant and must still fire."""
    root = tmp_path / "portable"
    root.mkdir(parents=True, exist_ok=True)
    (root / "gateway.py").write_text('apikey: "AKIAIOSFODNN7EXAMPLE"\n', encoding="utf-8")
    rc = helpers_module.action_scan_tree([str(root)])
    assert rc != 0, "a colon-shaped api key MUST fail the secrets scan"


def test_scanner_catches_an_all_caps_value_with_a_different_trailing_name(
    tmp_path: Path, helpers_module: Any
) -> None:
    """Precision is anchored on the CONSTANT NAME, not the value's shape:
    an all-caps value whose trailing identifier differs from the constant
    (here MY_OWN_SECRET under API_KEY) is a possible secret and MUST fire."""
    root = tmp_path / "portable"
    root.mkdir(parents=True, exist_ok=True)
    (root / "gateway.py").write_text('API_KEY = "MY_OWN_SECRET"\n', encoding="utf-8")
    rc = helpers_module.action_scan_tree([str(root)])
    assert rc != 0, "a value whose trailing name differs from its constant must still fail"


def test_scanner_catches_a_secret_in_a_file_that_also_has_a_self_named_constant(
    tmp_path: Path, helpers_module: Any
) -> None:
    """The pattern loop walks every match of every pattern: a file may hold a
    legitimate constant AND a real secret — only the constant is skipped."""
    root = tmp_path / "portable"
    root.mkdir(parents=True, exist_ok=True)
    (root / "server.py").write_text(
        'SECRET_ENV_API_KEY = "NSE_GATEWAY_API_KEY"\napi_key = "AKIAIOSFODNN7EXAMPLE"\n',
        encoding="utf-8",
    )
    rc = helpers_module.action_scan_tree([str(root)])
    assert rc != 0, "a real secret next to a self-named constant MUST still be reported"
