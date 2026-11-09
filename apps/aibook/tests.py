import io
import shutil
import uuid
import tempfile
from datetime import date
from unittest.mock import patch

from django.test import override_settings
from PIL import Image
from pypdf import PdfReader
from rest_framework.test import APIClient, APITestCase

from apps.accounts.models import Role, User
from apps.aidraft import layouts
from apps.aidraft.planner import PhotoInfo, build_book, sanitise_story
from apps.aidraft.providers import AIProviderError, ImageInput, MockProvider
from apps.aidraft.services import _merge
from apps.orders.models import Order
from apps.photos.storage import get_storage
from apps.projects.models import BookProject
from apps.trips.models import Trip

from . import render, services
from .models import AIBook, AIBookPhoto, AIBookRun

TMP = tempfile.mkdtemp(prefix="wodin-aibook-test-")


def jpeg(size=(1800, 1200), color=(200, 120, 60), seed=0):
    """A photo-like image. `seed` changes the picture's structure, so it is not flagged as a near-duplicate of another."""
    from PIL import ImageDraw
    img = Image.new("RGB", size, color)
    d = ImageDraw.Draw(img)
    w, h = size
    for k in range(6):
        x, y = ((seed * 37 + k * 211) % w), ((seed * 91 + k * 157) % h)
        r = 60 + ((seed + k) * 47) % 240
        d.ellipse([x - r, y - r, x + r, y + r], fill=((seed * 53 + k * 90) % 256, (k * 60 + seed * 29) % 256, (seed * 17 + k * 40) % 256))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def png(size=(1800, 1200)):
    buf = io.BytesIO()
    Image.new("RGB", size, (10, 80, 120)).save(buf, "PNG")
    return buf.getvalue()


