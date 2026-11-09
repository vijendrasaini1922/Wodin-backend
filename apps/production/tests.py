import io
import shutil
import tempfile
from decimal import Decimal

from django.test import override_settings
from PIL import Image
from rest_framework.test import APIClient

from apps.accounts.models import Role, User
from apps.orders.models import Order
from apps.payments.tests import PaymentHelpers
from apps.photos.models import Photo
from apps.photos.storage import get_storage
from apps.photos.tests import noisy_jpeg

from .models import ProductionJob

TMP = tempfile.mkdtemp(prefix="wodin-prod-media-")


@override_settings(STORAGE_BACKEND="local", PRIVATE_MEDIA_ROOT=TMP, BACKEND_URL="http://testserver")
class ProductionHelpers(PaymentHelpers):
    """Fixtures/helpers (no tests of its own), reused by the shipping tests."""

    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP, ignore_errors=True)

    def setUp(self):
        super().setUp()
        # tiny trim so test PDFs are small
        self.variant.trim_width_mm = self.variant.trim_height_mm = Decimal("50")
        self.variant.save()
        get_storage().save(self.photo.original_key, noisy_jpeg((1600, 1200)), "image/jpeg")
        self.ops_user = User.objects.create_user("+919000000009", role=Role.OPERATIONS)
        self.ops = APIClient(); self.ops.force_authenticate(self.ops_user)

    def full_layout(self, pid, pages=21, photo=None):
        photo = photo or self.photo
        body = [{"number": n, "layout": "single", "elements": [
            {"kind": "photo", "photo": str(photo.id), "x": 0, "y": 0, "w": 1, "h": 1, "z": 0},
            {"kind": "text", "text": f"p{n}", "x": 0.2, "y": 0.2, "w": 0.3, "h": 0.1, "z": 1}]} for n in range(pages)]
        r = self.des.put(f"/api/design/projects/{pid}/pages/", body, format="json")
        self.assertEqual(r.status_code, 200, r.content)

    def paid_order(self, pages=21):
        pid = self.make_project(submit=False)
        order = Order.objects.get(pk=self.cust.post(f"/api/projects/{pid}/submit/").data["order_id"])
        self.full_layout(pid, pages)
        rev = self.publish(pid)
        self.cust.post(f"/api/projects/{pid}/approve/", {"revision_id": rev["id"]}, format="json")
        self.cust.post(f"/api/orders/{order.id}/address/", {"address": self.ADDRESS}, format="json")
        self.confirm(order, self.start(order))
        order.refresh_from_db()
        self.assertEqual(order.status, "paid")
        return pid, order

    def create_job(self, order):
        r = self.ops.post("/api/ops/production/", {"order_id": str(order.id)}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        return r.data["id"]

    def run_to_qc(self, order):
        job = self.create_job(order)
        self.assertTrue(self.ops.post(f"/api/ops/production/{job}/generate-pdf/").data["pdf_valid"])
        self.assertEqual(self.ops.post(f"/api/ops/production/{job}/start-printing/").status_code, 200)
        order.refresh_from_db(); self.assertEqual(order.status, "printing")
        self.assertEqual(self.ops.post(f"/api/ops/production/{job}/quality-check/").status_code, 200)
        order.refresh_from_db(); self.assertEqual(order.status, "quality_check")
        return job


class ProductionTests(ProductionHelpers):
    # ---- gate ----
    def test_gate_blocks_unpaid_order(self):
        pid = self.make_project(submit=False)
        order = Order.objects.get(pk=self.cust.post(f"/api/projects/{pid}/submit/").data["order_id"])
        self.full_layout(pid)
        rev = self.publish(pid)
        self.cust.post(f"/api/projects/{pid}/approve/", {"revision_id": rev["id"]}, format="json")  # approved, not paid
        r = self.ops.post("/api/ops/production/", {"order_id": str(order.id)}, format="json")
        self.assertEqual(r.status_code, 400)
        self.assertIn("Payment", str(r.data))

    def test_gate_blocks_paid_but_unapproved_or_superseded_design(self):
        pid, order = self.paid_order()
        job = self.create_job(order)
        # designer changes the design after payment -> prior approval invalidated
        self.full_layout(pid)
        self.publish(pid, "post-payment tweak")
        r = self.ops.post(f"/api/ops/production/{job}/generate-pdf/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("approval", str(r.data).lower())
        r = self.ops.post(f"/api/ops/production/{job}/start-printing/")
        self.assertEqual(r.status_code, 400)
        self.assertEqual(ProductionJob.objects.get(pk=job).status, "ready")
        # customer re-approves -> unblocked, and the order returns to PAID (payment was already verified)
        latest = self.cust.get(f"/api/projects/{pid}/revisions/").data[0]
        self.cust.post(f"/api/projects/{pid}/approve/", {"revision_id": latest["id"]}, format="json")
        order.refresh_from_db()
        self.assertEqual((order.status, order.payment_status), ("paid", "paid"))
        self.assertEqual(self.ops.post(f"/api/ops/production/{job}/generate-pdf/").status_code, 200)

    def test_only_operations_can_use_production_api(self):
        _, order = self.paid_order()
        self.assertEqual(self.cust.post("/api/ops/production/", {"order_id": str(order.id)}, format="json").status_code, 403)
        self.assertEqual(self.des.get("/api/ops/production/").status_code, 403)
        self.assertEqual(APIClient().get("/api/ops/production/").status_code, 403)

    # ---- print PDF ----
    def test_pdf_generated_and_validated_for_approved_revision(self):
        _, order = self.paid_order()
        job = self.create_job(order)
        r = self.ops.post(f"/api/ops/production/{job}/generate-pdf/")
        self.assertEqual(r.status_code, 200, r.content)
        self.assertTrue(r.data["pdf_valid"], r.data["pdf_report"])
        report = r.data["pdf_report"]
        self.assertEqual(report["page_count"], 21)
        self.assertEqual(report["bleed_mm"], 3.0)
        self.assertEqual(report["pixel_size"], [661, 661])  # (50 + 2*3) mm at 300 DPI
        self.assertEqual(r.data["revision_number"], 1)
        pdf = get_storage().read(ProductionJob.objects.get(pk=job).print_pdf_key)
        self.assertTrue(pdf.startswith(b"%PDF"))
        url = self.ops.get(f"/api/ops/production/{job}/pdf/").data["url"]
        self.assertIn("/api/storage/download/", url)

    def test_pdf_invalid_when_pages_missing(self):
        _, order = self.paid_order(pages=5)
        job = self.create_job(order)
        r = self.ops.post(f"/api/ops/production/{job}/generate-pdf/")
        self.assertFalse(r.data["pdf_valid"])
        self.assertIn("mismatch", " ".join(r.data["pdf_report"]["errors"]))
        r = self.ops.post(f"/api/ops/production/{job}/start-printing/")
        self.assertEqual(r.status_code, 400)  # invalid PDF cannot go to print

    def test_pdf_invalid_for_low_resolution_and_missing_photos(self):
        _, order = self.paid_order()
        # replace the stored original with a tiny image: stretched across the page it is far below 100 DPI
        small = io.BytesIO(); Image.new("RGB", (60, 60), "red").save(small, "JPEG")
        get_storage().save(self.photo.original_key, small.getvalue(), "image/jpeg")
        job = self.create_job(order)
        r = self.ops.post(f"/api/ops/production/{job}/generate-pdf/")
        self.assertFalse(r.data["pdf_valid"])
        self.assertIn("resolution too low", " ".join(r.data["pdf_report"]["errors"]))
        get_storage().delete(self.photo.original_key)
        r = self.ops.post(f"/api/ops/production/{job}/generate-pdf/")
        self.assertIn("missing", " ".join(r.data["pdf_report"]["errors"]))

    # ---- workflow ----
    def test_full_print_and_qc_flow(self):
        _, order = self.paid_order()
        job = self.run_to_qc(order)
        r = self.ops.post(f"/api/ops/production/{job}/qc/", {"checklist": {"size_pages_order": True, "images_ok": True}}, format="json")
        self.assertEqual(r.data["status"], "quality_check")  # partial checklist does not pass
        r = self.ops.post(f"/api/ops/production/{job}/qc/", {"checklist": {k: True for k in r.data["checklist"]}, "notes": "all good"}, format="json")
        self.assertEqual(r.data["status"], "passed")
        self.assertIsNotNone(r.data["qc_passed_at"])
        self.assertEqual(self.ops.post(f"/api/ops/production/{job}/qc/", {"checklist": {"bogus": True}}, format="json").status_code, 400)

    def test_qc_failure_returns_to_printing_and_cannot_skip_steps(self):
        _, order = self.paid_order()
        job = self.create_job(order)
        self.assertEqual(self.ops.post(f"/api/ops/production/{job}/quality-check/").status_code, 400)  # not printing yet
        self.assertEqual(self.ops.post(f"/api/ops/production/{job}/qc/", {"checklist": {}}, format="json").status_code, 400)
        job = self.run_to_qc(order)
        self.assertEqual(self.ops.post(f"/api/ops/production/{job}/qc-fail/", {}, format="json").status_code, 400)
        r = self.ops.post(f"/api/ops/production/{job}/qc-fail/", {"notes": "binding defect"}, format="json")
        self.assertEqual(r.data["status"], "printing")
        order.refresh_from_db(); self.assertEqual(order.status, "printing")
        self.assertEqual(self.ops.post(f"/api/ops/production/{job}/start-printing/").status_code, 400)  # already started

    def test_job_creation_idempotent_and_list_filter(self):
        _, order = self.paid_order()
        a = self.create_job(order); b = self.create_job(order)
        self.assertEqual(a, b)
        self.assertEqual(len(self.ops.get("/api/ops/production/?status=ready").data), 1)
        self.assertEqual(len(self.ops.get("/api/ops/production/?status=printing").data), 0)
