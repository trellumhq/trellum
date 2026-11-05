"""WSGI entry point (gunicorn)."""
import os

from django.core.wsgi import get_wsgi_application
from django.conf import settings

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trellum_portal.settings.prod")

application = get_wsgi_application()

from apps.studios.startup import check_studio_files_at_startup  # noqa: E402

if getattr(settings, "CHECK_STUDIO_FILES_AT_STARTUP", True):
    check_studio_files_at_startup()
