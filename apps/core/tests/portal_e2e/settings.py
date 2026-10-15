"""Disposable live-browser settings; never used by deployment settings."""
import os
from pathlib import Path
from trellum_portal.settings.test import *  # noqa: F403

DATA_DIR = Path(os.environ['PORTAL_E2E_DATA_DIR'])
STATIC_ROOT = Path(os.environ.get('PORTAL_E2E_STATIC_ROOT', DATA_DIR / 'static'))
EMAIL_BACKEND = 'django.core.mail.backends.locmem.EmailBackend'
MIDDLEWARE = ['apps.core.tests.portal_e2e.instrumentation.MeasureRequests', *MIDDLEWARE]  # noqa: F405
PASSWORD_HASHERS = ['django.contrib.auth.hashers.PBKDF2PasswordHasher']

TRELLUM_REPORT_SCOPED_ACCESS_READY = True

TRELLUM_IMPERSONATION_ENABLED = True
