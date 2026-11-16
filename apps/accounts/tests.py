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


WA_SETTINGS = dict(OTP_PROVIDER="whatsapp", WHATSAPP_ACCESS_TOKEN="tok", WHATSAPP_PHONE_NUMBER_ID="123",
                   WHATSAPP_TEMPLATE_NAME="wodin_login_otp")


class WhatsAppProviderTests(APITestCase):
    """WhatsApp Cloud API is always mocked here: no real message is ever sent from the test suite."""

    phone = "+919876543210"

    def _resp(self, status=200, body=None):
        from unittest.mock import Mock
        r = Mock(status_code=status, content=b"x")
        r.json.return_value = body if body is not None else {"messages": [{"id": "wamid.1"}]}
        return r

    @override_settings(**WA_SETTINGS)
    def test_success_sends_authentication_template(self):
        from unittest.mock import patch
        cache.clear()
        with patch("apps.accounts.otp.requests.post", return_value=self._resp()) as post:
            r = APIClient().post("/api/auth/otp/request/", {"phone": self.phone})
        self.assertEqual(r.status_code, 200)
        self.assertIn("/123/messages", post.call_args.args[0])
        self.assertEqual(post.call_args.kwargs["headers"]["Authorization"], "Bearer tok")
        payload = post.call_args.kwargs["json"]
        self.assertEqual(payload["to"], "919876543210")
        self.assertEqual(payload["template"]["name"], "wodin_login_otp")
        code = payload["template"]["components"][0]["parameters"][0]["text"]
        self.assertEqual(len(code), 6)
        self.assertEqual(payload["template"]["components"][1]["parameters"][0]["text"], code)
        self.assertEqual(OTPCode.objects.filter(phone=self.phone).count(), 1)

    @override_settings(**WA_SETTINGS)
    def test_rejection_returns_503_and_leaves_no_code(self):
        from unittest.mock import patch
        cache.clear()
        bad = self._resp(400, {"error": {"code": 132001, "message": "Template does not exist"}})
        with patch("apps.accounts.otp.requests.post", return_value=bad):
            r = APIClient().post("/api/auth/otp/request/", {"phone": self.phone})
        self.assertEqual(r.status_code, 503)
        self.assertNotIn("Template", str(r.data))
        self.assertEqual(OTPCode.objects.filter(phone=self.phone).count(), 0)

    @override_settings(**WA_SETTINGS)
    def test_network_error_returns_503(self):
        import requests
        from unittest.mock import patch
        cache.clear()
        with patch("apps.accounts.otp.requests.post", side_effect=requests.ConnectionError("boom")):
            r = APIClient().post("/api/auth/otp/request/", {"phone": self.phone})
        self.assertEqual(r.status_code, 503)

    @override_settings(OTP_PROVIDER="whatsapp", WHATSAPP_ACCESS_TOKEN="", WHATSAPP_PHONE_NUMBER_ID="",
                       WHATSAPP_TEMPLATE_NAME="")
    def test_missing_configuration_returns_503_without_calling_whatsapp(self):
        from unittest.mock import patch
        cache.clear()
        with patch("apps.accounts.otp.requests.post") as post:
            r = APIClient().post("/api/auth/otp/request/", {"phone": self.phone})
        self.assertEqual(r.status_code, 503)
        post.assert_not_called()


