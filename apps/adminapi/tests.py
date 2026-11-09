from decimal import Decimal

from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.catalog.models import Coupon
from apps.notifications.models import AuditLog
from apps.orders.models import Order
from apps.production.tests import ProductionHelpers


class AdminApiTests(ProductionHelpers):
    def setUp(self):
        super().setUp()
        self.admin_user = User.objects.create_user("+919000000100", role=Role.ADMIN)
        self.admin = APIClient(); self.admin.force_authenticate(self.admin_user)
        self.support = APIClient(); self.support.force_authenticate(User.objects.create_user("+919000000101", role=Role.SUPPORT))

    # ---- role boundaries ----
    def test_role_matrix(self):
        gets = ["/api/ops/dashboard/", "/api/ops/orders/", "/api/ops/customers/", "/api/ops/analytics/"]
        admin_only = ["/api/admin/products/", "/api/admin/coupons/", "/api/admin/staff/", "/api/admin/audit-log/",
                      "/api/admin/pricing-settings/", "/api/admin/variants/", "/api/admin/papers/"]
        for url in gets:
            self.assertEqual(APIClient().get(url).status_code, 403, url)
            self.assertEqual(self.cust.get(url).status_code, 403, url)
            for c in (self.des, self.ops, self.support, self.admin):
                self.assertEqual(c.get(url).status_code, 200, url)
        for url in admin_only:
            for c in (APIClient(), self.cust, self.des, self.ops, self.support):
                self.assertIn(c.get(url).status_code, (401, 403), url)
            self.assertEqual(self.admin.get(url).status_code, 200, url)

    # ---- catalogue is admin-configurable and affects quotes (not hard-coded) ----
    def test_price_change_flows_to_public_quote_but_not_existing_orders(self):
        pid = self.make_project(submit=False)
        order = Order.objects.get(pk=self.cust.post(f"/api/projects/{pid}/submit/").data["order_id"])
        r = self.admin.patch(f"/api/admin/variants/{self.variant.pk}/", {"base_price": "2500.00"}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        q = APIClient().post("/api/catalog/quote/", {"variant_id": self.variant.pk, "page_count": 20}, format="json").data
        self.assertEqual(q["subtotal"], "2500.00")
        order.refresh_from_db()
        self.assertEqual(order.total_amount, Decimal("2079.00"))

    def test_coupon_and_pricing_settings_management(self):
        r = self.admin.post("/api/admin/coupons/", {"code": "welcome", "kind": "percent", "value": "10"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(Coupon.objects.get().code, "WELCOME")
        self.assertEqual(self.admin.post("/api/admin/coupons/", {"code": "BAD", "kind": "percent", "value": "150"}, format="json").status_code, 400)
        self.assertEqual(self.admin.patch("/api/admin/pricing-settings/", {"shipping_fee": "99.00", "tax_rate_percent": "18"}, format="json").status_code, 200)
        q = APIClient().post("/api/catalog/quote/", {"variant_id": self.variant.pk, "page_count": 20}, format="json").data
        self.assertEqual(q["shipping"], "99.00")

    # ---- orders ----
    def test_order_list_filter_search_and_detail(self):
        _, order = self.paid_order()
        lst = self.support.get("/api/ops/orders/?status=paid").data
        self.assertEqual(lst["count"], 1)
        self.assertEqual(self.support.get(f"/api/ops/orders/?q={order.number[-4:]}").data["count"], 1)
        self.assertEqual(self.support.get("/api/ops/orders/?q=nomatch").data["count"], 0)
        d = self.support.get(f"/api/ops/orders/{order.id}/").data
        self.assertEqual(d["approval"]["revision_number"], 1)
        self.assertEqual(d["payments"][0]["status"], "paid")
        self.assertTrue(d["history"])
        self.assertEqual(d["shipping_address"]["city"], "Pune")

    def test_cancel_rules(self):
        pid = self.make_project(submit=False)
        unpaid = Order.objects.get(pk=self.cust.post(f"/api/projects/{pid}/submit/").data["order_id"])
        self.assertEqual(self.support.post(f"/api/ops/orders/{unpaid.id}/cancel/").status_code, 403)  # ops only
        self.assertEqual(self.ops.post(f"/api/ops/orders/{unpaid.id}/cancel/", {"note": "customer asked"}, format="json").status_code, 200)
        _, paid = self.paid_order()
        self.assertEqual(self.ops.post(f"/api/ops/orders/{paid.id}/cancel/").status_code, 400)

    def test_staff_photo_access_is_signed_and_audited(self):
        _, order = self.paid_order()
        type(self.photo).objects.filter(pk=self.photo.pk).update(thumb_key="k/thumb.jpg")
        before = AuditLog.objects.filter(action="photos.staff_access").count()
        data = self.des.get(f"/api/ops/orders/{order.id}/photos/").data
        self.assertEqual(len(data), 1)
        self.assertIn("/api/storage/download/", data[0]["thumb_url"])
        self.assertEqual(AuditLog.objects.filter(action="photos.staff_access").count(), before + 1)
        originals = self.ops.get(f"/api/ops/orders/{order.id}/original-urls/").data
        self.assertIn("/api/storage/download/", originals[0]["url"])
        self.assertTrue(AuditLog.objects.filter(action="photos.staff_original_access", actor=self.ops_user).exists())
        self.assertEqual(self.cust.get(f"/api/ops/orders/{order.id}/photos/").status_code, 403)

    # ---- customers & staff ----
    def test_customer_directory_hides_guests_and_staff(self):
        User.objects.create_user("guest:abc", is_guest=True)
        phones = [c["phone"] for c in self.support.get("/api/ops/customers/").data["results"]]
        self.assertIn("+919876543210", phones)
        self.assertNotIn("guest:abc", phones)
        self.assertNotIn("+919000000100", phones)
        self.assertEqual(self.support.get("/api/ops/customers/?q=98765").data["count"], 1)

    def test_staff_management_and_self_demotion_guard(self):
        r = self.admin.post("/api/admin/staff/", {"phone": "98000 00001", "role": "designer", "full_name": "Dee"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        self.assertEqual(User.objects.get(phone="+919800000001").role, "designer")
        self.assertEqual(self.admin.post("/api/admin/staff/", {"phone": "9800000002", "role": "customer"}, format="json").status_code, 400)
        mine = self.admin.get("/api/admin/staff/").data
        me = next(u for u in mine if u["phone"] == "+919000000100")
        self.assertEqual(self.admin.patch(f"/api/admin/staff/{me['id']}/", {"role": "support"}, format="json").status_code, 400)
        # a freshly created staff user can log in via OTP and gets the staff role (same account)
        self.assertEqual(User.objects.get(phone="+919800000001").is_staff, False)

    # ---- metrics ----
    def test_dashboard_and_analytics_numbers(self):
        _, order = self.paid_order()
        d = self.admin.get("/api/ops/dashboard/").data
        self.assertEqual(d["orders"]["total"], 1)
        self.assertEqual(d["orders"]["by_status"]["paid"], 1)
        self.assertEqual(d["money"]["revenue"], "2079.00")
        self.assertEqual(d["pipeline"]["awaiting_printing"], 1)
        self.assertEqual(d["pipeline"]["approved_unpaid"], 0)
        pay_id = str(order.payments.get().id)
        self.ops.post(f"/api/ops/payments/{pay_id}/refund/", {"amount": "79.00", "reason": "shipping"}, format="json")
        d = self.admin.get("/api/ops/dashboard/").data
        self.assertEqual((d["money"]["refunds"], d["money"]["net"]), ("79.00", "2000.00"))
        a = self.admin.get("/api/ops/analytics/?days=7").data
        self.assertEqual((a["orders_paid"], a["revenue"], a["average_order_value"]), (1, "2079.00", "2079.00"))
        self.assertEqual(a["project_to_paid_conversion_pct"], 100.0)
        self.assertEqual(self.admin.get("/api/ops/analytics/?days=abc").status_code, 400)

    def test_audit_log_viewer_filter(self):
        self.paid_order()
        rows = self.admin.get("/api/admin/audit-log/?action=revision.approved").data
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["actor"], str(self.customer))
