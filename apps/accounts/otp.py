"""OTP generation/verification and the pluggable SMS provider interface."""
import hashlib
import hmac
import logging
import secrets
from datetime import timedelta

import requests
from django.conf import settings
from django.utils import timezone

from .models import OTPCode

log = logging.getLogger(__name__)


class OTPProvider:
    def send(self, phone: str, code: str) -> None:  # pragma: no cover - interface
        raise NotImplementedError


class ConsoleOTPProvider(OTPProvider):
    """Development only: logs the code to the server console."""

    def send(self, phone, code):
        log.warning("DEV OTP for %s is %s", phone, code)
        print(f"[DEV OTP] {phone}: {code}", flush=True)


class DummyOTPProvider(OTPProvider):
    """Tests only: sends nothing."""

    def send(self, phone, code):
        pass


class OTPDeliveryError(Exception):
    """The SMS provider could not deliver the code (never includes secrets)."""


class MSG91OTPProvider(OTPProvider):
    """MSG91 OTP API (v5). Needs an approved DLT template containing ##OTP##; see docs/MSG91.md.

    MSG91 can answer HTTP 200 with {"type": "error"}, so the JSON body is checked, not just the status code.
    """

    URL = "https://control.msg91.com/api/v5/otp"

    def send(self, phone, code):
        if not settings.MSG91_AUTH_KEY or not settings.MSG91_TEMPLATE_ID:
            raise OTPDeliveryError("MSG91 is not configured (MSG91_AUTH_KEY / MSG91_TEMPLATE_ID).")
        try:
            resp = requests.post(
                self.URL,
                params={"template_id": settings.MSG91_TEMPLATE_ID, "mobile": phone.lstrip("+"), "otp": code,
                        "otp_expiry": max(1, settings.OTP_TTL_SECONDS // 60)},
                headers={"authkey": settings.MSG91_AUTH_KEY, "accept": "application/json"},
                timeout=10,
            )
            body = resp.json() if resp.content else {}
        except (requests.RequestException, ValueError) as exc:
            log.error("MSG91 request failed: %s", exc.__class__.__name__)
            raise OTPDeliveryError("Could not reach the SMS provider.") from exc
        if resp.status_code >= 400 or str(body.get("type", "")).lower() != "success":
            log.error("MSG91 rejected the OTP (HTTP %s): %s", resp.status_code, body.get("message", "no message"))
            raise OTPDeliveryError("The SMS provider rejected the request.")


def get_provider() -> OTPProvider:
    if settings.OTP_PROVIDER == "msg91":
        return MSG91OTPProvider()
    if settings.OTP_PROVIDER == "dummy":
        return DummyOTPProvider()
    if settings.OTP_PROVIDER == "console":
        if not settings.DEBUG:
            raise RuntimeError("OTP_PROVIDER=console is not allowed when DEBUG is false")
        return ConsoleOTPProvider()
    raise RuntimeError(f"Unknown OTP_PROVIDER {settings.OTP_PROVIDER!r}")


def hash_code(phone: str, code: str) -> str:
    return hmac.new(settings.SECRET_KEY.encode(), f"{phone}:{code}".encode(), hashlib.sha256).hexdigest()


class OTPRateLimited(Exception):
    pass


def issue_otp(phone: str, ip: str | None = None) -> None:
    now = timezone.now()
    recent = OTPCode.objects.filter(phone=phone, created_at__gte=now - timedelta(hours=1))
    last = recent.order_by("-created_at").first()
    if last and (now - last.created_at).total_seconds() < settings.OTP_RESEND_COOLDOWN_SECONDS:
        raise OTPRateLimited("Please wait before requesting another code.")
    if recent.count() >= 5:
        raise OTPRateLimited("Too many codes requested. Try again later.")
    code = "".join(secrets.choice("0123456789") for _ in range(settings.OTP_LENGTH))
    otp = OTPCode.objects.create(
        phone=phone,
        code_hash=hash_code(phone, code),
        expires_at=now + timedelta(seconds=settings.OTP_TTL_SECONDS),
        ip_address=ip,
    )
    try:
        get_provider().send(phone, code)
    except OTPDeliveryError:
        otp.delete()  # an undelivered code must not count towards the resend cooldown / hourly cap
        raise


def verify_otp(phone: str, code: str) -> bool:
    """Single-use check with attempt limit. Returns True and consumes the code on success."""
    now = timezone.now()
    otp = (
        OTPCode.objects.filter(phone=phone, consumed_at__isnull=True, expires_at__gt=now)
        .order_by("-created_at")
        .first()
    )
    if not otp or otp.attempts >= settings.OTP_MAX_ATTEMPTS:
        return False
    otp.attempts += 1
    ok = hmac.compare_digest(otp.code_hash, hash_code(phone, str(code)))
    if ok:
        otp.consumed_at = now
    otp.save(update_fields=["attempts", "consumed_at"])
    return ok
