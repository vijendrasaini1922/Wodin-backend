"""Send one real OTP through the configured provider (WhatsApp or MSG91) to verify setup.

    python manage.py send_test_otp +919XXXXXXXXX

Uses the active OTP_PROVIDER, creates no OTPCode row, and never prints secrets.
"""
import secrets

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.accounts.otp import OTPDeliveryError, get_provider
from apps.accounts.phone import normalise_phone


class Command(BaseCommand):
    help = "Send a test OTP using the configured OTP_PROVIDER."

    def add_arguments(self, parser):
        parser.add_argument("phone")

    def handle(self, *args, phone, **options):
        try:
            number = normalise_phone(phone)
        except Exception as exc:  # noqa: BLE001 - show the validation message
            raise CommandError(f"Invalid phone: {exc}") from exc
        code = "".join(secrets.choice("0123456789") for _ in range(settings.OTP_LENGTH))
        self.stdout.write(f"Provider: {settings.OTP_PROVIDER}; sending to {number} ...")
        try:
            get_provider().send(number, code)
        except OTPDeliveryError as exc:
            raise CommandError(f"Delivery failed: {exc} (see the server log for the provider's reason)") from exc
        self.stdout.write(self.style.SUCCESS("Accepted by provider. Check the phone for the message."))
