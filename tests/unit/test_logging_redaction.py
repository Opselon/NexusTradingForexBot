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


def test_logging_redaction_structlog_key_value_constants_preserved_bug141b():
    """BUG-141b: structlog renders "key=VALUE" as ONE token, so the all-uppercase
    constant guard never matched (key prefix breaks isupper) and observability
    pairs (event=..., severity=..., reason=...) were entropy-redacted. The
    value-part guard must preserve them while secret semantics stay intact."""
    from nexus_scalp.observability.logging import _redact_value

    line = (
        "[TELEGRAM] event=BLOCKED_NOT_CONFIGURED severity=INFO "
        "reason=BOT_TOKEN_OR_ADMIN_MISSING correlation_id=- blocked_since_start=1"
    )
    out = _redact_value(line)
    assert "event=BLOCKED_NOT_CONFIGURED" in out
    assert "severity=INFO" in out
    assert "reason=BOT_TOKEN_OR_ADMIN_MISSING" in out
    assert "blocked_since_start=1" in out

    # Secret-assignment + blob semantics intact
    assert "hunter2" not in _redact_value("password= hunter2SuperSecretValue42")
    blob = "gAAAAABmZ8k2xQ9tR7uPqW3vXyZ1aB4cD6eF8gH0jK2lM4nO6pQ8rS0tU2vW4xY6zA8bC0dE"
    assert "[REDACTED_SECRET]" in _redact_value(blob)


def test_numeric_key_value_pairs_are_never_entropy_redacted():
    """AGENT-2 (2026-09-01): 'consistency_violations=1' measured 3.63 bits over
    the whole token (the KEY supplies the entropy) and was redacted in the live
    DB_HYGIENE audit summary — numeric observability values are not secrets."""
    from nexus_scalp.observability.logging import _redact_value

    keep = [
        "consistency_violations=1 orphans=3755 duplicates=3",
        "count=243",
        "failures=67",
        "retry_after_sec=60.0",
        "queue_size=0 sent=0 failed=1",
    ]
    for s in keep:
        assert "[REDACTED_SECRET]" not in _redact_value(s), s

    # secrets still redacted (regression guard on the same code path)
    for s in [
        "password=hunter2secret",
        "bot_token=123456:ABC-DEF1234",
        "api_key=sk-123456789abcdefghij0123456789",
    ]:
        assert "[REDACTED_SECRET]" in _redact_value(s), s
