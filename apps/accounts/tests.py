from django.core.cache import cache
from django.test import override_settings
from rest_framework.test import APIClient, APITestCase

from .models import OTPCode, User
from .otp import hash_code
from .phone import normalise_phone


def set_known_code(phone, code="123456"):
    otp = OTPCode.objects.filter(phone=phone).latest("created_at")
    otp.code_hash = hash_code(phone, code)
    otp.save()


class PhoneTests(APITestCase):
    def test_normalise(self):
        self.assertEqual(normalise_phone("98765 43210"), "+919876543210")
        self.assertEqual(normalise_phone("+91-9876543210"), "+919876543210")
        self.assertEqual(normalise_phone("09876543210"), "+919876543210")

    def test_invalid(self):
        from rest_framework.exceptions import ValidationError

        for bad in ["12345", "5876543210", "abcdefghij", ""]:
            with self.assertRaises(ValidationError):
                normalise_phone(bad)


@override_settings(OTP_PROVIDER="dummy")
class AuthFlowTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.phone = "+919876543210"

    def request_otp(self):
        return self.client.post("/api/auth/otp/request/", {"phone": "9876543210"})

    def test_full_login_flow_and_me(self):
        self.assertEqual(self.request_otp().status_code, 200)
        self.assertNotIn("123456", str(OTPCode.objects.get().code_hash))
        set_known_code(self.phone)
        r = self.client.post("/api/auth/otp/verify/", {"phone": self.phone, "code": "123456"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["is_new"])
        me = self.client.get("/api/me/")
        self.assertEqual(me.data["phone"], self.phone)
        self.assertEqual(self.client.patch("/api/me/", {"full_name": "Asha"}, format="json").data["full_name"], "Asha")
        self.assertEqual(self.client.post("/api/auth/logout/").status_code, 204)
        self.assertEqual(self.client.get("/api/me/").status_code, 403)

    def test_code_is_single_use(self):
        self.request_otp()
        set_known_code(self.phone)
        self.assertEqual(self.client.post("/api/auth/otp/verify/", {"phone": self.phone, "code": "123456"}).status_code, 200)
        self.assertEqual(APIClient().post("/api/auth/otp/verify/", {"phone": self.phone, "code": "123456"}).status_code, 400)

    def test_wrong_code_and_attempt_limit(self):
        self.request_otp()
        set_known_code(self.phone)
        for _ in range(5):
            self.assertEqual(self.client.post("/api/auth/otp/verify/", {"phone": self.phone, "code": "000000"}).status_code, 400)
        # even the right code is rejected after max attempts
        self.assertEqual(self.client.post("/api/auth/otp/verify/", {"phone": self.phone, "code": "123456"}).status_code, 400)

    def test_resend_cooldown(self):
        self.assertEqual(self.request_otp().status_code, 200)
        self.assertEqual(self.request_otp().status_code, 429)

    def test_request_does_not_reveal_account_existence(self):
        User.objects.create_user(self.phone)
        known = self.request_otp()
        cache.clear()
        OTPCode.objects.all().delete()
        unknown = self.client.post("/api/auth/otp/request/", {"phone": "9123456789"})
        self.assertEqual(known.data, unknown.data)

    def test_invalid_phone_rejected(self):
        self.assertEqual(self.client.post("/api/auth/otp/request/", {"phone": "123"}).status_code, 400)


class AddressTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user("+919876543210")
        self.other = User.objects.create_user("+919123456789")
        self.client.force_authenticate(self.user)
        self.payload = dict(recipient_name="Asha", phone="+919876543210", line1="1 MG Road", city="Pune", state="MH", pincode="411001")

    def test_first_address_default_and_switch(self):
        a = self.client.post("/api/addresses/", self.payload, format="json")
        self.assertTrue(a.data["is_default"])
        b = self.client.post("/api/addresses/", {**self.payload, "is_default": True}, format="json")
        self.assertTrue(b.data["is_default"])
        self.assertEqual(self.user.addresses.filter(is_default=True).count(), 1)

    def test_pincode_validation(self):
        self.assertEqual(self.client.post("/api/addresses/", {**self.payload, "pincode": "12"}, format="json").status_code, 400)

    def test_cannot_access_other_users_address(self):
        a = self.client.post("/api/addresses/", self.payload, format="json")
        self.client.force_authenticate(self.other)
        self.assertEqual(self.client.get(f"/api/addresses/{a.data['id']}/").status_code, 404)

    def test_unauthenticated(self):
        self.assertEqual(APIClient().get("/api/addresses/").status_code, 403)


class MSG91ProviderTests(APITestCase):
    """MSG91 is always mocked here: no real SMS is ever sent from the test suite."""

    phone = "+919876543210"

    def _resp(self, status=200, body=None):
        from unittest.mock import Mock
        r = Mock(status_code=status, content=b"x")
        r.json.return_value = body if body is not None else {"type": "success", "message": "id"}
        return r

    @override_settings(OTP_PROVIDER="msg91", MSG91_AUTH_KEY="k", MSG91_TEMPLATE_ID="t")
    def test_success_sends_template_mobile_and_otp(self):
        from unittest.mock import patch
        cache.clear()
        with patch("apps.accounts.otp.requests.post", return_value=self._resp()) as post:
            r = APIClient().post("/api/auth/otp/request/", {"phone": self.phone})
        self.assertEqual(r.status_code, 200)
        params = post.call_args.kwargs["params"]
        self.assertEqual(params["template_id"], "t")
        self.assertEqual(params["mobile"], "919876543210")
        self.assertEqual(len(params["otp"]), 6)
        self.assertEqual(post.call_args.kwargs["headers"]["authkey"], "k")
        self.assertEqual(OTPCode.objects.filter(phone=self.phone).count(), 1)

    @override_settings(OTP_PROVIDER="msg91", MSG91_AUTH_KEY="k", MSG91_TEMPLATE_ID="t")
    def test_http_200_with_error_body_is_a_failure_and_leaves_no_code(self):
        from unittest.mock import patch
        cache.clear()
        bad = self._resp(200, {"type": "error", "message": "Template not approved"})
        with patch("apps.accounts.otp.requests.post", return_value=bad):
            r = APIClient().post("/api/auth/otp/request/", {"phone": self.phone})
        self.assertEqual(r.status_code, 503)
        self.assertNotIn("Template", str(r.data))  # provider detail stays in the server log
        self.assertEqual(OTPCode.objects.filter(phone=self.phone).count(), 0)  # user may retry immediately

    @override_settings(OTP_PROVIDER="msg91", MSG91_AUTH_KEY="k", MSG91_TEMPLATE_ID="t")
    def test_network_error_returns_503(self):
        import requests
        from unittest.mock import patch
        cache.clear()
        with patch("apps.accounts.otp.requests.post", side_effect=requests.ConnectionError("boom")):
            r = APIClient().post("/api/auth/otp/request/", {"phone": self.phone})
        self.assertEqual(r.status_code, 503)

    @override_settings(OTP_PROVIDER="msg91", MSG91_AUTH_KEY="", MSG91_TEMPLATE_ID="")
    def test_missing_configuration_returns_503_without_calling_msg91(self):
        from unittest.mock import patch
        cache.clear()
        with patch("apps.accounts.otp.requests.post") as post:
            r = APIClient().post("/api/auth/otp/request/", {"phone": self.phone})
        self.assertEqual(r.status_code, 503)
        post.assert_not_called()
