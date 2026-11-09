import json
from decimal import Decimal

from django.test import Client, override_settings
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.orders.models import Order
from apps.orders.tests import ProjectBase

from .models import Payment, Refund, WebhookEvent
from .providers import FakeProvider


@override_settings(PAYMENT_PROVIDER="test")
class PaymentHelpers(ProjectBase):
    """Shared helpers (no tests of its own) reused by the production tests."""

    ADDRESS = {"recipient_name": "Asha", "phone": "+919876543210", "line1": "1 MG Road", "city": "Pune",
               "state": "MH", "pincode": "411001"}

    def approved_order(self, address=True):
        pid = self.make_project(submit=False)
        order = Order.objects.get(pk=self.cust.post(f"/api/projects/{pid}/submit/").data["order_id"])
        self.layout(pid)
        rev = self.publish(pid)
        self.cust.post(f"/api/projects/{pid}/approve/", {"revision_id": rev["id"]}, format="json")
        if address:
            self.cust.post(f"/api/orders/{order.id}/address/", {"address": self.ADDRESS}, format="json")
        order.refresh_from_db()
        return pid, order

    def start(self, order):
        r = self.cust.post(f"/api/orders/{order.id}/payment/")
        self.assertEqual(r.status_code, 200, r.content)
        return r.data

    def confirm(self, order, start, payment_id="pay_1", signature=None):
        sig = signature if signature is not None else FakeProvider.sign(start["gateway_order_id"], payment_id)
        return self.cust.post(f"/api/orders/{order.id}/payment/confirm/", {
            "gateway_order_id": start["gateway_order_id"], "gateway_payment_id": payment_id, "signature": sig}, format="json")

    def webhook(self, event, order_id, payment_id="pay_1", amount=207900, event_id="evt_1", signature=None):
        body = json.dumps({"event": event, "payload": {"payment": {"entity": {
            "id": payment_id, "order_id": order_id, "amount": amount, "error_description": "card declined"}}}}).encode()
        sig = signature if signature is not None else FakeProvider.sign_webhook(body)
        return Client().post("/api/payments/webhook/razorpay/", data=body, content_type="application/json",
                             HTTP_X_RAZORPAY_SIGNATURE=sig, HTTP_X_RAZORPAY_EVENT_ID=event_id)



