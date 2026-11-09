from decimal import Decimal

from rest_framework.exceptions import ValidationError
from rest_framework.test import APIClient

from apps.catalog.models import Coupon
from apps.projects.tests import ProjectBase

from .models import Order
from .services import cancel, order_from_token, set_status, tracking_token


class OrderTests(ProjectBase):
    def submit(self, **extra):
        pid = self.make_project(submit=False)
        r = self.cust.post(f"/api/projects/{pid}/submit/", extra, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        return pid, Order.objects.get(pk=r.data["order_id"])

    def test_submit_creates_order_with_snapshot_and_history(self):
        pid, order = self.submit()
        self.assertEqual(order.user, self.customer)
        self.assertEqual(order.status, "design")
        self.assertEqual(order.total_amount, Decimal("2079.00"))
        self.assertEqual(order.items.get().project_id.hex, pid.replace("-", ""))
        self.assertEqual([h.to_status for h in order.history.all()], ["placed", "design"])
        self.assertTrue(order.number.startswith("WD-"))

    def test_price_snapshot_unaffected_by_catalogue_changes(self):
        _, order = self.submit()
        self.variant.base_price = Decimal("5000")
        self.variant.save()
        order.refresh_from_db()
        self.assertEqual(order.total_amount, Decimal("2079.00"))
        self.assertEqual(order.price_snapshot["subtotal"], "1999.00")

    def test_submit_is_idempotent_one_order_per_project(self):
        pid, order = self.submit()
        # resubmission rejected, and no second order created
        self.assertEqual(self.cust.post(f"/api/projects/{pid}/submit/").status_code, 400)
        self.assertEqual(Order.objects.count(), 1)

    def test_coupon_applied_at_submit(self):
        Coupon.objects.create(code="FLAT100", kind="flat", value=100)
        _, order = self.submit(coupon_code="flat100")
        self.assertEqual(order.total_amount, Decimal("1979.00"))
        self.assertEqual(order.coupon_code, "FLAT100")

    def test_order_status_follows_design_workflow(self):
        pid, order = self.submit()
        self.layout(pid)
        rev = self.publish(pid)
        order.refresh_from_db(); self.assertEqual(order.status, "preview_ready")
        self.cust.post(f"/api/projects/{pid}/request-changes/", {"note": "fix"}, format="json")
        order.refresh_from_db(); self.assertEqual(order.status, "design")
        rev2 = self.publish(pid)
        order.refresh_from_db(); self.assertEqual(order.status, "preview_ready")
        self.cust.post(f"/api/projects/{pid}/approve/", {"revision_id": rev2["id"]}, format="json")
        order.refresh_from_db(); self.assertEqual(order.status, "approved")
        self.assertEqual(order.payment_status, "unpaid")  # approval does not imply payment

    def test_invalid_transition_rejected(self):
        _, order = self.submit()
        with self.assertRaises(ValidationError):
            set_status(order, "printing")
        with self.assertRaises(ValidationError):
            set_status(order, "dispatched")

    def test_cancel_only_before_payment(self):
        _, order = self.submit()
        order = Order.objects.get(pk=order.pk)
        Order.objects.filter(pk=order.pk).update(status="paid")
        order.refresh_from_db()
        with self.assertRaises(ValidationError):
            cancel(order, self.customer)
        Order.objects.filter(pk=order.pk).update(status="design")
        order.refresh_from_db()
        self.assertEqual(cancel(order, self.customer).status, "cancelled")

    def test_my_orders_scoped_and_timeline(self):
        _, order = self.submit()
        r = self.cust.get("/api/orders/")
        self.assertEqual(r.data["count"], 1)
        timeline = r.data["results"][0]["timeline"]
        self.assertEqual([s["key"] for s in timeline][:3], ["placed", "design", "preview_ready"])
        self.assertTrue(timeline[0]["done"] and timeline[1]["current"])
        other = APIClient(); other.force_authenticate(self.other)
        self.assertEqual(other.get("/api/orders/").data["count"], 0)
        self.assertEqual(other.get(f"/api/orders/{order.id}/").status_code, 404)

    def test_delivery_address_snapshot(self):
        _, order = self.submit()
        addr = {"recipient_name": "Asha", "phone": "+919876543210", "line1": "1 MG Road", "city": "Pune",
                "state": "MH", "pincode": "411001"}
        r = self.cust.post(f"/api/orders/{order.id}/address/", {"address": addr}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["shipping_address"]["city"], "Pune")
        self.assertEqual(self.cust.post(f"/api/orders/{order.id}/address/", {}, format="json").status_code, 400)
        saved = self.cust.post("/api/addresses/", addr, format="json").data
        r = self.cust.post(f"/api/orders/{order.id}/address/", {"address_id": saved["id"]}, format="json")
        self.assertEqual(r.status_code, 200)

    def test_public_tracking_token(self):
        _, order = self.submit()
        token = tracking_token(order)
        anon = APIClient()
        r = anon.get(f"/api/orders/track/{token}/")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.data["number"], order.number)
        self.assertNotIn("contact_phone", r.data)
        self.assertNotIn("shipping_address", r.data)
        self.assertEqual(anon.get(f"/api/orders/track/{token[:-2]}xx/").status_code, 404)
        self.assertIsNone(order_from_token("garbage"))
        self.assertEqual(order_from_token(token), order)
        # order detail exposes the token to its owner
        self.assertEqual(self.cust.get(f"/api/orders/{order.id}/").data["tracking_token"], token)


