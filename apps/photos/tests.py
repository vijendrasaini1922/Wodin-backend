import io
import random
import shutil
import tempfile
from uuid import UUID

from django.test import Client, override_settings
from PIL import Image, ImageDraw
from rest_framework.test import APIClient, APITestCase

from apps.accounts.models import User
from apps.trips.models import Trip

from .models import Photo
from .processing import hamming

TMP = tempfile.mkdtemp(prefix="wodin-test-media-")


def noisy_jpeg(size=(1600, 1200), seed=1, fmt="JPEG"):
    """Sharp, high-detail image (random blocks) so blur detection does not flag it."""
    rnd = random.Random(seed)
    img = Image.new("RGB", size, "white")
    d = ImageDraw.Draw(img)
    for _ in range(600):
        x, y = rnd.randrange(size[0]), rnd.randrange(size[1])
        d.rectangle([x, y, x + rnd.randrange(5, 60), y + rnd.randrange(5, 60)],
                    fill=tuple(rnd.randrange(256) for _ in range(3)))
    buf = io.BytesIO()
    img.save(buf, fmt)
    return buf.getvalue()


def flat_jpeg(size=(1600, 1200)):
    buf = io.BytesIO()
    Image.new("RGB", size, (120, 130, 140)).save(buf, "JPEG")
    return buf.getvalue()