class GoogleLoginTests(APITestCase):
    """Google token verification is always mocked here: no network call is ever made from the test suite."""

    url = "/api/auth/google/"

    def setUp(self):
        cache.clear()

    def _claims(self, **over):
        claims = {"iss": "accounts.google.com", "aud": "client-id", "sub": "1111", "email": "Asha@Example.com",
                  "email_verified": True, "name": "Asha Rao"}
        claims.update(over)
        return claims

    def _post(self, claims=None, exc=None, client=None):
        from unittest.mock import patch
        with patch("apps.accounts.google_auth.id_token.verify_oauth2_token", side_effect=exc, return_value=claims):
            return (client or self.client).post(self.url, {"credential": "tok"}, format="json")

    @override_settings(GOOGLE_CLIENT_ID="client-id")
    def test_first_sign_in_creates_account_and_session(self):
        r = self._post(self._claims())
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["is_new"])
        me = self.client.get("/api/me/").data
        self.assertEqual((me["email"], me["full_name"], me["phone"], me["has_google"]), ("asha@example.com", "Asha Rao", "", True))
        self.assertTrue(User.objects.get(google_sub="1111").profile)

    @override_settings(GOOGLE_CLIENT_ID="client-id")
    def test_second_sign_in_reuses_account(self):
        self._post(self._claims())
        self.client.post("/api/auth/logout/")
        r = self._post(self._claims())
        self.assertFalse(r.data["is_new"])
        self.assertEqual(User.objects.filter(google_sub="1111").count(), 1)

    @override_settings(GOOGLE_CLIENT_ID="client-id")
    def test_placeholder_phone_is_never_exposed(self):
        self._post(self._claims())
        self.assertTrue(User.objects.get(google_sub="1111").phone.startswith("g:"))
        self.assertEqual(self.client.get("/api/me/").data["phone"], "")

    @override_settings(GOOGLE_CLIENT_ID="client-id")
    def test_invalid_expired_or_wrong_audience_rejected(self):
        r = self._post(exc=ValueError("bad"))
        self.assertEqual(r.status_code, 400)
        self.assertEqual(self.client.get("/api/me/").status_code, 403)
        self.assertEqual(User.objects.count(), 0)

    @override_settings(GOOGLE_CLIENT_ID="client-id")
    def test_unverified_email_rejected(self):
        self.assertEqual(self._post(self._claims(email_verified=False)).status_code, 400)
        self.assertEqual(self._post(self._claims(email="")).status_code, 400)
        self.assertEqual(User.objects.count(), 0)

    @override_settings(GOOGLE_CLIENT_ID="")
    def test_not_configured_rejected_without_verifying(self):
        from unittest.mock import patch
        with patch("apps.accounts.google_auth.id_token.verify_oauth2_token") as verify:
            r = self.client.post(self.url, {"credential": "tok"}, format="json")
        self.assertEqual(r.status_code, 400)
        verify.assert_not_called()

    @override_settings(GOOGLE_CLIENT_ID="client-id")
    def test_customer_is_not_matched_by_email_alone(self):
        victim = User.objects.create_user("+919876543210", email="asha@example.com", full_name="Typed email")
        r = self._post(self._claims())
        self.assertTrue(r.data["is_new"])
        self.assertNotEqual(r.data["user"]["id"], str(victim.id))
        victim.refresh_from_db()
        self.assertIsNone(victim.google_sub)

    @override_settings(GOOGLE_CLIENT_ID="client-id")
    def test_staff_account_provisioned_by_email_is_claimed(self):
        from django.core.management import call_command
        call_command("make_staff", "asha@example.com", "--role", "admin", stdout=__import__("io").StringIO())
        staff = User.objects.get(email="asha@example.com")
        self.assertEqual((staff.role, staff.is_staff, staff.google_sub), ("admin", True, None))
        r = self._post(self._claims())
        self.assertFalse(r.data["is_new"])
        self.assertEqual((r.data["user"]["id"], r.data["user"]["role"]), (str(staff.id), "admin"))
        staff.refresh_from_db()
        self.assertEqual(staff.google_sub, "1111")
        # a different Google account with the same email can no longer claim it
        other = self._post(self._claims(sub="2222"), client=APIClient())
        self.assertTrue(other.data["is_new"])
        self.assertEqual(other.data["user"]["role"], "customer")

    @override_settings(GOOGLE_CLIENT_ID="client-id")
    def test_guest_data_is_merged_into_google_account(self):
        from apps.trips.models import Trip
        guest = self.client.post("/api/auth/guest/").data
        trip = Trip.objects.create(user=User.objects.get(pk=guest["id"]), title="Goa", destinations=["Goa"])
        r = self._post(self._claims())
        self.assertEqual(r.status_code, 200)
        self.assertEqual(Trip.objects.get(pk=trip.pk).user_id, User.objects.get(google_sub="1111").pk)
        self.assertFalse(User.objects.get(pk=guest["id"]).is_active)

    @override_settings(GOOGLE_CLIENT_ID="client-id")
    def test_deleted_account_frees_the_google_identity(self):
        self._post(self._claims())
        self.assertEqual(self.client.delete("/api/me/").status_code, 204)
        r = self._post(self._claims())
        self.assertTrue(r.data["is_new"])

    @override_settings(GOOGLE_CLIENT_ID="client-id")
    def test_deactivated_account_cannot_sign_in(self):
        self._post(self._claims())
        self.client.post("/api/auth/logout/")
        User.objects.filter(google_sub="1111").update(is_active=False)
        self.assertEqual(self._post(self._claims()).status_code, 400)
        self.assertEqual(self.client.get("/api/me/").status_code, 403)


