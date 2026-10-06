"""Build-time settings for the static Trellum website."""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent

SECRET_KEY = "static-build-only"
DEBUG = False
ALLOWED_HOSTS = ["*"]

SITE_BRAND = "trellum"
SITE_TAGLINE = "Reports as code you can trust."
SITE_BASE_URL = os.environ.get("SITE_BASE_URL", "https://trellum.dev").rstrip("/")
GITHUB_URL = "https://github.com/trellumhq/trellum"
FRAMEWORK_REPO_URL = GITHUB_URL
WEBSITE_REPO_URL = GITHUB_URL
DEMO_URL = "/demo/"
CONTACT_EMAIL = "hello@trellum.dev"

DOCS_ROOT = BASE_DIR / "content" / "docs"
DOCS_DEFAULT_VERSION = "latest"
BLOG_ROOT = BASE_DIR / "content" / "blog"
DOCS_CACHE = True

INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "apps.pages",
    "apps.docs",
    "apps.blog",
]
MIDDLEWARE = []
ROOT_URLCONF = "trellum_website.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "apps.pages.context_processors.brand",
            ],
        },
    },
]
DATABASES = {}
STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
}
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = False
USE_TZ = True