from django.core.cache import cache
from django.test import override_settings
from apps.accounts.models import OTPCode, User
from apps.accounts.otp import hash_code
from apps.trips.models import Trip
from apps.projects.models import BookProject


@override_settings(OTP_PROVIDER="dummy")
class GuestCheckoutTests(ProjectBase):
    def test_guest_checkout_then_link_to_account_via_otp(self):
        cache.clear()
        guest_client = APIClient()
        g = guest_client.post("/api/auth/guest/")
        self.assertEqual(g.status_code, 201)
        self.assertTrue(g.data["is_guest"])
        guest = User.objects.get(pk=g.data["id"])

        trip = Trip.objects.create(user=guest, title="Guest trip", destinations=["Goa"])
        from apps.photos.models import Photo
        Photo.objects.create(user=guest, trip=trip, original_filename="a.jpg", content_type="image/jpeg",
                             original_key="g/o.jpg", status=Photo.Status.READY)
        pid = guest_client.post("/api/projects/", {"trip": str(trip.id), "variant": self.variant.pk, "page_count": 20}, format="json").data["id"]

        # LOOPHOLE GUARD: an unverified guest cannot place an order, with or without a typed phone number
        for body in ({}, {"contact_phone": "98 7654 3299"}):
            r = guest_client.post(f"/api/projects/{pid}/submit/", body, format="json")
            self.assertEqual(r.status_code, 400, r.content)
            self.assertEqual(r.data["code"], "phone_verification_required")
        self.assertEqual(Order.objects.count(), 0)
        self.assertEqual(BookProject.objects.get(pk=pid).status, "draft")

        # verify the phone with an OTP -> the guest's book is linked to the real account
        phone = "+919876543299"
        guest_client.post("/api/auth/otp/request/", {"phone": phone})
        otp = OTPCode.objects.filter(phone=phone).latest("created_at")
        otp.code_hash = hash_code(phone, "123456"); otp.save()
        v = guest_client.post("/api/auth/otp/verify/", {"phone": phone, "code": "123456"})
        self.assertEqual(v.status_code, 200, v.content)
        real = User.objects.get(phone=phone)
        self.assertEqual(Trip.objects.get(pk=trip.pk).user, real)
        self.assertFalse(User.objects.get(pk=guest.pk).is_active)

        # now the same project can be submitted, and the order uses the VERIFIED phone
        r = guest_client.post(f"/api/projects/{pid}/submit/", {"contact_phone": "+910000000000"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        order = Order.objects.get(pk=r.data["order_id"])
        self.assertEqual(order.user, real)
        self.assertEqual(order.contact_phone, phone)
        self.assertEqual(APIClient().get(f"/api/orders/track/{tracking_token(order)}/").status_code, 200)
        self.assertEqual(guest_client.get("/api/orders/").data["count"], 1)

    def test_guest_endpoint_idempotent_for_logged_in_user(self):
        r = self.cust.post("/api/auth/guest/")
        self.assertFalse(r.data["is_guest"])
        self.assertEqual(User.objects.filter(is_guest=True).count(), 0)