class PasswordLoginTests(APITestCase):
    """A Google sign-up can add a password, then log in with Google or with email + password."""

    login_url = "/api/auth/password/"
    set_url = "/api/auth/password/set/"
    password = "Sunrise-Trek-42"

    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user("g:abc123", google_sub="1111", email="asha@example.com", full_name="Asha Rao")
        from .models import CustomerProfile
        CustomerProfile.objects.create(user=self.user)

    def _login(self, email="asha@example.com", password=None, client=None):
        return (client or APIClient()).post(self.login_url, {"email": email, "password": password or self.password}, format="json")

    def _set_password(self, **body):
        self.user.refresh_from_db()  # force_login stores the password hash in the session; it must be current
        self.client.force_login(self.user)
        return self.client.post(self.set_url, {"new_password": self.password, **body}, format="json")

    def test_google_user_sets_password_then_logs_in_with_email(self):
        self.assertFalse(self.client.force_login(self.user) or self.client.get("/api/me/").data["has_password"])
        r = self._set_password()
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.data["has_password"])
        self.assertEqual(self.client.get("/api/me/").status_code, 200)  # session survives the password change
        fresh = APIClient()
        r = self._login(email="Asha@Example.com", client=fresh)  # email is case-insensitive
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.data["is_new"])
        self.assertEqual(fresh.get("/api/me/").data["email"], "asha@example.com")

    def test_wrong_password_and_unknown_email_get_the_same_error(self):
        self._set_password()
        wrong = self._login(password="Not-The-Password-1")
        unknown = self._login(email="nobody@example.com")
        self.assertEqual((wrong.status_code, unknown.status_code), (400, 400))
        self.assertEqual(wrong.data, unknown.data)

    def test_account_without_password_cannot_log_in(self):
        self.assertEqual(self._login().status_code, 400)

    def test_weak_password_rejected(self):
        for weak in ["short1", "12345678", "password"]:
            r = self._set_password(new_password=weak)
            self.assertEqual(r.status_code, 400)
            self.assertIn("new_password", r.data)
        self.assertFalse(User.objects.get(pk=self.user.pk).has_usable_password())

    def test_changing_password_needs_current_password(self):
        self._set_password()
        self.assertEqual(self._set_password(new_password="Another-Trek-77").status_code, 400)
        r = self._set_password(new_password="Another-Trek-77", current_password=self.password)
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self._login(password="Another-Trek-77").status_code, 200)
        self.assertEqual(self._login().status_code, 400)

    def test_typed_profile_email_cannot_be_used_to_log_in(self):
        victim = User.objects.create_user("+919876543210", email="typed@example.com")
        victim.set_password(self.password)
        victim.save()
        self.assertEqual(self._login(email="typed@example.com").status_code, 400)

    def test_phone_only_account_cannot_set_password(self):
        phone_user = User.objects.create_user("+919876543210", email="p@example.com")
        self.client.force_login(phone_user)
        r = self.client.post(self.set_url, {"new_password": self.password}, format="json")
        self.assertEqual(r.status_code, 400)

    def test_deactivated_account_cannot_log_in(self):
        self._set_password()
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        self.assertEqual(self._login().status_code, 400)

    def test_google_email_cannot_be_edited_in_profile(self):
        self.client.force_login(self.user)
        r = self.client.patch("/api/me/", {"email": "other@example.com", "full_name": "Asha R"}, format="json")
        self.assertEqual((r.data["email"], r.data["full_name"]), ("asha@example.com", "Asha R"))
