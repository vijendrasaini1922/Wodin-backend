"""WoDin Django settings. All secrets/config come from environment variables (see ../.env.example)."""
import os
from pathlib import Path

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in ("1", "true", "yes", "on")


DEBUG = env_bool("DJANGO_DEBUG", True)
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY", "")
if not SECRET_KEY:
    if not DEBUG:
        raise RuntimeError("DJANGO_SECRET_KEY must be set when DEBUG is false")
    SECRET_KEY = "dev-insecure-key-do-not-use-in-production"

ALLOWED_HOSTS = [h.strip() for h in os.getenv("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h.strip()]
FRONTEND_ORIGIN = os.getenv("FRONTEND_ORIGIN", "http://localhost:3000")
BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000")  # used to build signed local upload/download URLs

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "corsheaders",
    "drf_spectacular",
    "apps.core",
    "apps.accounts",
    "apps.catalog",
    "apps.trips",
    "apps.photos",
    "apps.projects",
    "apps.orders",
    "apps.payments",
    "apps.production",
    "apps.shipping",
    "apps.reviews",
    "apps.notifications",
    "apps.support",
    "apps.adminapi",
    "apps.invoices",
    "apps.aidraft",
    "apps.aibook",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

AUTH_USER_MODEL = "accounts.User"

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# Database: SQLite in dev, PostgreSQL via DATABASE_URL in production.
DATABASES = {
    "default": dj_database_url.config(
        default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}", conn_max_age=60
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-in"
TIME_ZONE = "Asia/Kolkata"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
# Private storage root for the local storage backend (never served publicly).
PRIVATE_MEDIA_ROOT = BASE_DIR / "media"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---- Sessions / cookies ----
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SESSION_COOKIE_AGE = 60 * 60 * 24 * 30
CSRF_COOKIE_SAMESITE = "Lax"
CSRF_TRUSTED_ORIGINS = [FRONTEND_ORIGIN]
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
if not DEBUG:
    SECURE_SSL_REDIRECT = env_bool("SECURE_SSL_REDIRECT", True)
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = env_bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", False)  # enable once ALL subdomains are HTTPS-only
    SECURE_HSTS_PRELOAD = env_bool("SECURE_HSTS_PRELOAD", False)
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# ---- CORS ----
CORS_ALLOWED_ORIGINS = [FRONTEND_ORIGIN]
CORS_ALLOW_CREDENTIALS = True

# ---- DRF ----
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        # Dev (DEBUG) pages fire many requests from one IP (HMR, Strict Mode), so limits are relaxed there.
        "anon": os.getenv("ANON_RATE", "5000/min" if DEBUG else "120/min"),
        "user": os.getenv("USER_RATE", "5000/min" if DEBUG else "600/min"),
        "otp_request": os.getenv("OTP_REQUEST_RATE", "200/hour" if DEBUG else "5/hour"),
        "otp_verify": os.getenv("OTP_VERIFY_RATE", "200/hour" if DEBUG else "20/hour"),
        "contact": "10/hour",
    },
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 50,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "WoDin API",
    "DESCRIPTION": "Backend API for the WoDin travel photobook website.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
}

# ---- Background jobs (Celery). Without REDIS_URL tasks run inline. ----
REDIS_URL = os.getenv("REDIS_URL", "")
CELERY_BROKER_URL = REDIS_URL or "memory://"
CELERY_TASK_ALWAYS_EAGER = not REDIS_URL

# ---- Seller details printed on invoices (set the real legal details) ----
SELLER_NAME = os.getenv("SELLER_NAME", "WoDin")
SELLER_ADDRESS = os.getenv("SELLER_ADDRESS", "")
SELLER_GSTIN = os.getenv("SELLER_GSTIN", "")

# ---- Email ----
EMAIL_BACKEND = os.getenv("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "WoDin <no-reply@wodin.example>")

# ---- Pluggable providers ----
OTP_PROVIDER = os.getenv("OTP_PROVIDER", "console")
MSG91_AUTH_KEY = os.getenv("MSG91_AUTH_KEY", "")
MSG91_TEMPLATE_ID = os.getenv("MSG91_TEMPLATE_ID", "")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash")
AI_PROVIDER = os.getenv("AI_PROVIDER", "gemini" if GEMINI_API_KEY else "mock")  # gemini | mock (DEBUG only)
AI_IMAGE_EDGE = int(os.getenv("AI_IMAGE_EDGE", "1024"))  # px sent to the AI (long side)
AI_CHUNK_SIZE = int(os.getenv("AI_CHUNK_SIZE", "40"))  # photos per AI request (keeps requests small)
AI_MAX_PHOTOS = int(os.getenv("AI_MAX_PHOTOS", "300"))  # hard cap per draft: protects cost
AI_MAX_RUNS_PER_PROJECT = int(os.getenv("AI_MAX_RUNS_PER_PROJECT", "5"))
# ---- AI Book mode (prompt + photos -> borderless PDF). Separate from orders; limits protect cost. ----
AIBOOK_MAX_PHOTOS = int(os.getenv("AIBOOK_MAX_PHOTOS", "150"))
AIBOOK_MAX_RUNS_PER_DAY = int(os.getenv("AIBOOK_MAX_RUNS_PER_DAY", "12"))  # AI calls per user per 24 h (a book uses 1-2)
AIBOOK_MAX_GENERATIONS_PER_BOOK = int(os.getenv("AIBOOK_MAX_GENERATIONS_PER_BOOK", "4"))
AIBOOK_PDF_DPI = int(os.getenv("AIBOOK_PDF_DPI", "200"))  # 300 is print quality but makes much larger files
AIBOOK_BLEED_MM = float(os.getenv("AIBOOK_BLEED_MM", "3"))
AIBOOK_MAX_QUESTIONS = 3
AIBOOK_INLINE = False  # tests set True: run jobs inline. Otherwise Celery if REDIS_URL is set, else a background thread.
PAYMENT_PROVIDER = os.getenv("PAYMENT_PROVIDER", "mock")
RAZORPAY_KEY_ID = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")
SHIPPING_PROVIDER = os.getenv("SHIPPING_PROVIDER", "manual")
STORAGE_BACKEND = os.getenv("STORAGE_BACKEND", "local")
S3_BUCKET = os.getenv("S3_BUCKET", "")
S3_ENDPOINT_URL = os.getenv("S3_ENDPOINT_URL", "")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "")

# ---- Business rules ----
OTP_LENGTH = 6
OTP_TTL_SECONDS = 300
OTP_RESEND_COOLDOWN_SECONDS = 30
OTP_MAX_ATTEMPTS = 5
UPLOAD_URL_TTL_SECONDS = 900
MAX_UPLOAD_BYTES = 40 * 1024 * 1024
MIN_PHOTO_PIXELS = 1200  # shorter side below this is flagged low-resolution
REJECT_PHOTO_PIXELS = 300  # shorter side below this is rejected
BLUR_THRESHOLD = 40.0  # Laplacian variance below this is flagged as possibly blurry
NEAR_DUPLICATE_DISTANCE = 4  # max dHash Hamming distance
MAX_PHOTOS_PER_TRIP = 500
DOWNLOAD_URL_TTL_SECONDS = 600
DATA_UPLOAD_MAX_MEMORY_SIZE = MAX_UPLOAD_BYTES + 1024 * 1024
