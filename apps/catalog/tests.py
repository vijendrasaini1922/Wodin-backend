from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APITestCase

from .models import AddOn, Coupon, Paper, PricingSettings, Product, ProductVariant


class CatalogBase(APITestCase):
    def setUp(self):
        self.product = Product.objects.create(name="Premium", slug="premium")
        self.variant = ProductVariant.objects.create(
            product=self.product, name="A4", base_page_count=20, allowed_page_counts=[20, 24, 32],
            base_price=Decimal("1999"), extra_page_price=Decimal("50"), additional_copy_price=Decimal("1500"),
        )
        self.paper = Paper.objects.create(name="Glossy", surcharge=Decimal("100"))
        self.addon = AddOn.objects.create(name="Gift wrap", price=Decimal("99"))
        PricingSettings.objects.update_or_create(pk=1, defaults=dict(
            shipping_fee=Decimal("80"), free_shipping_above=None, tax_rate_percent=Decimal("0"), prices_include_tax=True))

    def quote(self, **kw):
        body = dict(variant_id=self.variant.pk, page_count=20, copies=1)
        body.update(kw)
        return self.client.post("/api/catalog/quote/", body, format="json")


class QuoteTests(CatalogBase):
    def test_base_price_plus_shipping(self):
        r = self.quote()
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["subtotal"], "1999.00")
        self.assertEqual(r.data["total"], "2079.00")

    def test_extra_pages_paper_addon_copies(self):
        r = self.quote(page_count=24, paper_id=self.paper.pk, addon_ids=[self.addon.pk], copies=2)
        # first copy: 1999 + 4*50 + 100 = 2299 ; second: 1500 + 300 = 1800 ; add-on 99
        self.assertEqual(r.data["subtotal"], "4198.00")
        self.assertEqual(r.data["total"], "4278.00")

    def test_percent_coupon_with_cap(self):
        Coupon.objects.create(code="save10", kind="percent", value=Decimal("10"), max_discount=Decimal("100"))
        r = self.quote(coupon_code="SAVE10")
        self.assertEqual(r.data["discount"], "100.00")
        self.assertEqual(r.data["total"], "1979.00")

    def test_flat_coupon_matches_plan_example(self):
        Coupon.objects.create(code="FLAT100", kind="flat", value=Decimal("100"))
        r = self.quote(coupon_code="flat100")
        self.assertEqual(r.data["total"], "1979.00")

    def test_coupon_rules(self):
        Coupon.objects.create(code="OLD", kind="flat", value=10, valid_until=timezone.now() - timedelta(days=1))
        Coupon.objects.create(code="USED", kind="flat", value=10, usage_limit=1, used_count=1)
        Coupon.objects.create(code="BIG", kind="flat", value=10, min_subtotal=Decimal("99999"))
        Coupon.objects.create(code="OFF", kind="flat", value=10, is_active=False)
        for code in ["OLD", "USED", "BIG", "OFF", "NOPE"]:
            self.assertEqual(self.quote(coupon_code=code).status_code, 400, code)

    def test_free_shipping_threshold_and_inclusive_tax(self):
        PricingSettings.objects.filter(pk=1).update(free_shipping_above=Decimal("1500"), tax_rate_percent=Decimal("18"))
        r = self.quote()
        self.assertEqual(r.data["shipping"], "0.00")
        self.assertEqual(r.data["total"], "1999.00")
        self.assertEqual(r.data["tax"], "304.93")  # 1999 - 1999/1.18

    def test_exclusive_tax(self):
        PricingSettings.objects.filter(pk=1).update(tax_rate_percent=Decimal("18"), prices_include_tax=False)
        r = self.quote()
        self.assertEqual(r.data["tax"], "359.82")
        self.assertEqual(r.data["total"], "2438.82")

    def test_validation(self):
        self.assertEqual(self.quote(page_count=22).status_code, 400)
        self.assertEqual(self.quote(copies=0).status_code, 400)
        self.assertEqual(self.quote(variant_id=9999).status_code, 400)
        self.assertEqual(self.quote(addon_ids=[9999]).status_code, 400)

    def test_inactive_variant_unavailable(self):
        ProductVariant.objects.filter(pk=self.variant.pk).update(is_active=False)
        self.assertEqual(self.quote().status_code, 400)


class PublicApiTests(CatalogBase):
    def test_products_list_is_public(self):
        r = self.client.get("/api/catalog/products/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data[0]["variants"][0]["name"], "A4")

    def test_options(self):
        r = self.client.get("/api/catalog/options/")
        self.assertEqual(r.data["papers"][0]["name"], "Glossy")

    def test_coupon_validate_endpoint(self):
        Coupon.objects.create(code="FLAT100", kind="flat", value=100)
        r = self.client.post("/api/catalog/coupon/validate/", dict(variant_id=self.variant.pk, page_count=20, coupon_code="FLAT100"), format="json")
        self.assertEqual(r.data["discount"], "100.00")
        self.assertEqual(Coupon.objects.get(code="FLAT100").used_count, 0)
