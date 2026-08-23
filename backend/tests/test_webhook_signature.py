from __future__ import annotations

import hashlib
import hmac

from app.integrations.razorpay import verify_webhook_signature


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_valid_signature() -> None:
    secret = "whsec_test"
    body = b'{"event":"payment.failed"}'
    assert verify_webhook_signature(body, _sign(body, secret), secret) is True


def test_tampered_body_fails() -> None:
    secret = "whsec_test"
    body = b'{"event":"payment.failed"}'
    sig = _sign(body, secret)
    assert verify_webhook_signature(b'{"event":"payment.captured"}', sig, secret) is False


def test_empty_signature_fails() -> None:
    assert verify_webhook_signature(b"{}", "", "secret") is False
