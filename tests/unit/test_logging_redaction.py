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
        "reason=DELIVERY_DISABLED correlation_id=- blocked_since_start=1"
    )
    out = _redact_value(line)
    assert "event=BLOCKED_NOT_CONFIGURED" in out
    assert "severity=INFO" in out
    assert "reason=DELIVERY_DISABLED" in out
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


def test_filesystem_paths_in_exceptions_are_not_redacted_bug312():
    """BUG-312: an artifact path in an exception message is diagnostic, not a
    credential. The entropy catcher masked the whole path interior and left
    only the extension, so the operator read a FAKE filename shaped like a
    timestamp ('calibration_20260921T0345Z.json') and hunted for a code path
    that never existed."""
    from nexus_scalp.observability.logging import _redact_value

    keep = [
        "[Errno 2] No such file or directory: "
        "'artifacts/models/scalp/XAUUSD/70d_liquidity/confidence_calibration.json'",
        "path=C:/Users/Capsizer/source/repos/NexusTradingForexBot/artifacts/audit.db",
        "artifact missing: artifacts/models/scalp/XAUUSD/70d_liquidity/model.pt",
    ]
    for s in keep:
        assert _redact_value(s) == s, f"path must survive redaction: {s}"

    # the carve-out is narrow: only real directory structure + a known
    # artifact extension. Opaque high-entropy secrets still redact.
    # NOTE: the api_key fixture below is intentionally NOT a live secret shape
    # (GitHub push protection blocks real-looking keys): it is a short marker
    # the secret-assignment scrub must still catch by its key name.
    for s in [
        "password=hunter2SuperSecretValue4242",
        "bot_token=1234567890:***",
        "api_key=<redacted-test-key-not-a-secret>",
    ]:
        assert "[REDACTED_SECRET]" in _redact_value(s), f"secret must redact: {s}"
