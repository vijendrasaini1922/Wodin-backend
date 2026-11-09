from rest_framework.test import APIClient

from apps.production.tests import ProductionHelpers

from .models import Invoice


class InvoiceTests(ProductionHelpers):
    def test_not_available_before_payment(self):
        pid = self.make_project(submit=False)
        from apps.orders.models import Order
        order = Order.objects.get(pk=self.cust.post(f"/api/projects/{pid}/submit/").data["order_id"])
        self.assertEqual(self.cust.get(f"/api/orders/{order.id}/invoice/").status_code, 400)

    def test_issue_once_with_frozen_totals_and_downloadable_pdf(self):
        _, order = self.paid_order()
        r = self.cust.get(f"/api/orders/{order.id}/invoice/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.data["totals"]["total"], "2079.00")
        self.assertRegex(r.data["number"], r"^WD/\d{4}/00001$")
        again = self.cust.get(f"/api/orders/{order.id}/invoice/")
        self.assertEqual(again.data["number"], r.data["number"])
        self.assertEqual(Invoice.objects.count(), 1)
        from django.test import Client
        pdf = b"".join(Client().get(r.data["pdf_url"].replace("http://testserver", "")).streaming_content)
        self.assertTrue(pdf.startswith(b"%PDF"))

    def test_catalogue_changes_do_not_alter_issued_invoice(self):
        _, order = self.paid_order()
        before = self.cust.get(f"/api/orders/{order.id}/invoice/").data["totals"]
        self.variant.base_price = 9999
        self.variant.save()
        self.assertEqual(self.cust.get(f"/api/orders/{order.id}/invoice/").data["totals"], before)

    def test_sequential_numbers_and_access_control(self):
        _, first = self.paid_order()
        self.cust.get(f"/api/orders/{first.id}/invoice/")
        other = APIClient(); other.force_authenticate(self.other)
        self.assertEqual(other.get(f"/api/orders/{first.id}/invoice/").status_code, 404)
        self.assertEqual(APIClient().get(f"/api/orders/{first.id}/invoice/").status_code, 403)
        from apps.invoices.services import _next_number
        self.assertTrue(_next_number().endswith("/00002"))