class PaymentTests(PaymentHelpers):
    # ---- gate rules ----
    def test_cannot_pay_before_approval_or_without_address(self):
        pid = self.make_project(submit=False)
        order = Order.objects.get(pk=self.cust.post(f"/api/projects/{pid}/submit/").data["order_id"])
        self.assertEqual(self.cust.post(f"/api/orders/{order.id}/payment/").status_code, 400)  # not approved
        self.layout(pid)
        rev = self.publish(pid)
        self.cust.post(f"/api/projects/{pid}/approve/", {"revision_id": rev["id"]}, format="json")
        self.assertEqual(self.cust.post(f"/api/orders/{order.id}/payment/").status_code, 400)  # no address yet

    def test_amount_comes_from_server_snapshot(self):
        _, order = self.approved_order()
        data = self.start(order)
        self.assertEqual(data["amount_paise"], 207900)
        self.assertEqual(Payment.objects.get().amount, Decimal("2079.00"))

    def test_start_is_idempotent_no_duplicate_gateway_orders(self):
        _, order = self.approved_order()
        a, b = self.start(order), self.start(order)
        self.assertEqual(a["gateway_order_id"], b["gateway_order_id"])
        self.assertEqual(Payment.objects.count(), 1)

    # ---- confirmation ----
    def test_confirm_with_valid_signature_marks_paid(self):
        _, order = self.approved_order()
        r = self.confirm(order, self.start(order))
        self.assertEqual(r.status_code, 200, r.content)
        order.refresh_from_db()
        self.assertEqual((order.payment_status, order.status), ("paid", "paid"))
        self.assertEqual(Payment.objects.get().gateway_payment_id, "pay_1")

    def test_bad_signature_is_rejected_and_order_stays_unpaid(self):
        _, order = self.approved_order()
        r = self.confirm(order, self.start(order), signature="deadbeef")
        self.assertEqual(r.status_code, 400)
        order.refresh_from_db()
        self.assertEqual((order.payment_status, order.status), ("unpaid", "approved"))

    def test_confirm_twice_is_idempotent(self):
        _, order = self.approved_order()
        start = self.start(order)
        self.assertEqual(self.confirm(order, start).status_code, 200)
        self.assertEqual(self.confirm(order, start).status_code, 200)  # replayed confirmation is harmless
        self.assertEqual(Payment.objects.filter(status="paid").count(), 1)
        order.refresh_from_db()
        self.assertEqual(order.status, "paid")

    def test_cannot_confirm_another_customers_order(self):
        _, order = self.approved_order()
        start = self.start(order)
        other = APIClient(); other.force_authenticate(self.other)
        r = other.post(f"/api/orders/{order.id}/payment/confirm/", {
            "gateway_order_id": start["gateway_order_id"], "gateway_payment_id": "p",
            "signature": FakeProvider.sign(start["gateway_order_id"], "p")}, format="json")
        self.assertEqual(r.status_code, 404)

    # ---- dev-only mock-pay helper ----
    def test_mock_pay_is_not_available_outside_the_mock_provider(self):
        _, order = self.approved_order()
        start = self.start(order)
        r = self.cust.post(f"/api/orders/{order.id}/payment/mock-pay/",
                           {"gateway_order_id": start["gateway_order_id"]}, format="json")
        self.assertEqual(r.status_code, 404)  # provider is "test" here, so no signature can be minted

    @override_settings(PAYMENT_PROVIDER="mock", DEBUG=True)
    def test_mock_pay_signature_confirms_payment_in_dev(self):
        _, order = self.approved_order()
        start = self.start(order)
        r = self.cust.post(f"/api/orders/{order.id}/payment/mock-pay/",
                           {"gateway_order_id": start["gateway_order_id"]}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        done = self.cust.post(f"/api/orders/{order.id}/payment/confirm/", r.data, format="json")
        self.assertEqual(done.status_code, 200, done.content)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "paid")

    @override_settings(PAYMENT_PROVIDER="mock", DEBUG=True)
    def test_mock_pay_rejects_unknown_gateway_order_and_other_customers(self):
        _, order = self.approved_order()
        start = self.start(order)
        bad = self.cust.post(f"/api/orders/{order.id}/payment/mock-pay/", {"gateway_order_id": "order_x"}, format="json")
        self.assertEqual(bad.status_code, 400)
        other = APIClient(); other.force_authenticate(self.other)
        r = other.post(f"/api/orders/{order.id}/payment/mock-pay/",
                       {"gateway_order_id": start["gateway_order_id"]}, format="json")
        self.assertEqual(r.status_code, 404)

    # ---- webhooks ----
    def test_webhook_captures_payment_even_if_browser_never_returns(self):
        _, order = self.approved_order()
        start = self.start(order)
        r = self.webhook("payment.captured", start["gateway_order_id"])
        self.assertEqual((r.status_code, r.json()["result"]), (200, "paid"))
        order.refresh_from_db()
        self.assertEqual((order.payment_status, order.status), ("paid", "paid"))

    def test_webhook_signature_required(self):
        _, order = self.approved_order()
        start = self.start(order)
        self.assertEqual(self.webhook("payment.captured", start["gateway_order_id"], signature="nope").status_code, 400)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "unpaid")

    def test_webhook_is_idempotent_on_event_id(self):
        _, order = self.approved_order()
        start = self.start(order)
        self.webhook("payment.captured", start["gateway_order_id"])
        again = self.webhook("payment.captured", start["gateway_order_id"])
        self.assertEqual(again.json()["result"], "duplicate")
        self.assertEqual(WebhookEvent.objects.count(), 1)

    def test_webhook_amount_mismatch_does_not_mark_paid(self):
        _, order = self.approved_order()
        start = self.start(order)
        r = self.webhook("payment.captured", start["gateway_order_id"], amount=100)
        self.assertEqual(r.status_code, 400)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "unpaid")
        self.assertEqual(WebhookEvent.objects.count(), 0)  # rolled back, so a corrected retry is processed
        self.assertEqual(self.webhook("payment.captured", start["gateway_order_id"]).json()["result"], "paid")

    def test_failed_then_retry_succeeds_and_failed_never_overrides_paid(self):
        _, order = self.approved_order()
        start = self.start(order)
        self.webhook("payment.failed", start["gateway_order_id"], event_id="e1")
        self.assertEqual(Payment.objects.get().status, "failed")
        retry = self.start(order)  # new gateway order is created after a failure
        self.assertNotEqual(retry["gateway_order_id"], start["gateway_order_id"])
        self.webhook("payment.captured", retry["gateway_order_id"], payment_id="pay_2", event_id="e2")
        self.webhook("payment.failed", retry["gateway_order_id"], payment_id="pay_2", event_id="e3")
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "paid")
        self.assertEqual(Payment.objects.filter(status="paid").count(), 1)

    def test_double_charge_is_flagged_for_refund(self):
        _, order = self.approved_order()
        first = self.start(order)
        self.webhook("payment.captured", first["gateway_order_id"], event_id="e1")
        # a second, older attempt also succeeds at the gateway
        second = Payment.objects.create(order=order, provider="test", gateway_order_id="order_late", amount=order.total_amount)
        self.webhook("payment.captured", "order_late", payment_id="pay_dup", event_id="e2")
        second.refresh_from_db()
        self.assertTrue(second.is_duplicate)
        order.refresh_from_db()
        self.assertEqual(order.status, "paid")

    def test_pay_rejected_when_already_paid(self):
        _, order = self.approved_order()
        self.confirm(order, self.start(order))
        self.assertEqual(self.cust.post(f"/api/orders/{order.id}/payment/").status_code, 400)

    # ---- refunds ----
    def test_refund_permissions_limits_and_customer_visibility(self):
        _, order = self.approved_order()
        self.confirm(order, self.start(order))
        payment = Payment.objects.get()
        url = f"/api/ops/payments/{payment.id}/refund/"
        self.assertEqual(self.cust.post(url, {"amount": "10", "reason": "x"}, format="json").status_code, 403)

        ops = User.objects.create_user("+919000000002", role=Role.OPERATIONS)
        c = APIClient(); c.force_authenticate(ops)
        self.assertEqual(c.post(url, {"amount": "5000", "reason": "too much"}, format="json").status_code, 400)
        r = c.post(url, {"amount": "500", "reason": "damaged"}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "partially_refunded")
        r = c.post(url, {"amount": "1579", "reason": "rest"}, format="json")
        self.assertEqual(r.status_code, 201)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "refunded")
        self.assertEqual(c.post(url, {"amount": "1", "reason": "again"}, format="json").status_code, 400)

        history = self.cust.get(f"/api/orders/{order.id}/payments/").data
        self.assertEqual(len(history[0]["refunds"]), 2)
        self.assertEqual(Refund.objects.count(), 2)

    def test_mock_provider_blocked_without_debug(self):
        from apps.payments.providers import get_provider
        with override_settings(PAYMENT_PROVIDER="mock", DEBUG=False):
            with self.assertRaises(RuntimeError):
                get_provider()


