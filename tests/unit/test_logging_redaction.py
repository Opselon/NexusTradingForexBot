import pytest

from nexus_scalp.observability.logging import _redact_sensitive_fields


def test_logging_redaction_hardening():
    # 1. Under-redaction fix: password in event string
    out = _redact_sensitive_fields(
        None, "info", {"event": "MT5 login account 123456 password=hunter2"}
    )
    assert "hunter2" not in out["event"]
    assert "[REDACTED_SECRET]" in out["event"]

    # 2. Token in exc_info
    out_exc = _redact_sensitive_fields(None, "error", {"exc_info": "ValueError(token=12345:abc)"})
    assert "12345:abc" not in out_exc["exc_info"]

    # 3. Over-redaction fix: author, token_bucket preserved
    ed2 = {"author": "Jane Doe", "token_bucket": 5, "authored_by": "Bob"}
    out2 = _redact_sensitive_fields(None, "info", ed2)
    assert out2["author"] == "Jane Doe"
    assert out2["token_bucket"] == 5

    # 4. Key-based still works
    out3 = _redact_sensitive_fields(None, "info", {"mt5_password": "xyz", "api_key": "123"})
    assert out3["mt5_password"] == "[REDACTED_SECRET]"
    assert out3["api_key"] == "[REDACTED_SECRET]"
