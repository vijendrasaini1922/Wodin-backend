"""Seed the catalogue and a demo staff team for local development.

Prices are ILLUSTRATIVE (taken from the plan's example) - set the real ones in the admin panel.
No fake customer reviews are created: the plan forbids fabricated testimonials.
"""
from decimal import Decimal

from django.core.management.base import BaseCommand
from django.db import transaction

from apps.accounts.models import CustomerProfile, Role, User
from apps.catalog.models import AddOn, Coupon, Paper, PricingSettings, Product, ProductVariant, Theme

STAFF = [("+919000000001", "Demo Admin", Role.ADMIN), ("+919000000002", "Demo Designer", Role.DESIGNER),
         ("+919000000003", "Demo Operations", Role.OPERATIONS), ("+919000000004", "Demo Support", Role.SUPPORT)]

# (name, trim_w_mm, trim_h_mm, base price, extra page, additional copy)
VARIANTS = [("8x8 inch", "203.2", "203.2", "1499", "40", "1100"),
            ("A4 portrait", "210", "297", "1999", "50", "1500"),
            ("A4 landscape", "297", "210", "1999", "50", "1500")]


class Command(BaseCommand):
    help = "Create demo catalogue, coupons, themes and staff accounts (idempotent)."

    @transaction.atomic
    def handle(self, *args, **options):
        product, _ = Product.objects.update_or_create(
            slug="travel-photobook",
            defaults=dict(name="WoDin Premium Photobook", tagline="Your journey. Your memories. Your book.",
                          description="A hardcover travel photobook, designed from your trip photos. Preview and approve every page before it is printed.",
                          sort_order=1))
        for i, (name, w, h, base, extra, copy) in enumerate(VARIANTS, start=1):
            ProductVariant.objects.update_or_create(
                product=product, name=name,
                defaults=dict(cover="Hardcover", base_page_count=20, allowed_page_counts=[20, 24, 32, 40],
                              base_price=Decimal(base), extra_page_price=Decimal(extra), additional_copy_price=Decimal(copy),
                              trim_width_mm=Decimal(w), trim_height_mm=Decimal(h), bleed_mm=Decimal("3"), sort_order=i))
        for name, surcharge in [("Matte", "0"), ("Glossy", "100")]:
            Paper.objects.update_or_create(name=name, defaults=dict(surcharge=Decimal(surcharge)))
        for name in ["Minimal", "Cinematic", "Colourful", "Scrapbook"]:
            Theme.objects.update_or_create(slug=name.lower(), defaults=dict(name=name))
        AddOn.objects.update_or_create(name="Gift wrap", defaults=dict(price=Decimal("99")))
        PricingSettings.objects.update_or_create(pk=1, defaults=dict(
            shipping_fee=Decimal("80"), free_shipping_above=None, tax_rate_percent=Decimal("0"), prices_include_tax=True))
        Coupon.objects.update_or_create(code="WELCOME100", defaults=dict(kind="flat", value=Decimal("100"), is_active=True))

        for phone, name, role in STAFF:
            user, _ = User.objects.get_or_create(phone=phone)
            user.full_name, user.role, user.is_staff = name, role, role == Role.ADMIN
            user.save()
            CustomerProfile.objects.get_or_create(user=user)

        self.stdout.write(self.style.SUCCESS(
            "Seeded catalogue (illustrative prices), coupon WELCOME100 and demo staff.\n"
            "Staff log in with OTP (printed in the server console in dev): "
            + ", ".join(f"{p} ({r})" for p, _, r in STAFF)))