@override_settings(STORAGE_BACKEND="local", PRIVATE_MEDIA_ROOT=TMP, BACKEND_URL="http://testserver", ALLOWED_HOSTS=["testserver"])
class PhotoFlowTests(APITestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(TMP, ignore_errors=True)

    def setUp(self):
        self.user = User.objects.create_user("+919876543210")
        self.other = User.objects.create_user("+919123456789")
        self.trip = Trip.objects.create(user=self.user, title="T", destinations=["X"])
        self.client.force_authenticate(self.user)

    def upload(self, data, name="a.jpg", ctype="image/jpeg", client=None):
        client = client or self.client
        r = client.post(f"/api/trips/{self.trip.id}/photos/upload-urls/",
                        {"files": [{"filename": name, "content_type": ctype, "size": len(data)}]}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        item = r.data["files"][0]
        if "error" in item:
            return item
        path = item["upload"]["url"].replace("http://testserver", "")
        put = Client().put(path, data=data, content_type=ctype)  # unauthenticated, signed URL only
        self.assertEqual(put.status_code, 200, put.content)
        done = client.post(f"/api/photos/{item['photo_id']}/complete/")
        self.assertEqual(done.status_code, 200, done.content)
        return done.data

    def test_happy_path_ready_with_private_signed_urls(self):
        photo = self.upload(noisy_jpeg())
        self.assertEqual(photo["status"], "ready")
        self.assertEqual((photo["width"], photo["height"]), (1600, 1200))
        self.assertEqual(photo["flags"], [])
        got = Client().get(photo["thumb_url"].replace("http://testserver", ""))
        self.assertEqual(got.status_code, 200)
        thumb = Image.open(io.BytesIO(b"".join(got.streaming_content)))
        self.assertLessEqual(max(thumb.size), 400)
        self.assertEqual(Client().get("/api/storage/download/garbage/").status_code, 403)

    def test_png_and_heic_accepted(self):
        self.assertEqual(self.upload(noisy_jpeg(fmt="PNG"), "b.png", "image/png")["status"], "ready")
        self.assertEqual(self.upload(noisy_jpeg(seed=9, fmt="HEIF"), "c.heic", "image/heic")["status"], "ready")

    def test_unsupported_type_rejected_at_registration(self):
        self.assertIn("error", self.upload(b"x", "a.gif", "image/gif"))

    def test_corrupt_and_mismatched_files_rejected(self):
        self.assertEqual(self.upload(b"not an image at all", "a.jpg")["status"], "rejected")
        self.assertEqual(self.upload(noisy_jpeg(fmt="PNG"), "a.jpg", "image/jpeg")["status"], "rejected")

    def test_too_small_rejected_and_low_res_flagged(self):
        self.assertEqual(self.upload(noisy_jpeg((200, 200)))["status"], "rejected")
        self.assertIn("low_resolution", self.upload(noisy_jpeg((800, 600), seed=3))["flags"])

    def test_blur_flag(self):
        self.assertIn("blurry", self.upload(flat_jpeg())["flags"])

    def test_exact_and_near_duplicates(self):
        data = noisy_jpeg(seed=5)
        self.upload(data)
        self.assertIn("duplicate", self.upload(data, "copy.jpg")["flags"])
        small = Image.open(io.BytesIO(data)).resize((1500, 1125))
        buf = io.BytesIO()
        small.save(buf, "JPEG", quality=70)
        self.assertIn("near_duplicate", self.upload(buf.getvalue(), "resized.jpg")["flags"])
        self.assertNotIn("near_duplicate", self.upload(noisy_jpeg(seed=77), "other.jpg")["flags"])

    def test_hundred_photos_upload_reliably_in_batches(self):
        """Launch acceptance: a customer can upload at least 100 photographs reliably."""
        client, ids = self.client, []
        for batch in range(10):
            files = []
            blobs = [noisy_jpeg((1300, 1000), seed=1000 + batch * 10 + i) for i in range(10)]
            for i, data in enumerate(blobs):
                files.append({"filename": f"p{batch}_{i}.jpg", "content_type": "image/jpeg", "size": len(data)})
            reg = client.post(f"/api/trips/{self.trip.id}/photos/upload-urls/", {"files": files}, format="json")
            self.assertEqual(reg.status_code, 200, reg.content)
            for item, data in zip(reg.data["files"], blobs):
                path = item["upload"]["url"].replace("http://testserver", "")
                self.assertEqual(Client().put(path, data=data, content_type="image/jpeg").status_code, 200)
                done = client.post(f"/api/photos/{item['photo_id']}/complete/")
                self.assertEqual(done.status_code, 200, done.content)
                ids.append(done.data["id"])
        self.assertEqual(len(set(ids)), 100)
        self.assertEqual(Photo.objects.filter(trip=self.trip, status="ready").count(), 100)

    def test_complete_without_upload_fails_and_tampered_url_rejected(self):
        r = self.client.post(f"/api/trips/{self.trip.id}/photos/upload-urls/",
                             {"files": [{"filename": "a.jpg", "content_type": "image/jpeg", "size": 10}]}, format="json")
        pid = r.data["files"][0]["photo_id"]
        self.assertEqual(self.client.post(f"/api/photos/{pid}/complete/").status_code, 400)
        bad = r.data["files"][0]["upload"]["url"].replace("http://testserver", "")[:-3] + "xx/"
        self.assertEqual(Client().put(bad, data=b"x", content_type="image/jpeg").status_code, 403)

    def test_manage_rotate_crop_favourite_reorder_group_delete(self):
        a, b = self.upload(noisy_jpeg(seed=11)), self.upload(noisy_jpeg(seed=12), "b.jpg")
        r = self.client.patch(f"/api/photos/{a['id']}/", {"rotation": 90, "is_favourite": True, "caption": "Hi",
                                                         "crop": {"x": 0, "y": 0, "w": 0.5, "h": 0.5}}, format="json")
        self.assertEqual(r.status_code, 200, r.content)
        thumb = Image.open(io.BytesIO(b"".join(Client().get(r.data["thumb_url"].replace("http://testserver", "")).streaming_content)))
        self.assertGreater(thumb.size[0] / thumb.size[1], 0.5)  # rotated+cropped, derivative regenerated
        self.assertEqual(self.client.patch(f"/api/photos/{a['id']}/", {"rotation": 45}, format="json").status_code, 400)
        self.assertEqual(self.client.patch(f"/api/photos/{a['id']}/", {"crop": {"x": 0.9, "y": 0, "w": 0.5, "h": 1}}, format="json").status_code, 400)

        reorder = self.client.post(f"/api/photos/reorder/{self.trip.id}/", {"ids": [b["id"], a["id"]]}, format="json")
        self.assertEqual(reorder.status_code, 200, reorder.content)
        ids = [p["id"] for p in self.client.get(f"/api/trips/{self.trip.id}/photos/").data]
        self.assertEqual(ids, [b["id"], a["id"]])

        g = self.client.post(f"/api/trips/{self.trip.id}/photo-groups/", {"title": "Day 1"}, format="json")
        self.assertEqual(g.status_code, 201)
        self.assertEqual(self.client.patch(f"/api/photos/{a['id']}/", {"group": g.data["id"]}, format="json").data["group"], UUID(str(g.data["id"])))

        self.assertEqual(self.client.delete(f"/api/photos/{a['id']}/").status_code, 204)
        self.assertFalse(Photo.objects.filter(pk=a["id"]).exists())

    def test_original_preserved_after_edit(self):
        a = self.upload(noisy_jpeg(seed=21))
        self.client.patch(f"/api/photos/{a['id']}/", {"rotation": 180}, format="json")
        from .storage import get_storage
        key = Photo.objects.get(pk=a["id"]).original_key
        self.assertEqual(Image.open(io.BytesIO(get_storage().read(key))).size, (1600, 1200))

    def test_access_control_between_users(self):
        a = self.upload(noisy_jpeg(seed=31))
        other = APIClient()
        other.force_authenticate(self.other)
        self.assertEqual(other.get(f"/api/photos/{a['id']}/").status_code, 404)
        self.assertEqual(other.get(f"/api/trips/{self.trip.id}/photos/").status_code, 404)
        self.assertEqual(other.post(f"/api/trips/{self.trip.id}/photos/upload-urls/", {"files": []}, format="json").status_code, 404)
        self.assertEqual(other.post(f"/api/photos/{a['id']}/complete/").status_code, 404)
        self.assertEqual(APIClient().get(f"/api/trips/{self.trip.id}/photos/").status_code, 403)

    def test_hamming(self):
        self.assertEqual(hamming("0000000000000000", "000000000000000f"), 4)
