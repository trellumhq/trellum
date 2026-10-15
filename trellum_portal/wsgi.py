"""WSGI entry point (gunicorn)."""
import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trellum_portal.settings.prod")

application = get_wsgi_application()

from apps.core.workspace import initialize_workspace_identity

initialize_workspace_identity()
