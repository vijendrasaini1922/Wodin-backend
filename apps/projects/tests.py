from decimal import Decimal

from rest_framework.test import APIClient, APITestCase

from apps.accounts.models import Role, User
from apps.catalog.models import Product, ProductVariant
from apps.photos.models import Photo
from apps.trips.models import Trip

from .models import Approval, BookProject
from .services import latest_valid_approval


class ProjectBase(APITestCase):
    def setUp(self):
        self.customer = User.objects.create_user("+919876543210")
        self.other = User.objects.create_user("+919123456789")
        self.designer = User.objects.create_user("+919000000001", role=Role.DESIGNER)
        self.trip = Trip.objects.create(user=self.customer, title="Manali", destinations=["Manali"])
        product = Product.objects.create(name="P", slug="p")
        self.variant = ProductVariant.objects.create(
            product=product, name="A4", base_page_count=20, allowed_page_counts=[20, 24],
            base_price=Decimal("1999"), additional_copy_price=Decimal("1500"))
        self.photo = Photo.objects.create(
            user=self.customer, trip=self.trip, original_filename="a.jpg", content_type="image/jpeg",
            original_key="k/original.jpg", preview_key="k/preview.jpg", status=Photo.Status.READY)
        self.cust = APIClient(); self.cust.force_authenticate(self.customer)
        self.des = APIClient(); self.des.force_authenticate(self.designer)

    def make_project(self, submit=True):
        r = self.cust.post("/api/projects/", {"trip": str(self.trip.id), "variant": self.variant.pk, "page_count": 20}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        pid = r.data["id"]
        if submit:
            self.assertEqual(self.cust.post(f"/api/projects/{pid}/submit/").status_code, 200)
        return pid

    def layout(self, pid, text="Hello"):
        pages = [{"number": 0, "layout": "cover", "elements": [
            {"kind": "photo", "photo": str(self.photo.id), "x": 0, "y": 0, "w": 1, "h": 0.8, "z": 0},
            {"kind": "text", "text": text, "x": 0.1, "y": 0.85, "w": 0.8, "h": 0.1, "z": 1}]}]
        r = self.des.put(f"/api/design/projects/{pid}/pages/", pages, format="json")
        self.assertEqual(r.status_code, 200, r.content)

    def publish(self, pid, notes=""):
        r = self.des.post(f"/api/design/projects/{pid}/publish-revision/", {"notes": notes}, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        return r.data


class ProjectFlowTests(ProjectBase):
    def test_create_validates_ownership_and_options(self):
        other_trip = Trip.objects.create(user=self.other, title="x", destinations=["y"])
        bad = self.cust.post("/api/projects/", {"trip": str(other_trip.id), "variant": self.variant.pk, "page_count": 20}, format="json")
        self.assertEqual(bad.status_code, 400)
        bad = self.cust.post("/api/projects/", {"trip": str(self.trip.id), "variant": self.variant.pk, "page_count": 22}, format="json")
        self.assertEqual(bad.status_code, 400)

    def test_submit_needs_photos(self):
        Photo.objects.all().delete()
        pid = self.make_project(submit=False)
        self.assertEqual(self.cust.post(f"/api/projects/{pid}/submit/").status_code, 400)

    def test_designer_cannot_publish_draft_or_empty(self):
        pid = self.make_project(submit=False)
        self.assertEqual(self.des.post(f"/api/design/projects/{pid}/publish-revision/").status_code, 404)  # drafts hidden
        pid2 = self.make_project()
        self.assertEqual(self.des.post(f"/api/design/projects/{pid2}/publish-revision/").status_code, 400)  # no pages

    def test_customers_cannot_use_design_api(self):
        pid = self.make_project()
        self.assertEqual(self.cust.get("/api/design/projects/").status_code, 403)
        self.assertEqual(self.cust.put(f"/api/design/projects/{pid}/pages/", [], format="json").status_code, 403)

    def test_layout_validation(self):
        pid = self.make_project()
        bad_geom = [{"number": 0, "elements": [{"kind": "text", "text": "x", "x": 2, "y": 0, "w": 1, "h": 1}]}]
        self.assertEqual(self.des.put(f"/api/design/projects/{pid}/pages/", bad_geom, format="json").status_code, 400)
        too_many = [{"number": 99, "elements": []}]
        self.assertEqual(self.des.put(f"/api/design/projects/{pid}/pages/", too_many, format="json").status_code, 400)
        dup = [{"number": 1, "elements": []}, {"number": 1, "elements": []}]
        self.assertEqual(self.des.put(f"/api/design/projects/{pid}/pages/", dup, format="json").status_code, 400)

    def test_cannot_use_photo_from_another_trip(self):
        pid = self.make_project()
        foreign = Photo.objects.create(user=self.other, trip=Trip.objects.create(user=self.other, title="o", destinations=["o"]),
                                       original_filename="f.jpg", content_type="image/jpeg", original_key="z", status=Photo.Status.READY)
        pages = [{"number": 1, "elements": [{"kind": "photo", "photo": str(foreign.id), "x": 0, "y": 0, "w": 1, "h": 1}]}]
        self.assertEqual(self.des.put(f"/api/design/projects/{pid}/pages/", pages, format="json").status_code, 400)

    def test_publish_review_and_approve_exact_revision(self):
        pid = self.make_project()
        self.layout(pid)
        rev = self.publish(pid, "first draft")
        self.assertEqual(rev["number"], 1)
        self.assertEqual(rev["snapshot"]["pages"][0]["elements"][1]["text"], "Hello")
        self.assertEqual(BookProject.objects.get(pk=pid).status, "preview_ready")

        listed = self.cust.get(f"/api/projects/{pid}/revisions/").data
        self.assertEqual(len(listed), 1)

        self.assertEqual(self.cust.post(f"/api/projects/{pid}/approve/", {}, format="json").status_code, 400)  # must name revision
        ok = self.cust.post(f"/api/projects/{pid}/approve/", {"revision_id": rev["id"]}, format="json")
        self.assertEqual(ok.status_code, 200, ok.content)
        self.assertEqual(BookProject.objects.get(pk=pid).status, "approved")
        approval = latest_valid_approval(BookProject.objects.get(pk=pid))
        self.assertEqual(str(approval.revision_id), rev["id"])
        self.assertEqual(approval.user, self.customer)
        # approving twice is rejected
        self.assertEqual(self.cust.post(f"/api/projects/{pid}/approve/", {"revision_id": rev["id"]}, format="json").status_code, 400)

    def test_new_revision_invalidates_prior_approval_and_blocks_stale_approval(self):
        pid = self.make_project()
        self.layout(pid)
        rev1 = self.publish(pid)
        self.cust.post(f"/api/projects/{pid}/approve/", {"revision_id": rev1["id"]}, format="json")
        self.assertIsNotNone(latest_valid_approval(BookProject.objects.get(pk=pid)))

        self.layout(pid, text="Changed after approval")
        rev2 = self.publish(pid)
        self.assertEqual(rev2["number"], 2)
        self.assertIsNone(latest_valid_approval(BookProject.objects.get(pk=pid)))  # prior approval invalidated
        a = Approval.objects.get()
        self.assertIsNotNone(a.invalidated_at)  # kept for audit, not deleted
        self.assertEqual(BookProject.objects.get(pk=pid).status, "preview_ready")

        stale = self.cust.post(f"/api/projects/{pid}/approve/", {"revision_id": rev1["id"]}, format="json")
        self.assertEqual(stale.status_code, 400)
        self.assertEqual(self.cust.post(f"/api/projects/{pid}/approve/", {"revision_id": rev2["id"]}, format="json").status_code, 200)

    def test_snapshot_is_immutable_after_layout_edit(self):
        pid = self.make_project()
        self.layout(pid, "v1")
        rev = self.publish(pid)
        self.layout(pid, "v2")  # editing working layout must not change the sent revision
        stored = self.cust.get(f"/api/projects/{pid}/revisions/").data[0]
        self.assertEqual(stored["snapshot"]["pages"][0]["elements"][1]["text"], "v1")
        self.assertEqual(stored["id"], rev["id"])

    def test_change_requests_flow(self):
        pid = self.make_project()
        self.layout(pid)
        self.publish(pid)
        r = self.cust.post(f"/api/projects/{pid}/request-changes/", {"page_number": 0, "note": "Use a different cover photo"}, format="json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(BookProject.objects.get(pk=pid).status, "changes_requested")
        self.assertEqual(self.des.get(f"/api/design/projects/{pid}/change-requests/").data[0]["note"], "Use a different cover photo")
        self.publish(pid, "fixed")
        self.assertTrue(self.des.get(f"/api/design/projects/{pid}/change-requests/").data[0]["resolved"])
        self.assertEqual(self.cust.post(f"/api/projects/{pid}/request-changes/", {"note": ""}, format="json").status_code, 400)

    def test_other_customer_cannot_see_or_approve(self):
        pid = self.make_project()
        self.layout(pid)
        rev = self.publish(pid)
        other = APIClient(); other.force_authenticate(self.other)
        self.assertEqual(other.get(f"/api/projects/{pid}/").status_code, 404)
        self.assertEqual(other.post(f"/api/projects/{pid}/approve/", {"revision_id": rev["id"]}, format="json").status_code, 404)
