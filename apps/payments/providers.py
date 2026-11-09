"""Payment provider interface. Razorpay is the first implementation; add others without touching the services."""
import hashlib
import hmac

import logging

import requests
from django.conf import settings
from rest_framework import status
from rest_framework.exceptions import APIException

log = logging.getLogger(__name__)


class PaymentGatewayError(APIException):
    """The payment gateway call failed. The customer sees a generic message; the reason goes to the server log only."""

    status_code = status.HTTP_500_INTERNAL_SERVER_ERROR
    default_detail = "We could not start the payment right now. Please try again shortly."
    default_code = "payment_gateway_error"


def _hmac(secret: str, message: bytes) -> str:
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


class PaymentProvider:
    name = ""

    def public_key(self) -> str: raise NotImplementedError
    def create_order(self, amount_paise: int, currency: str, receipt: str) -> dict: raise NotImplementedError
    def verify_checkout_signature(self, order_id: str, payment_id: str, signature: str) -> bool: raise NotImplementedError
    def verify_webhook_signature(self, body: bytes, signature: str) -> bool: raise NotImplementedError
    def refund(self, payment_id: str, amount_paise: int, reason: str) -> dict: raise NotImplementedError


class RazorpayProvider(PaymentProvider):
    name = "razorpay"
    base = "https://api.razorpay.com/v1"

    def __init__(self):
        self.key_id, self.key_secret = settings.RAZORPAY_KEY_ID, settings.RAZORPAY_KEY_SECRET
        self.webhook_secret = settings.RAZORPAY_WEBHOOK_SECRET
        if not (self.key_id and self.key_secret):
            raise RuntimeError("RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET must be set")

    def public_key(self):
        return self.key_id

    def _post(self, path, payload):
        try:
            r = requests.post(f"{self.base}{path}", json=payload, auth=(self.key_id, self.key_secret), timeout=15)
        except requests.RequestException as exc:
            log.error("Razorpay request failed: %s", exc.__class__.__name__)
            raise PaymentGatewayError() from exc
        if r.status_code == 401:
            log.error("Razorpay rejected the API credentials (check RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET)")
            raise PaymentGatewayError()
        if not r.ok:
            try:
                reason = (r.json().get("error") or {}).get("description", "")
            except ValueError:
                reason = ""
            log.error("Razorpay API error %s on %s: %s", r.status_code, path, reason)
            raise PaymentGatewayError()
        return r.json()

    def create_order(self, amount_paise, currency, receipt):
        data = self._post("/orders", {"amount": amount_paise, "currency": currency, "receipt": receipt})
        return {"id": data["id"]}

    def verify_checkout_signature(self, order_id, payment_id, signature):
        expected = _hmac(self.key_secret, f"{order_id}|{payment_id}".encode())
        return hmac.compare_digest(expected, signature or "")

    def verify_webhook_signature(self, body, signature):
        if not self.webhook_secret:
            return False
        return hmac.compare_digest(_hmac(self.webhook_secret, body), signature or "")

    def refund(self, payment_id, amount_paise, reason):
        data = self._post(f"/payments/{payment_id}/refund", {"amount": amount_paise, "notes": {"reason": reason[:200]}})
        return {"id": data["id"], "status": "processed" if data.get("status") == "processed" else "pending"}


class FakeProvider(PaymentProvider):
    """Local development ('mock', DEBUG only) and tests ('test'). Signatures use fixed dev secrets."""

    SECRET = "mock-checkout-secret"
    WEBHOOK_SECRET = "mock-webhook-secret"

    def __init__(self, name):
        self.name = name
        self.counter = 0

    def public_key(self):
        return "mock_key"

    def create_order(self, amount_paise, currency, receipt):
        import uuid
        return {"id": f"order_mock_{uuid.uuid4().hex[:14]}"}

    def verify_checkout_signature(self, order_id, payment_id, signature):
        return hmac.compare_digest(_hmac(self.SECRET, f"{order_id}|{payment_id}".encode()), signature or "")

    def verify_webhook_signature(self, body, signature):
        return hmac.compare_digest(_hmac(self.WEBHOOK_SECRET, body), signature or "")

    def refund(self, payment_id, amount_paise, reason):
        import uuid
        return {"id": f"rfnd_mock_{uuid.uuid4().hex[:12]}", "status": "processed"}

    @classmethod
    def sign(cls, order_id, payment_id):
        return _hmac(cls.SECRET, f"{order_id}|{payment_id}".encode())

    @classmethod
    def sign_webhook(cls, body: bytes):
        return _hmac(cls.WEBHOOK_SECRET, body)


def get_provider() -> PaymentProvider:
    name = settings.PAYMENT_PROVIDER
    if name == "razorpay":
        return RazorpayProvider()
    if name == "mock":
        if not settings.DEBUG:
            raise RuntimeError("PAYMENT_PROVIDER=mock is not allowed when DEBUG is false")
        return FakeProvider("mock")
    if name == "test":
        return FakeProvider("test")
    raise RuntimeError(f"Unknown PAYMENT_PROVIDER {name!r}")
