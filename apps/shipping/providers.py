"""Courier provider interface. `manual` lets ops enter AWB/tracking by hand; swap in an aggregator later."""
import re

from django.conf import settings


class ShippingProvider:
    def serviceable(self, pincode: str) -> bool: raise NotImplementedError


class ManualProvider(ShippingProvider):
    def serviceable(self, pincode):
        from .models import NonServiceablePincode

        if not re.fullmatch(r"[1-9]\d{5}", pincode or ""):
            return False
        return not any(pincode.startswith(p) for p in NonServiceablePincode.objects.values_list("prefix", flat=True))


def get_provider() -> ShippingProvider:
    name = getattr(settings, "SHIPPING_PROVIDER", "manual")
    if name == "manual":
        return ManualProvider()
    raise RuntimeError(f"Unknown SHIPPING_PROVIDER {name!r}")
