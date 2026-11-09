from rest_framework.test import APIClient

from apps.orders.services import tracking_token
from apps.production.tests import ProductionHelpers

from .models import NonServiceablePincode, Shipment


class ShippingTests(ProductionHelpers):

    def qc_passed_order(self):
        _, order = self.paid_order()
        job = self.run_to_qc(order)
        self.ops.post(f"/api/ops/production/{job}/qc/", {"checklist": {k: True for k in self.ops.get(f"/api/ops/production/{job}/").data["checklist"]}}, format="json")
        return order

    def dispatch(self, order, **kw):
        body = {"order_id": str(order.id), "courier": "Delhivery", "tracking_number": "AWB123",
                "tracking_url": "https://track.example.com/AWB123", **kw}
        return self.ops.post("/api/ops/shipments/", body, format="json")

    def test_serviceability(self):
        c = APIClient()
        self.assertTrue(c.get("/api/shipping/serviceability/?pincode=411001").data["serviceable"])
        self.assertFalse(c.get("/api/shipping/serviceability/?pincode=12").data["serviceable"])
        self.assertFalse(c.get("/api/shipping/serviceability/?pincode=011001").data["serviceable"])
        NonServiceablePincode.objects.create(prefix="7441")
        self.assertFalse(c.get("/api/shipping/serviceability/?pincode=744101").data["serviceable"])

    def test_cannot_dispatch_before_qc_passed(self):
        _, order = self.paid_order()
        r = self.dispatch(order)
        self.assertEqual(r.status_code, 400)
        self.assertIn("quality check", str(r.data))
        job = self.run_to_qc(order)  # in QC but checklist incomplete
        self.assertEqual(self.dispatch(order).status_code, 400)

    def test_dispatch_track_and_deliver(self):
        order = self.qc_passed_order()
        r = self.dispatch(order)
        self.assertEqual(r.status_code, 201, r.content)
        order.refresh_from_db(); self.assertEqual(order.status, "dispatched")
        self.assertEqual(self.dispatch(order).status_code, 400)  # no double dispatch

        sid = r.data["id"]
        ev = self.ops.post(f"/api/ops/shipments/{sid}/events/", {"status": "Out for delivery", "location": "Pune"}, format="json")
        self.assertEqual(ev.status_code, 201, ev.content)

        mine = self.cust.get(f"/api/orders/{order.id}/shipment/").data
        self.assertEqual(mine["tracking_number"], "AWB123")
        self.assertEqual([e["status"] for e in mine["events"]], ["Dispatched", "Out for delivery"])
        public = APIClient().get(f"/api/orders/track/{tracking_token(order)}/").data
        self.assertEqual(public["shipment"]["courier"], "Delhivery")

        self.assertEqual(self.ops.post(f"/api/ops/shipments/{sid}/delivered/").status_code, 200)
        self.ops.post(f"/api/ops/shipments/{sid}/delivered/")  # idempotent
        order.refresh_from_db(); self.assertEqual(order.status, "delivered")
        self.assertEqual(Shipment.objects.get().events.filter(status="Delivered").count(), 1)

    def test_unserviceable_pincode_blocks_dispatch(self):
        order = self.qc_passed_order()
        NonServiceablePincode.objects.create(prefix="4110")
        r = self.dispatch(order)
        self.assertEqual(r.status_code, 400)
        self.assertIn("not serviceable", str(r.data))

    def test_permissions(self):
        order = self.qc_passed_order()
        self.assertEqual(self.cust.post("/api/ops/shipments/", {"order_id": str(order.id)}, format="json").status_code, 403)
        other = APIClient(); other.force_authenticate(self.other)
        self.assertEqual(other.get(f"/api/orders/{order.id}/shipment/").status_code, 404)
