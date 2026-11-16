"""Sign in with Google: verify a Google ID token and find or create the matching WoDin user.

The browser (Google Identity Services) obtains a signed ID token for our OAuth client; the backend only trusts that
token after google-auth has checked signature, expiry, issuer and audience (= GOOGLE_CLIENT_ID). No client secret
is needed for this flow.
"""
import logging
import uuid

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import Q
from google.auth import exceptions as google_exceptions
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token

from .models import Role, User

log = logging.getLogger(__name__)


class GoogleAuthError(Exception):
    """The credential is missing, invalid, expired, or has an unverified email (never includes the token)."""


def verify_google_token(credential: str) -> dict:
    """Return {"sub", "email", "name"} from a verified Google ID token, or raise GoogleAuthError."""
    client_id = settings.GOOGLE_CLIENT_ID
    if not client_id:
        log.error("GOOGLE_CLIENT_ID is empty: set it in backend/.env and restart the backend.")
        raise GoogleAuthError("Google sign-in is not configured.")
    try:
        claims = id_token.verify_oauth2_token(credential, google_requests.Request(), client_id,
                                              clock_skew_in_seconds=10)
    except (ValueError, google_exceptions.GoogleAuthError) as exc:
        log.warning("Google token rejected: %s", exc.__class__.__name__)
        raise GoogleAuthError("Invalid Google token.") from exc
    sub, email = claims.get("sub"), (claims.get("email") or "").strip().lower()
    if not sub or not email or claims.get("email_verified") is not True:
        raise GoogleAuthError("Google account has no verified email.")
    return {"sub": sub, "email": email, "name": (claims.get("name") or "").strip()}


def _placeholder_phone() -> str:
    return f"g:{uuid.uuid4().hex[:14]}"  # 16 chars, fits User.phone


def get_or_create_google_user(info: dict) -> tuple[User, bool]:
    """Match by Google `sub`; otherwise claim a staff account pre-provisioned for this email; otherwise sign up.

    Customers are never matched by email alone: an email typed into a profile is unverified, so linking by it
    could hand one person's account to another.
    """
    user = User.objects.filter(google_sub=info["sub"]).first()
    if user:
        return user, False
    staff = (User.objects.filter(email__iexact=info["email"], google_sub__isnull=True, is_guest=False, is_active=True)
             .exclude(role=Role.CUSTOMER).first())
    if staff:
        staff.google_sub = info["sub"]
        if not staff.full_name:
            staff.full_name = info["name"][:120]
        staff.save(update_fields=["google_sub", "full_name"])
        return staff, False
    try:
        with transaction.atomic():  # savepoint: a concurrent first sign-in must not poison the caller's transaction
            user = User.objects.create_user(_placeholder_phone(), google_sub=info["sub"], email=info["email"],
                                            full_name=info["name"][:120])
    except IntegrityError:
        return User.objects.get(google_sub=info["sub"]), False
    return user, True


def provision_staff(email: str, role: str, full_name: str = "") -> tuple[User, bool]:
    """Create (or update) a staff account that the owner of `email` claims by signing in with Google."""
    email = email.strip().lower()
    existing = (User.objects.filter(email__iexact=email, is_guest=False, is_active=True)
                .filter(Q(google_sub__isnull=False) | ~Q(role=Role.CUSTOMER)).first())
    created = existing is None
    user = existing or User.objects.create_user(_placeholder_phone(), email=email)
    user.role, user.is_staff = role, role == Role.ADMIN
    if full_name:
        user.full_name = full_name[:120]
    user.save()
    return user, created