@override_settings(AI_PROVIDER="test", STORAGE_BACKEND="local", PRIVATE_MEDIA_ROOT=TMP, AIBOOK_INLINE=True, AIBOOK_PDF_DPI=50)
class AIBookBase(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user("+919876543210", role=Role.DESIGNER)
        self.other = User.objects.create_user("+919123456789", role=Role.DESIGNER)
        self.c = APIClient(); self.c.force_authenticate(self.user)
        self.o = APIClient(); self.o.force_authenticate(self.other)

    def tearDown(self):
        shutil.rmtree(TMP, ignore_errors=True)

    def new_book(self, **kw):
        body = {"prompt": "Our family trip to Manali in May. Aarav and Meera.", "size": "portrait", **kw}
        r = self.c.post("/api/aibooks/", body, format="json")
        self.assertEqual(r.status_code, 201, r.content)
        return r.data["id"]

    def add_photos(self, book_id, n=8, client=None):
        """Mirror the browser: ask for signed URLs, PUT the bytes, then call complete."""
        client = client or self.c
        files = [{"filename": f"p{i}.jpg", "content_type": "image/jpeg", "size": 1000} for i in range(n)]
        r = client.post(f"/api/aibooks/{book_id}/photos/upload-urls/", {"files": files}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        ids = []
        for i, f in enumerate(r.data["files"]):
            self.assertNotIn("error", f, f)
            key = AIBookPhoto.objects.get(pk=f["photo_id"]).original_key
            get_storage().save(key, jpeg(color=(30 + i * 20, 100, 160), size=(1800, 1200) if i % 3 else (1200, 1800), seed=i + 1), "image/jpeg")
            done = client.post(f"/api/aibooks/photos/{f['photo_id']}/complete/")
            self.assertEqual(done.status_code, 200, done.content)
            ids.append(f["photo_id"])
        return ids


class AccessTests(AIBookBase):
    def test_login_required(self):
        self.assertIn(APIClient().get("/api/aibooks/").status_code, (401, 403))

    def test_owner_scoping(self):
        bid = self.new_book()
        self.assertEqual(self.o.get(f"/api/aibooks/{bid}/").status_code, 404)
        self.assertEqual(self.o.delete(f"/api/aibooks/{bid}/").status_code, 404)
        self.assertEqual(self.o.post(f"/api/aibooks/{bid}/start/").status_code, 404)
        self.assertEqual(self.o.get(f"/api/aibooks/{bid}/pdf/").status_code, 404)
        self.assertEqual(self.o.post(f"/api/aibooks/{bid}/photos/upload-urls/", {"files": [{"filename": "a.jpg", "content_type": "image/jpeg", "size": 5}]}, format="json").status_code, 404)
        self.assertEqual(self.o.get("/api/aibooks/").data, [])

    def test_other_users_photo_cannot_be_completed_or_deleted(self):
        bid = self.new_book()
        pid = self.add_photos(bid, 1)[0]
        self.assertEqual(self.o.post(f"/api/aibooks/photos/{pid}/complete/").status_code, 404)
        self.assertEqual(self.o.delete(f"/api/aibooks/photos/{pid}/").status_code, 404)

    def test_customers_and_other_staff_are_refused_but_admin_is_allowed(self):
        for phone, role, expected in (("+919000000009", Role.CUSTOMER, 403), ("+919000000010", Role.SUPPORT, 403),
                                      ("+919000000011", Role.OPERATIONS, 403), ("+919000000012", Role.ADMIN, 200)):
            c = APIClient(); c.force_authenticate(User.objects.create_user(phone, role=role))
            self.assertEqual(c.get("/api/aibooks/").status_code, expected, role)
            self.assertEqual(c.post("/api/aibooks/", {"prompt": "A long enough brief about a trip"}, format="json").status_code, 403 if expected == 403 else 201, role)

    def test_guest_is_refused(self):
        g = APIClient(); g.force_authenticate(User.objects.create_user("+919000000008", is_guest=True))
        self.assertEqual(g.get("/api/aibooks/").status_code, 403)

    def test_create_validation(self):
        r = self.c.post("/api/aibooks/", {"prompt": "short"}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/api/aibooks/", {"prompt": "A long enough brief about a trip", "size": "huge"}, format="json")
        self.assertEqual(r.status_code, 400)
        r = self.c.post("/api/aibooks/", {"prompt": "A long enough brief about a trip", "page_count": 2}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_does_not_touch_trips_orders_or_projects(self):
        bid = self.new_book()
        self.add_photos(bid, 6)
        self.assertEqual(self.c.post(f"/api/aibooks/{bid}/start/").status_code, 202)
        self.assertEqual((Trip.objects.count(), BookProject.objects.count(), Order.objects.count()), (0, 0, 0))


class UploadTests(AIBookBase):
    def test_upload_flow_makes_working_copies_and_flags(self):
        bid = self.new_book()
        pid = self.add_photos(bid, 1)[0]
        p = AIBookPhoto.objects.get(pk=pid)
        self.assertEqual(p.status, "ready")
        self.assertTrue(get_storage().exists(p.render_key) and get_storage().exists(p.thumb_key))
        listing = self.c.get(f"/api/aibooks/{bid}/photos/")
        self.assertEqual(len(listing.data), 1)
        self.assertTrue(listing.data[0]["thumb_url"])

    def test_bad_type_and_fake_content_are_rejected(self):
        bid = self.new_book()
        r = self.c.post(f"/api/aibooks/{bid}/photos/upload-urls/", {"files": [{"filename": "a.exe", "content_type": "application/x-msdownload", "size": 5}]}, format="json")
        self.assertIn("error", r.data["files"][0])
        r = self.c.post(f"/api/aibooks/{bid}/photos/upload-urls/", {"files": [{"filename": "a.jpg", "content_type": "image/jpeg", "size": 5}]}, format="json")
        photo = AIBookPhoto.objects.get(pk=r.data["files"][0]["photo_id"])
        get_storage().save(photo.original_key, b"not an image at all", "image/jpeg")
        done = self.c.post(f"/api/aibooks/photos/{photo.id}/complete/")
        self.assertEqual(done.data["status"], "rejected")

    def test_png_declared_as_jpeg_is_rejected(self):
        bid = self.new_book()
        r = self.c.post(f"/api/aibooks/{bid}/photos/upload-urls/", {"files": [{"filename": "a.jpg", "content_type": "image/jpeg", "size": 5}]}, format="json")
        photo = AIBookPhoto.objects.get(pk=r.data["files"][0]["photo_id"])
        get_storage().save(photo.original_key, png(), "image/jpeg")
        self.assertEqual(self.c.post(f"/api/aibooks/photos/{photo.id}/complete/").data["status"], "rejected")

    def test_tiny_photo_rejected_and_small_photo_flagged(self):
        bid = self.new_book()
        for size, expect in (((200, 150), "rejected"), ((700, 500), "ready")):
            r = self.c.post(f"/api/aibooks/{bid}/photos/upload-urls/", {"files": [{"filename": "a.jpg", "content_type": "image/jpeg", "size": 5}]}, format="json")
            photo = AIBookPhoto.objects.get(pk=r.data["files"][0]["photo_id"])
            get_storage().save(photo.original_key, jpeg(size=size), "image/jpeg")
            self.assertEqual(self.c.post(f"/api/aibooks/photos/{photo.id}/complete/").data["status"], expect)

    def test_complete_without_upload_fails(self):
        bid = self.new_book()
        r = self.c.post(f"/api/aibooks/{bid}/photos/upload-urls/", {"files": [{"filename": "a.jpg", "content_type": "image/jpeg", "size": 5}]}, format="json")
        self.assertEqual(self.c.post(f"/api/aibooks/photos/{r.data['files'][0]['photo_id']}/complete/").status_code, 400)

    @override_settings(AIBOOK_MAX_PHOTOS=3)
    def test_photo_cap(self):
        bid = self.new_book()
        files = [{"filename": f"{i}.jpg", "content_type": "image/jpeg", "size": 5} for i in range(4)]
        self.assertEqual(self.c.post(f"/api/aibooks/{bid}/photos/upload-urls/", {"files": files}, format="json").status_code, 400)

    def test_duplicate_is_flagged_and_left_out(self):
        bid = self.new_book()
        ids = []
        for _ in range(2):
            r = self.c.post(f"/api/aibooks/{bid}/photos/upload-urls/", {"files": [{"filename": "a.jpg", "content_type": "image/jpeg", "size": 5}]}, format="json")
            photo = AIBookPhoto.objects.get(pk=r.data["files"][0]["photo_id"])
            get_storage().save(photo.original_key, jpeg(color=(1, 2, 3)), "image/jpeg")
            self.c.post(f"/api/aibooks/photos/{photo.id}/complete/")
            ids.append(photo.id)
        self.assertIn("duplicate", AIBookPhoto.objects.get(pk=ids[1]).flags)
        self.assertEqual(len(services.usable_photos(AIBook.objects.get(pk=bid))), 1)

    def test_delete_photo_removes_files(self):
        bid = self.new_book()
        pid = self.add_photos(bid, 1)[0]
        p = AIBookPhoto.objects.get(pk=pid)
        self.assertEqual(self.c.delete(f"/api/aibooks/photos/{pid}/").status_code, 204)
        self.assertFalse(get_storage().exists(p.original_key))


class GenerationTests(AIBookBase):
    def test_start_needs_photos(self):
        bid = self.new_book()
        self.assertEqual(self.c.post(f"/api/aibooks/{bid}/start/").status_code, 400)

    def test_generate_gives_pdf_and_viewer_pages(self):
        bid = self.new_book(allow_questions=False)
        self.add_photos(bid, 8)
        r = self.c.post(f"/api/aibooks/{bid}/start/")
        self.assertEqual(r.status_code, 202, r.content)
        d = self.c.get(f"/api/aibooks/{bid}/").data
        self.assertEqual(d["status"], "ready", d)
        self.assertEqual(d["summary"]["photos_total"], 8, d["summary"])
        self.assertGreaterEqual(len(d["pages"]), 7)
        self.assertTrue(all(d["pages"]))
        self.assertGreater(d["pdf_size"], 0)
        self.assertEqual(d["progress"], 100)

        link = self.c.get(f"/api/aibooks/{bid}/pdf/").data
        self.assertTrue(link["filename"].endswith(".pdf"))
        res = APIClient().get(link["url"].replace("http://localhost:8000", ""))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res["Content-Type"], "application/pdf")
        self.assertIn("attachment", res["Content-Disposition"])
        data = b"".join(res.streaming_content)
        reader = PdfReader(io.BytesIO(data))
        self.assertEqual(len(reader.pages), len(d["pages"]))
        page = reader.pages[0]
        w, h = float(page.trimbox.width) / 72 * 25.4, float(page.trimbox.height) / 72 * 25.4
        self.assertAlmostEqual(w, 210, delta=1.5)
        self.assertAlmostEqual(h, 297, delta=1.5)
        self.assertGreater(float(page.mediabox.width), float(page.trimbox.width))  # there is bleed

    def test_inline_pdf_link(self):
        bid = self.new_book(allow_questions=False)
        self.add_photos(bid, 6)
        self.c.post(f"/api/aibooks/{bid}/start/")
        url = self.c.get(f"/api/aibooks/{bid}/pdf/?inline=1").data["url"].replace("http://localhost:8000", "")
        self.assertIn("inline", APIClient().get(url)["Content-Disposition"])

    def test_pdf_not_ready_and_bad_token(self):
        bid = self.new_book()
        self.assertEqual(self.c.get(f"/api/aibooks/{bid}/pdf/").status_code, 400)
        self.assertEqual(APIClient().get("/api/aibooks/pdf/garbage/").status_code, 403)

    def test_square_and_landscape_sizes(self):
        for size, ratio in (("square", 1.0), ("landscape", 297 / 210)):
            bid = self.new_book(size=size, allow_questions=False)
            self.add_photos(bid, 6)
            self.c.post(f"/api/aibooks/{bid}/start/")
            d = self.c.get(f"/api/aibooks/{bid}/").data
            self.assertEqual(d["status"], "ready", d)
            self.assertAlmostEqual(d["aspect"], ratio, places=3)

    def test_pages_are_borderless(self):
        bid = self.new_book(allow_questions=False)
        self.add_photos(bid, 8)
        self.c.post(f"/api/aibooks/{bid}/start/")
        book = AIBook.objects.get(pk=bid)
        covered = [e for e in book.pages[0]["elements"] if e["kind"] == "photo"]
        self.assertTrue(any(e["w"] >= 0.99 and e["h"] >= 0.99 for e in covered), "the cover photo should bleed over the full page")
        self.assertEqual(book.pages[0]["layout"], "cover")

    def test_questions_flow(self):
        bid = self.new_book()
        self.add_photos(bid, 6)
        qs = [{"id": "q1", "question": "Which language?", "hint": ""}, {"id": "q2", "question": "Who is in the photos?", "hint": ""}]
        with patch("apps.aibook.services.ai.ask_questions", return_value=(qs, 10, 5)):
            r = self.c.post(f"/api/aibooks/{bid}/start/")
        self.assertEqual(r.status_code, 202)
        d = self.c.get(f"/api/aibooks/{bid}/").data
        self.assertEqual(d["status"], "questions")
        self.assertEqual([q["id"] for q in d["questions"]], ["q1", "q2"])
        # unknown ids are ignored, answers are cleaned
        a = self.c.post(f"/api/aibooks/{bid}/answers/", {"answers": {"q1": "English", "zzz": "x", "q2": "Aarav\x00 and Meera"}}, format="json")
        self.assertEqual(a.status_code, 202, a.content)
        book = AIBook.objects.get(pk=bid)
        self.assertEqual(book.status, "ready", book.error)
        self.assertEqual(set(book.answers), {"q1", "q2"})
        self.assertNotIn("\x00", book.answers["q2"])

    def test_answer_when_not_asked_is_refused(self):
        bid = self.new_book()
        self.assertEqual(self.c.post(f"/api/aibooks/{bid}/answers/", {"answers": {}}, format="json").status_code, 400)

    def test_answers_reach_the_story_prompt_as_data(self):
        bid = self.new_book()
        self.add_photos(bid, 6)
        AIBook.objects.filter(pk=bid).update(status="questions", questions=[{"id": "q1", "question": "Tone?", "hint": ""}])
        seen = {}
        original = MockProvider.plan_story

        def spy(self_, images, context):
            seen.update(context)
            return original(self_, images, context)

        with patch.object(MockProvider, "plan_story", spy):
            self.c.post(f"/api/aibooks/{bid}/answers/", {"answers": {"q1": "funny"}}, format="json")
        self.assertEqual(seen["answers"], {"q1": "funny"})
        self.assertEqual(seen["questions"], {"q1": "Tone?"})
        self.assertIn("Manali", seen["brief"])

    def test_ai_failure_is_reported_and_retry_is_possible(self):
        bid = self.new_book(allow_questions=False)
        self.add_photos(bid, 6)
        with patch.object(MockProvider, "plan_story", side_effect=AIProviderError("The AI service is busy.")):
            self.c.post(f"/api/aibooks/{bid}/start/")
        d = self.c.get(f"/api/aibooks/{bid}/").data
        self.assertEqual((d["status"], d["error"]), ("failed", "The AI service is busy."))
        self.c.post(f"/api/aibooks/{bid}/start/")
        self.assertEqual(self.c.get(f"/api/aibooks/{bid}/").data["status"], "ready")

    def test_unexpected_error_hides_internals(self):
        bid = self.new_book(allow_questions=False)
        self.add_photos(bid, 6)
        with patch.object(MockProvider, "plan_story", side_effect=RuntimeError("db password=hunter2")):
            self.c.post(f"/api/aibooks/{bid}/start/")
        d = self.c.get(f"/api/aibooks/{bid}/").data
        self.assertEqual(d["status"], "failed")
        self.assertNotIn("hunter2", str(d))

    def test_question_step_failure_is_reported(self):
        bid = self.new_book()
        self.add_photos(bid, 6)
        with patch("apps.aibook.services.ai.ask_questions", side_effect=AIProviderError("The AI service is busy.")):
            self.c.post(f"/api/aibooks/{bid}/start/")
        self.assertEqual(self.c.get(f"/api/aibooks/{bid}/").data["status"], "failed")

    @override_settings(AIBOOK_MAX_GENERATIONS_PER_BOOK=1)
    def test_per_book_generation_cap(self):
        bid = self.new_book(allow_questions=False)
        self.add_photos(bid, 6)
        self.assertEqual(self.c.post(f"/api/aibooks/{bid}/start/").status_code, 202)
        self.assertEqual(self.c.post(f"/api/aibooks/{bid}/start/").status_code, 400)

    @override_settings(AIBOOK_MAX_RUNS_PER_DAY=1)
    def test_daily_cap(self):
        bid = self.new_book(allow_questions=False)
        self.add_photos(bid, 6)
        self.assertEqual(self.c.post(f"/api/aibooks/{bid}/start/").status_code, 202)
        self.assertEqual(AIBookRun.objects.filter(user=self.user).count(), 1)
        bid2 = self.new_book(allow_questions=False)
        self.add_photos(bid2, 6)
        r = self.c.post(f"/api/aibooks/{bid2}/start/")
        self.assertEqual(r.status_code, 400)
        self.assertIn("limit", str(r.data).lower())

    def test_busy_book_cannot_be_changed_or_deleted(self):
        bid = self.new_book()
        AIBook.objects.filter(pk=bid).update(status="generating")
        self.assertEqual(self.c.delete(f"/api/aibooks/{bid}/").status_code, 400)
        self.assertEqual(self.c.patch(f"/api/aibooks/{bid}/", {"title": "x"}, format="json").status_code, 400)
        self.assertEqual(self.c.post(f"/api/aibooks/{bid}/photos/upload-urls/", {"files": [{"filename": "a.jpg", "content_type": "image/jpeg", "size": 5}]}, format="json").status_code, 400)

    def test_stale_job_is_failed_instead_of_spinning_forever(self):
        from datetime import timedelta

        from django.utils import timezone
        bid = self.new_book()
        AIBook.objects.filter(pk=bid).update(status="generating", updated_at=timezone.now() - timedelta(minutes=30))
        d = self.c.get(f"/api/aibooks/{bid}/").data
        self.assertEqual(d["status"], "failed")
        self.assertTrue(d["error"])

    def test_delete_cleans_up_files(self):
        bid = self.new_book(allow_questions=False)
        ids = self.add_photos(bid, 6)
        self.c.post(f"/api/aibooks/{bid}/start/")
        book = AIBook.objects.get(pk=bid)
        keys = [book.pdf_key, *book.view_keys, *(AIBookPhoto.objects.get(pk=i).original_key for i in ids)]
        self.assertTrue(all(get_storage().exists(k) for k in keys))
        self.assertEqual(self.c.delete(f"/api/aibooks/{bid}/").status_code, 204)
        self.assertFalse(any(get_storage().exists(k) for k in keys))
        self.assertEqual(AIBook.objects.count(), 0)

    def test_list_shows_cover_when_ready(self):
        bid = self.new_book(allow_questions=False)
        self.add_photos(bid, 6)
        self.c.post(f"/api/aibooks/{bid}/start/")
        row = self.c.get("/api/aibooks/").data[0]
        self.assertTrue(row["cover"])
        self.assertEqual(row["pages"], [])

    def test_create_from_customer_project(self):
        from decimal import Decimal
        from apps.catalog.models import Product, ProductVariant
        from apps.photos.models import Photo
        customer = User.objects.create_user("+919999999999")
        trip = Trip.objects.create(
            user=customer,
            title="Himachal Roadtrip",
            destinations=["Shimla", "Manali"],
            description="Our lovely mountain getaway with family.",
            special_message="Treasured memories",
        )
        product = Product.objects.create(name="Photobook", slug="photobook-test")
        variant = ProductVariant.objects.create(
            product=product,
            name="A4 Portrait",
            base_page_count=20,
            allowed_page_counts=[20, 24],
            base_price=Decimal("1999"),
            additional_copy_price=Decimal("1500"),
            trim_width_mm=Decimal("210.0"),
            trim_height_mm=Decimal("297.0"),
        )
        project = BookProject.objects.create(
            user=customer,
            trip=trip,
            variant=variant,
            page_count=20,
            status=BookProject.Status.DESIGN,
        )
        # Create ready photos for the customer trip
        storage = get_storage()
        for i in range(6):
            pid = uuid.uuid4() if hasattr(uuid, "uuid4") else None
            key = f"users/{customer.id}/trips/{trip.id}/photos/p{i}/original.jpg"
            storage.save(key, jpeg(color=(50 + i * 20, 80, 140), seed=i + 1), "image/jpeg")
            Photo.objects.create(
                user=customer,
                trip=trip,
                original_filename=f"photo_{i}.jpg",
                content_type="image/jpeg",
                original_key=key,
                status=Photo.Status.READY,
                position=i,
            )

        # Call endpoint as designer
        res = self.c.post(f"/api/design/projects/{project.id}/ai-book/", {"allow_questions": False, "premium": True}, format="json")
        self.assertEqual(res.status_code, 201, res.content)
        data = res.data
        self.assertEqual(data["project_id"], str(project.id))
        self.assertIn("Himachal Roadtrip", data["title"])
        self.assertEqual(data["photo_count"], 6)

        # Verify AI book exists and is tied to project
        book = AIBook.objects.get(pk=data["id"])
        self.assertEqual(book.project, project)
        self.assertEqual(book.photos.count(), 6)

        # GET endpoint on project
        get_res = self.c.get(f"/api/design/projects/{project.id}/ai-book/")
        self.assertEqual(get_res.status_code, 200)
        self.assertEqual(get_res.data["id"], str(book.id))

        # GET endpoint on aibook detail returns project_id
        detail_res = self.c.get(f"/api/aibooks/{book.id}/")
        self.assertEqual(detail_res.status_code, 200)
        self.assertEqual(detail_res.data["project_id"], str(project.id))


class PlannerBorderlessTests(APITestCase):
    def infos(self, n, land=True):
        return [PhotoInfo(id=f"p{i}", width=3000 if land else 2000, height=2000 if land else 3000, flags=[], taken=date(2025, 5, 1 + i // 3)) for i in range(n)]

    def story(self, n, **facts):
        res = MockProvider().plan_story([ImageInput(b"x", "")] * n, {"title": "Goa", "target_scenes": 4})
        story = _merge([res.story], [n])
        story["facts"] = facts
        story["highlights"] = []  # the mock story has highlights, which alone would justify a details page
        return story

    def trip(self, **kw):
        return {"title": "Goa", "cover_title": "", "destinations": [], "companions": [], "dates_text": "", "special_message": "", "dedication": "", **kw}

    def test_all_borderless_layouts_stay_inside_the_page(self):
        for a in (210 / 297, 1.0, 297 / 210):
            for key in ("full_story", "full_story_top", "bleed_pair", "bleed_pair_side", "bleed_trio", "bleed_quad", "bleed_six", "details_b", "message_b"):
                lay = layouts.get(key, a)
                for e in [*lay["photos"], *lay["texts"]]:
                    self.assertGreaterEqual(e["x"], -1e-6, (key, a, e))
                    self.assertGreaterEqual(e["y"], -1e-6, (key, a, e))
                    self.assertLessEqual(e["x"] + e["w"], 1 + 1e-6, (key, a, e))
                    self.assertLessEqual(e["y"] + e["h"], 1 + 1e-6, (key, a, e))

    def test_borderless_photos_have_no_frame(self):
        for key in ("full_story", "bleed_pair", "bleed_trio", "bleed_quad", "bleed_six"):
            for p in layouts.get(key, 0.707)["photos"]:
                self.assertEqual(p["style"]["frame"], "none", key)

    def test_details_page_only_with_facts(self):
        undated = [PhotoInfo(id=i.id, width=i.width, height=i.height, flags=[], taken=None) for i in self.infos(10)]  # photo dates count as facts
        pages, _ = build_book(undated, self.story(10), 12, self.trip(), 0.707, borderless=True)
        self.assertNotIn("details_b", [p["layout"] for p in pages])
        pages, _ = build_book(self.infos(10), self.story(10, places=["Goa"], travellers=["Aarav"], dates="1-5 May"),
                              12, self.trip(destinations=["Goa"], companions=["Aarav"], dates_text="1-5 May"), 0.707, borderless=True)
        self.assertIn("details_b", [p["layout"] for p in pages])

    def test_dates_text_overrides_formatted_dates(self):
        pages, _ = build_book(self.infos(10), self.story(10), 12, self.trip(dates_text="Summer 2025"), 0.707, borderless=True)
        text = " ".join(e.get("text", "") for p in pages for e in p["elements"] if e["kind"] == "text")
        self.assertIn("SUMMER 2025", text.upper())

    def test_sanitise_story_keeps_facts_and_drops_junk(self):
        out = sanitise_story({"book_title": "T", "scenes": [], "facts": {"places": ["Goa", "x" * 500, 5], "travellers": "nope", "dates": "May\x00 2025"}}, 1)
        self.assertEqual(out["facts"]["places"][0], "Goa")
        self.assertLessEqual(max(len(p) for p in out["facts"]["places"]), 120)
        self.assertEqual(out["facts"]["travellers"], [])
        self.assertNotIn("\x00", out["facts"]["dates"])


class RendererTests(APITestCase):
    def test_clean_for_replaces_unsupported_glyphs(self):
        font = render._font("Inter", 30)
        self.assertEqual(render.clean_for(font, "Hello"), "Hello")
        self.assertNotIn("\u0928", render.clean_for(font, "\u0928\u092e\u0938\u094d\u0924\u0947"))

    def test_page_with_empty_and_huge_text_renders_to_exact_size(self):
        page = {"number": 1, "layout": "x", "elements": [
            {"kind": "text", "text": "", "x": 0.1, "y": 0.1, "w": 0.8, "h": 0.1, "z": 1, "style": {"variant": "script", "size": "lg"}},
            {"kind": "text", "text": "word " * 600, "x": 0.1, "y": 0.3, "w": 0.8, "h": 0.2, "z": 2, "style": {"variant": "body", "size": "md"}},
            {"kind": "photo", "photo": "missing", "x": 0, "y": 0.6, "w": 1, "h": 0.4, "z": 0, "style": {}}]}
        img = render.PageRenderer(300, 400, 10, lambda pid: None).render(page)
        self.assertEqual(img.size, (320, 420))

    def test_pdf_has_trim_and_bleed_boxes(self):
        img = Image.new("RGB", (320, 420), "white")
        pdf = render.merge_pdfs([render.page_pdf(img, 100), render.page_pdf(img, 100)], 100, 10)
        reader = PdfReader(io.BytesIO(pdf))
        self.assertEqual(len(reader.pages), 2)
        self.assertLess(float(reader.pages[0].trimbox.width), float(reader.pages[0].mediabox.width))