@override_settings(PAYMENT_PROVIDER="razorpay", RAZORPAY_KEY_ID="rzp_test_key", RAZORPAY_KEY_SECRET="test_secret")
class RazorpayProviderTests(PaymentHelpers):
    """The real Razorpay provider with the HTTP layer mocked: nothing here ever reaches Razorpay."""

    def _resp(self, status=200, body=None):
        from unittest.mock import Mock
        r = Mock(status_code=status, ok=status < 400)
        r.json.return_value = body if body is not None else {"id": "order_RZP123"}
        return r

    def test_creates_gateway_order_with_server_amount_in_paise_and_basic_auth(self):
        from unittest.mock import patch
        _, order = self.approved_order()
        with patch("apps.payments.providers.requests.post", return_value=self._resp()) as post:
            r = self.cust.post(f"/api/orders/{order.id}/payment/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["gateway_order_id"], "order_RZP123")
        self.assertEqual(r.data["key_id"], "rzp_test_key")
        self.assertNotIn("test_secret", json.dumps(r.data))  # the secret never reaches the browser
        kwargs = post.call_args.kwargs
        self.assertEqual(post.call_args.args[0], "https://api.razorpay.com/v1/orders")
        self.assertEqual(kwargs["json"], {"amount": int(order.total_amount * 100), "currency": "INR", "receipt": order.number})
        self.assertEqual(kwargs["auth"], ("rzp_test_key", "test_secret"))

    def test_gateway_failure_returns_500_and_does_not_leak_details(self):
        from unittest.mock import patch
        _, order = self.approved_order()
        bad = self._resp(400, {"error": {"description": "Order amount less than minimum amount allowed"}})
        with patch("apps.payments.providers.requests.post", return_value=bad):
            r = self.cust.post(f"/api/orders/{order.id}/payment/")
        self.assertEqual(r.status_code, 500)
        self.assertNotIn("minimum", json.dumps(r.data))
        self.assertEqual(order.payments.count(), 0)

    def test_bad_gateway_credentials_return_500_not_a_logout(self):
        from unittest.mock import patch
        _, order = self.approved_order()
        with patch("apps.payments.providers.requests.post", return_value=self._resp(401, {"error": {}})):
            r = self.cust.post(f"/api/orders/{order.id}/payment/")
        self.assertEqual(r.status_code, 500)

    def test_network_error_returns_500(self):
        import requests
        from unittest.mock import patch
        _, order = self.approved_order()
        with patch("apps.payments.providers.requests.post", side_effect=requests.ConnectionError("down")):
            self.assertEqual(self.cust.post(f"/api/orders/{order.id}/payment/").status_code, 500)

    def test_amount_below_one_rupee_is_refused_before_calling_razorpay(self):
        from unittest.mock import patch
        _, order = self.approved_order()
        Order.objects.filter(pk=order.pk).update(total_amount=Decimal("0.50"))
        with patch("apps.payments.providers.requests.post") as post:
            r = self.cust.post(f"/api/orders/{order.id}/payment/")
        self.assertEqual(r.status_code, 400)
        post.assert_not_called()

    def test_verify_uses_hmac_sha256_of_order_pipe_payment_with_key_secret(self):
        import hashlib, hmac
        from unittest.mock import patch
        _, order = self.approved_order()
        with patch("apps.payments.providers.requests.post", return_value=self._resp()):
            self.cust.post(f"/api/orders/{order.id}/payment/")
        good = hmac.new(b"test_secret", b"order_RZP123|pay_ABC", hashlib.sha256).hexdigest()

        bad = self.cust.post(f"/api/orders/{order.id}/payment/confirm/", {
            "gateway_order_id": "order_RZP123", "gateway_payment_id": "pay_ABC", "signature": "0" * 64}, format="json")
        self.assertEqual(bad.status_code, 400)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "unpaid")  # a bad signature never marks the order paid

        missing = self.cust.post(f"/api/orders/{order.id}/payment/confirm/", {"gateway_order_id": "order_RZP123"}, format="json")
        self.assertEqual(missing.status_code, 400)

        ok = self.cust.post(f"/api/orders/{order.id}/payment/confirm/", {
            "gateway_order_id": "order_RZP123", "gateway_payment_id": "pay_ABC", "signature": good}, format="json")
        self.assertEqual(ok.status_code, 200, ok.content)
        order.refresh_from_db()
        self.assertEqual(order.payment_status, "paid")
