"""Unit tests for the PR evidence --json output machine contract and
UUID correlation retention in logging redaction.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import typer
from typer.testing import CliRunner

from nexus_scalp.cli.pr_commands import register_pr_commands
from nexus_scalp.observability.logging import _redact_value
from nexus_scalp.pr_evidence.models import Status


def test_pr_evidence_json_mode_produces_single_valid_document() -> None:
    """--json output must be EXACTLY ONE parseable JSON object on stdout.

    Regression test for the 2026-10-21 production incident where telegram_notify
    failed with:
        JSON parse error: Extra data: line 2 column 1 (char 27)
    caused by _emit(_banner_text(), json_mode=True) printing the banner as a
    second top-level JSON document before the payload.
    """
    app = typer.Typer()
    register_pr_commands(app)
    runner = CliRunner()

    mock_result = MagicMock()
    mock_result.pr = 999
    mock_result.commit = "abcdef1234567890"
    mock_result.status = Status.PASS
    mock_result.errors = []
    mock_result.to_dict.return_value = {
        "pr": 999,
        "commit": "abcdef1234567890",
        "status": "PASS",
        "errors": [],
    }

    mock_reporter = MagicMock()
    mock_reporter.report_once.return_value = mock_result

    with (
        patch("nexus_scalp.cli.pr_commands.make_client") as mock_client_factory,
        patch("nexus_scalp.cli.pr_commands.Reporter", return_value=mock_reporter),
    ):
        mock_client = MagicMock()
        mock_client.discover_pr.return_value = 999
        mock_client_factory.return_value = mock_client

        res = runner.invoke(app, ["pr", "report", "--pr", "999", "--json", "--dry-run"])

    assert res.exit_code == 0, f"CLI exited with {res.exit_code}: {res.output}"
    # stdout must contain strictly ONE json document
    stdout = res.stdout.strip()
    assert stdout, "stdout must not be empty in --json mode"

    # json.loads fails with "Extra data" if two documents are concatenated.
    parsed = json.loads(stdout)
    assert isinstance(parsed, dict)
    assert parsed.get("pr") == 999


def test_request_id_correlation_uuid_not_redacted() -> None:
    """UUID-shaped correlation IDs must not be masked as secrets.

    Regression test for the production soak warning:
        [ENTRY_BLOCKED] ... action=BUY_LIMIT symbol=XAUUSD [REDACTED_S...
    which occurred because the entropy-catcher treated the 36-character UUID
    body of request_id as a raw credential.
    """
    raw = (
        "[ENTRY_BLOCKED] layer=RISK_ENGINE reason=RISK_EVALUATION_REJECTED "
        "action=BUY_LIMIT symbol=XAUUSD "
        "request_id=12345678-1234-4234-8234-123456789abc"
    )
    result = _redact_value(raw)
    assert "request_id=12345678-1234-4234-8234-123456789abc" in result
    assert "[REDACTED_SECRET]" not in result


def test_actual_secret_keys_still_redacted() -> None:
    """Legitimate credentials must remain masked, even if UUID-shaped."""
    raw = "api_key=12345678-1234-4234-8234-123456789abc password=supersecret"
    result = _redact_value(raw)
    assert "[REDACTED_SECRET]" in result
    assert "supersecret" not in result
