"""Minimal Django settings for the test suite."""

SECRET_KEY = "test-only"
DEBUG = True
ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "langsys_django",
]

MIDDLEWARE = [
    "langsys_django.middleware.LangsysMiddleware",
]

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {},
    }
]

DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}}

USE_TZ = True

ROOT_URLCONF = "tests.urls"

# Points the SDK at a fake host; tests mock the HTTP layer with pytest-httpx.
LANGSYS = {
    "API_KEY": "test-key",
    "PROJECT_ID": "proj-1",
    "API_URL": "https://api.test/api",
    "BASE_LOCALE": "en-US",
    "SUPPORTED": ["en-US", "es-ES", "es-CR"],
}
