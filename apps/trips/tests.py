from rest_framework.test import APIClient, APITestCase

from apps.accounts.models import User


class TripTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user("+919876543210")
        self.other = User.objects.create_user("+919123456789")
        self.client.force_authenticate(self.user)
        self.payload = {"title": "Manali Memories", "destinations": ["Manali", "Solang"],
                        "start_date": "2026-05-01", "end_date": "2026-05-06"}

    def test_create_list_update_delete(self):
        r = self.client.post("/api/trips/", self.payload, format="json")
        self.assertEqual(r.status_code, 201)
        tid = r.data["id"]
        self.assertEqual(self.client.get("/api/trips/").data["results"][0]["title"], "Manali Memories")
        self.assertEqual(self.client.patch(f"/api/trips/{tid}/", {"dedication": "For Mom"}, format="json").status_code, 200)
        self.assertEqual(self.client.delete(f"/api/trips/{tid}/").status_code, 204)

    def test_date_validation_and_destination_required(self):
        bad = {**self.payload, "end_date": "2026-04-01"}
        self.assertEqual(self.client.post("/api/trips/", bad, format="json").status_code, 400)
        self.assertEqual(self.client.post("/api/trips/", {**self.payload, "destinations": []}, format="json").status_code, 400)

    def test_isolation_between_users(self):
        tid = self.client.post("/api/trips/", self.payload, format="json").data["id"]
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f"/api/trips/{tid}/").status_code, 404)
        self.assertEqual(self.client.get("/api/trips/").data["count"], 0)

    def test_requires_login(self):
        self.assertEqual(APIClient().get("/api/trips/").status_code, 403)
