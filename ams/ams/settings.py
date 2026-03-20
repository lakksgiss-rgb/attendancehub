"""Django settings for the attendance project."""

import os
import socket
from pathlib import Path

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent


# Quick-start development settings - unsuitable for production
# See https://docs.djangoproject.com/en/4.2/howto/deployment/checklist/

# SECURITY WARNING: keep the secret key used in production secret!
SECRET_KEY = "django-insecure-xrnc-#jvo8a9_jd@qz5gv-p%tsh)6_+t41=pij7ra6ctx!0gts"

# SECURITY WARNING: don't run with debug turned on in production!
DEBUG = True


def _env_list(name):
    raw_value = os.getenv(name, "")
    return [item.strip() for item in raw_value.split(",") if item.strip()]


def _discover_local_hosts():
    hosts = {"127.0.0.1", "localhost", "[::1]"}

    for candidate in {socket.gethostname(), socket.getfqdn()}:
        if candidate:
            hosts.add(candidate)

    try:
        hosts.update(ip for ip in socket.gethostbyname_ex(socket.gethostname())[2] if ip)
    except OSError:
        pass

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            hosts.add(sock.getsockname()[0])
    except OSError:
        pass

    return sorted(hosts)


BACKEND_PORT = os.getenv("BACKEND_PORT", "8000")
NETWORK_HOSTS = _discover_local_hosts()
EXTRA_ALLOWED_HOSTS = _env_list("DJANGO_ALLOWED_HOSTS")
ALLOWED_HOSTS = sorted(set(NETWORK_HOSTS + EXTRA_ALLOWED_HOSTS))


def _build_trusted_origins():
    origins = set()
    for host in ALLOWED_HOSTS:
        if host == "[::1]":
            continue
        origins.add(f"http://{host}:{BACKEND_PORT}")

    origins.update(_env_list("DJANGO_EXTRA_TRUSTED_ORIGINS"))
    origins.add("https://*.ngrok-free.app")
    origins.add("https://*.ngrok-free.dev")
    return sorted(origins)


CSRF_TRUSTED_ORIGINS = _build_trusted_origins()
CORS_ALLOWED_ORIGINS = CSRF_TRUSTED_ORIGINS.copy()
CORS_ALLOWED_ORIGIN_REGEXES = [
    r"^https://[a-z0-9-]+\.ngrok-free\.app$",
    r"^https://[a-z0-9-]+\.ngrok-free\.dev$",
]
CORS_ALLOW_CREDENTIALS = True


# Application definition

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "corsheaders",

    # Attendance management
    "ams.attendance",
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

ROOT_URLCONF = "ams.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
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

WSGI_APPLICATION = "ams.wsgi.application"


# Database
# https://docs.djangoproject.com/en/4.2/ref/settings/#databases

# Production: please set DATABASE_URL environment variable, e.g.
# postgres://user:password@host:port/dbname
# Otherwise use default local SQLite for quick local dev.
DATABASE_URL = os.getenv("DATABASE_URL", "")

if DATABASE_URL:
    try:
        import dj_database_url

        DATABASES = {
            "default": dj_database_url.parse(DATABASE_URL, conn_max_age=600, ssl_require=True)
        }
    except ImportError:
        raise ImportError(
            "dj-database-url is required for DATABASE_URL support. Install it with `pip install dj-database-url`."
        )
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }


# Password validation
# https://docs.djangoproject.com/en/4.2/ref/settings/#auth-password-validators

AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]


# Internationalization
# https://docs.djangoproject.com/en/4.2/topics/i18n/

LANGUAGE_CODE = "en-us"

TIME_ZONE = "UTC"

USE_I18N = True

USE_TZ = True


# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/4.2/howto/static-files/

STATIC_URL = "static/"

# Media files
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# Default primary key field type
# https://docs.djangoproject.com/en/4.2/ref/settings/#default-auto-field

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
