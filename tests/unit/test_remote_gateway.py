import hashlib
import hmac
import json
import pytest
from datetime import datetime, UTC

from nexus_scalp.adapters.mt5.remote_gateway import RemoteMT5GatewayAdapter

def test_verify_request_signature_valid():
    secret = "test_secret_123"
    adapter = RemoteMT5GatewayAdapter(secret_token=secret)

    timestamp = str(int(datetime.now(UTC).timestamp() * 1000))
    body = json.dumps({"action": "ping"}).encode("utf-8")

    # Generate valid signature
    message = f"{timestamp}.".encode() + body
    valid_signature = hmac.new(
        secret.encode(),
        msg=message,
        digestmod=hashlib.sha256,
    ).hexdigest()

    assert adapter.verify_request_signature(body, timestamp, valid_signature) is True

def test_verify_request_signature_invalid():
    secret = "test_secret_123"
    adapter = RemoteMT5GatewayAdapter(secret_token=secret)

    timestamp = str(int(datetime.now(UTC).timestamp() * 1000))
    body = json.dumps({"action": "ping"}).encode("utf-8")

    assert adapter.verify_request_signature(body, timestamp, "invalid_sig") is False

def test_verify_request_signature_wrong_body():
    secret = "test_secret_123"
    adapter = RemoteMT5GatewayAdapter(secret_token=secret)

    timestamp = str(int(datetime.now(UTC).timestamp() * 1000))
    original_body = json.dumps({"action": "ping"}).encode("utf-8")
    tampered_body = json.dumps({"action": "pong"}).encode("utf-8")

    # Generate signature for original body
    message = f"{timestamp}.".encode() + original_body
    valid_signature = hmac.new(
        secret.encode(),
        msg=message,
        digestmod=hashlib.sha256,
    ).hexdigest()

    # Verify with tampered body
    assert adapter.verify_request_signature(tampered_body, timestamp, valid_signature) is False

def test_verify_request_signature_wrong_timestamp():
    secret = "test_secret_123"
    adapter = RemoteMT5GatewayAdapter(secret_token=secret)

    timestamp1 = "1000000000"
    timestamp2 = "2000000000"
    body = b"test body"

    message = f"{timestamp1}.".encode() + body
    valid_signature = hmac.new(
        secret.encode(),
        msg=message,
        digestmod=hashlib.sha256,
    ).hexdigest()

    assert adapter.verify_request_signature(body, timestamp2, valid_signature) is False

def test_verify_request_signature_empty_body():
    secret = "test_secret_123"
    adapter = RemoteMT5GatewayAdapter(secret_token=secret)

    timestamp = "1000000000"
    body = b""

    message = f"{timestamp}.".encode() + body
    valid_signature = hmac.new(
        secret.encode(),
        msg=message,
        digestmod=hashlib.sha256,
    ).hexdigest()

    assert adapter.verify_request_signature(body, timestamp, valid_signature) is True
