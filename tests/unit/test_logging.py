"""
Unit Tests - Structured Logging
===============================
Verifies logging configuration and sensitive field redaction.
"""

from nexus_scalp.observability.logging import _redact_sensitive_fields, configure_logging, get_logger


def test_redact_sensitive_fields() -> None:
    """Ensures password and token keys are redacted from log event dicts."""
    event = {"message": "login attempt", "password": "secret123", "api_key": "abc"}
    result = _redact_sensitive_fields(None, None, event)
    assert result["password"] == "[REDACTED_SECRET]"
    assert result["api_key"] == "[REDACTED_SECRET]"
    assert result["message"] == "login attempt"


def test_configure_logging_and_get_logger() -> None:
    """Verifies logging can be configured and logger retrieved without error."""
    configure_logging(log_level="INFO", json_format=False, log_to_file=False)
    log = get_logger("test")
    assert log is not None
