"""
Django settings for the SEM Trading Floor project.

Phase 1 (foundation): configured for PostgreSQL via a DATABASE_URL environment
variable. Real-time delivery is round-based + polling for now.

LIVE-TICKER SPRINT (later): switch the delivery layer to WebSockets with Django
Channels. When we do that, only a few things change here:
  - add "channels" to INSTALLED_APPS,
  - set ASGI_APPLICATION to a Channels ProtocolTypeRouter (already pointed at
    semfloor.asgi below), and
  - add a CHANNEL_LAYERS setting backed by Redis.
The game rules (game/engine) and the state snapshot (game/state.py) do NOT change.
"""
from pathlib import Path
import os
import sys

import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load environment variables from a local .env file if present (dev convenience).
load_dotenv(BASE_DIR / ".env")

SECRET_KEY = os.environ.get("SECRET_KEY", "dev-insecure-change-me")
DEBUG = os.environ.get("DEBUG", "True").lower() in ("1", "true", "yes")
ALLOWED_HOSTS = [h.strip() for h in os.environ.get("ALLOWED_HOSTS", "127.0.0.1,localhost").split(",") if h.strip()]
# Required for cross-origin POSTs over HTTPS (your deployed domain), e.g.
# CSRF_TRUSTED_ORIGINS=https://your-app.onrender.com
CSRF_TRUSTED_ORIGINS = [o.strip() for o in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",") if o.strip()]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "game",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # WhiteNoise serves static files in production without a separate web server.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "semfloor.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        # Project-level templates dir is created in Phase 2 (the three surfaces).
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "semfloor.wsgi.application"
# Pointed at ASGI already so the live-ticker sprint can add Channels without a rename.
ASGI_APPLICATION = "semfloor.asgi.application"

# --- Database: PostgreSQL via DATABASE_URL ---
# conn_max_age keeps connections warm; require Postgres in all environments so dev
# matches production (no SQLite drift).
DATABASES = {
    "default": dj_database_url.config(
        default=os.environ.get("DATABASE_URL", "postgres://semfloor:semfloor@127.0.0.1:5432/semfloor"),
        conn_max_age=600,
    )
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
# collectstatic gathers files here for production; WhiteNoise serves them (compressed,
# hashed). Run `python manage.py collectstatic` on deploy.
STATIC_ROOT = BASE_DIR / "staticfiles"
# Hashed + compressed static files (WhiteNoise) only in production. In development and
# under the test runner (which forces DEBUG=False) we use plain storage so nothing
# depends on `collectstatic` having run first.
_TESTING = "test" in sys.argv
_static_backend = (
    "django.contrib.staticfiles.storage.StaticFilesStorage"
    if (DEBUG or _TESTING)
    else "semfloor.storage.WhiteNoiseStaticStorage"
)
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": _static_backend},
}

# Facilitator pages require a logged-in staff user; send them to the admin login.
LOGIN_URL = "/admin/login/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Security: relaxed in dev, hardened once DEBUG is off (i.e. in production) ---
if not DEBUG:
    # Trust the proxy's X-Forwarded-Proto so Django knows requests are HTTPS.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_CONTENT_TYPE_NOSNIFF = True
