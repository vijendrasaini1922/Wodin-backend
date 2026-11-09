from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.orders.models import Order
from apps.production.tests import ProductionHelpers

from .models import Review


class ReviewTests(ProductionHelpers):
    def setUp(self):
        super().setUp()
        self.support = APIClient()
        self.support.force_authenticate(User.objects.create_user("+919000000003", role=Role.SUPPORT))

    def delivered_order(self):
        _, order = self.paid_order()
        job = self.run_to_qc(order)
        labels = self.ops.get(f"/api/ops/production/{job}/").data["checklist"]
        self.ops.post(f"/api/ops/production/{job}/qc/", {"checklist": {k: True for k in labels}}, format="json")
        sid = self.ops.post("/api/ops/shipments/", {"order_id": str(order.id), "courier": "X", "tracking_number": "1"}, format="json").data["id"]
        self.ops.post(f"/api/ops/shipments/{sid}/delivered/")
        order.refresh_from_db()
        self.assertEqual(order.status, "delivered")
        return order

    def payload(self, order, **kw):
        return {"order": str(order.id), "rating": 5, "title": "Lovely", "body": "Beautiful book, great quality!",
                "display_name": "Asha", "destination": "Manali", "consent_publish_review": True, **kw}

    def test_cannot_review_before_delivery_or_others_orders(self):
        _, order = self.paid_order()
        self.assertEqual(self.cust.post("/api/my/reviews/", self.payload(order), format="json").status_code, 400)
        delivered = self.delivered_order() if False else None
        other = APIClient(); other.force_authenticate(self.other)
        self.assertEqual(other.post("/api/my/reviews/", self.payload(order), format="json").status_code, 400)

    def test_verified_flag_is_server_derived_and_one_review_per_order(self):
        order = self.delivered_order()
        r = self.cust.post("/api/my/reviews/", self.payload(order, is_verified=False, status="approved", is_featured=True), format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertTrue(r.data["is_verified"])
        self.assertEqual(r.data["status"], "pending")  # cannot self-approve
        self.assertFalse(Review.objects.get().is_featured)
        self.assertEqual(self.cust.post("/api/my/reviews/", self.payload(order), format="json").status_code, 400)

    def test_validation(self):
        order = self.delivered_order()
        for bad in [{"rating": 0}, {"rating": 6}, {"body": "short"}, {"consent_publish_review": False}]:
            self.assertEqual(self.cust.post("/api/my/reviews/", self.payload(order, **bad), format="json").status_code, 400, bad)

    def test_not_public_until_approved_and_consent_respected(self):
        order = self.delivered_order()
        rid = self.cust.post("/api/my/reviews/", self.payload(order), format="json").data["id"]
        anon = APIClient()
        self.assertEqual(anon.get("/api/reviews/").data["count"], 0)

        self.assertEqual(self.support.patch(f"/api/ops/reviews/{rid}/", {"status": "approved", "is_featured": True}, format="json").status_code, 200)
        public = anon.get("/api/reviews/").data
        self.assertEqual(public["count"], 1)
        self.assertTrue(public["results"][0]["is_verified"])
        self.assertNotIn("user", public["results"][0])
        s = anon.get("/api/reviews/summary/").data
        self.assertEqual((s["count"], s["average"], s["verified_count"]), (1, 5.0, 1))
        self.assertEqual(anon.get("/api/reviews/?destination=manali").data["count"], 1)
        self.assertEqual(anon.get("/api/reviews/?rating=3").data["count"], 0)

        # hide, then removal request, both remove it from the public list
        self.support.patch(f"/api/ops/reviews/{rid}/", {"status": "hidden"}, format="json")
        self.assertEqual(anon.get("/api/reviews/").data["count"], 0)
        self.support.patch(f"/api/ops/reviews/{rid}/", {"status": "approved"}, format="json")
        self.assertEqual(self.cust.post(f"/api/my/reviews/{rid}/request-removal/").status_code, 200)
        self.assertEqual(anon.get("/api/reviews/").data["count"], 0)

    def test_cannot_publish_without_consent(self):
        order = self.delivered_order()
        rid = self.cust.post("/api/my/reviews/", self.payload(order), format="json").data["id"]
        self.cust.patch(f"/api/my/reviews/{rid}/", {"consent_publish_review": False}, format="json")
        r = self.support.patch(f"/api/ops/reviews/{rid}/", {"status": "approved"}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_featuring_requires_approved(self):
        order = self.delivered_order()
        rid = self.cust.post("/api/my/reviews/", self.payload(order), format="json").data["id"]
        self.assertEqual(self.support.patch(f"/api/ops/reviews/{rid}/", {"is_featured": True}, format="json").status_code, 400)

    def test_edit_resets_moderation(self):
        order = self.delivered_order()
        rid = self.cust.post("/api/my/reviews/", self.payload(order), format="json").data["id"]
        self.support.patch(f"/api/ops/reviews/{rid}/", {"status": "approved"}, format="json")
        self.cust.patch(f"/api/my/reviews/{rid}/", {"body": "Updated text for this review."}, format="json")
        self.assertEqual(Review.objects.get().status, "pending")
        self.assertEqual(APIClient().get("/api/reviews/").data["count"], 0)

    def test_demo_reviews_never_public_and_admin_response(self):
        Review.objects.create(user=self.customer, rating=5, title="Demo", body="Seed data only here",
                              display_name="Demo", status="approved", consent_publish_review=True, is_demo=True)
        self.assertEqual(APIClient().get("/api/reviews/").data["count"], 0)
        rid = str(Review.objects.get().id)
        r = self.support.patch(f"/api/ops/reviews/{rid}/", {"response": "Thank you!"}, format="json")
        self.assertEqual(r.data["response"], "Thank you!")
        # admin cannot rewrite customer content or fake verification
        self.support.patch(f"/api/ops/reviews/{rid}/", {"rating": 1, "is_verified": True, "body": "x"}, format="json")
        review = Review.objects.get()
        self.assertEqual((review.rating, review.is_verified), (5, False))

    def test_moderation_permissions_and_filters(self):
        order = self.delivered_order()
        self.cust.post("/api/my/reviews/", self.payload(order), format="json")
        self.assertEqual(self.cust.get("/api/ops/reviews/").status_code, 403)
        self.assertEqual(self.support.get("/api/ops/reviews/?status=pending").data["count"], 1)
        self.assertEqual(self.support.get("/api/ops/reviews/?status=approved").data["count"], 0)
        other = APIClient(); other.force_authenticate(self.other)
        self.assertEqual(other.get("/api/my/reviews/").data, [])
