"""OBS-002 regression tests: correlation ids must survive redaction.

The 2026-08-31 observability census (artifacts/forensics/observability-audit.json
OBS-002) measured 2,631/2,631 EXEC_TRACE lines carrying
execution_id=[REDACTED_SECRET]: the >=24-char high-entropy catcher treats the
engine's own correlation ids as secrets, destroying the log<->DB join key
(audit_signals.execution_id / audit_orders.reason / X-Request-ID).

Fix (2026-09-09):
  * value pass: _CORRELATION_ID_RE pins canonical id shapes verbatim
    (EXEC-/INC-/upd-/fh-/req_ forms — case-sensitive, length-anchored)
  * key pass: correlation-id keys joined _NON_SECRET_KEY_FRAGMENTS

Secret semantics MUST stay intact: assignment-form secrets and non-id-shaped
high-entropy blobs are still redacted (the second half of every test).
"""

from nexus_scalp.observability.logging import (
    _CORRELATION_ID_RE,
    _redact_sensitive_fields,
    _redact_value,
)

EXEC_ID = "EXEC-20260909-213000-a1b2c3"


# ---------------------------------------------------------------------------
# Value pass: ids survive the entropy catcher
# ---------------------------------------------------------------------------


def test_correlation_id_regex_matches_canonical_shapes() -> None:
    canonical = [
        EXEC_ID,
        "INC-2026-7F6DE0C4",
        "upd-20260909T213000123456",
        "fh-99aa88bb77cc66dd11223344",
        "req_ab12cd34ef",
    ]
    for s in canonical:
        assert _CORRELATION_ID_RE.fullmatch(s), s


def test_correlation_id_regex_rejects_non_id_shapes() -> None:
    not_ids = [
        "password=hunter2SuperSecretValue42",
        "gAAAAABmXk2Lz9Qwertyuiop1234567890asdf",
        "randomHighEntropyBlob1234567890abc",
        "INC-2026-7f6de0c4",  # lowercase hex is not the canonical incident form
        "EXEC-20260909-213000-a1b2c3-EXTRA",  # extended tail: not a canonical id
    ]
    for s in not_ids:
        assert not _CORRELATION_ID_RE.fullmatch(s), s


def test_execution_id_survives_in_free_text() -> None:
    line = f"[EXEC_TRACE] decision=WAIT stage=POLICY id={EXEC_ID}"
    out = _redact_value(line)
    assert EXEC_ID in out
    assert "[REDACTED_SECRET]" not in out


def test_execution_id_key_value_pair_survives() -> None:
    out = _redact_value(f"execution_id={EXEC_ID} action=NO_TRADE")
    assert f"execution_id={EXEC_ID}" in out
    assert "NO_TRADE" in out


def test_req_and_incident_ids_survive() -> None:
    assert "req_ab12cd34ef" in _redact_value("req_ab12cd34ef request complete")
    assert "INC-2026-7F6DE0C4" in _redact_value("INC-2026-7F6DE0C4 halt reason=KILL_SWITCH")


# ---------------------------------------------------------------------------
# Key pass: id-shaped keys are no longer wholesale-masked
# ---------------------------------------------------------------------------


def test_id_shaped_keys_survive_key_based_redaction() -> None:
    out = _redact_sensitive_fields(
        None,
        "info",
        {
            "execution_id": EXEC_ID,
            "request_id": "req_ab12cd34ef",
            "correlation_id": "fh-99aa88bb77cc66dd11223344",
            "incident_id": "INC-2026-7F6DE0C4",
        },
    )
    assert out["execution_id"] == EXEC_ID
    assert out["request_id"] == "req_ab12cd34ef"
    assert out["correlation_id"] == "fh-99aa88bb77cc66dd11223344"
    assert out["incident_id"] == "INC-2026-7F6DE0C4"


# ---------------------------------------------------------------------------
# Secret semantics stay intact on the same code path
# ---------------------------------------------------------------------------


def test_secrets_still_redacted_alongside_ids() -> None:
    mixed = f"execution_id={EXEC_ID} password=hunter2SuperSecretValue42"
    out = _redact_value(mixed)
    assert EXEC_ID in out
    assert "hunter2" not in out
    assert "[REDACTED_SECRET]" in out


def test_non_id_high_entropy_blobs_still_redacted() -> None:
    for s in [
        "gAAAAABmXk2Lz9Qwertyuiop1234567890asdf",
        "randomHighEntropyBlob1234567890abc",
    ]:
        assert "[REDACTED_SECRET]" in _redact_value(s), s


def test_sensitive_keys_still_redacted() -> None:
    out = _redact_sensitive_fields(None, "info", {"mt5_password": "x", "execution_id": EXEC_ID})
    assert out["mt5_password"] == "[REDACTED_SECRET]"
    assert out["execution_id"] == EXEC_ID
