from django.core.management import call_command
from rest_framework.test import APIClient, APITestCase

from apps.accounts.models import User
from apps.reviews.models import Review


class SeedTests(APITestCase):
    def test_seed_is_idempotent_and_usable(self):
        call_command("seed_demo", verbosity=0)
        call_command("seed_demo", verbosity=0)
        self.assertEqual(User.objects.filter(role="admin").count(), 1)
        self.assertEqual(Review.objects.count(), 0)  # never fabricate testimonials
        products = APIClient().get("/api/catalog/products/").data
        self.assertEqual(len(products), 1)
        self.assertEqual(len(products[0]["variants"]), 3)
        v = products[0]["variants"][1]
        q = APIClient().post("/api/catalog/quote/", {"variant_id": v["id"], "page_count": 20, "coupon_code": "WELCOME100"}, format="json")
        self.assertEqual(q.data["total"], "1979.00")  # matches the plan's illustrative checkout example
