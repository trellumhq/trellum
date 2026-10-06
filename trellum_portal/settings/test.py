"""Settings for the automated test suite (pytest-django)."""
from .base import *  # noqa: F401,F403

DEBUG = False
SECRET_KEY = "test-secret-key"
SECRET_ENCRYPTION_KEY = "5oyYd0zpeq5F1zqLBTXCzCUJ9WQ0P84qBcNPXuMUsUE="
ALLOWED_HOSTS = ["*"]

# Fast password hashing in tests.
PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

# Plain static storage: no manifest needed in tests.
STORAGES["staticfiles"] = {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"}  # noqa: F405
WHITENOISE_AUTOREFRESH = True  # don't require a collectstatic run for tests

# Tests write studio trees into a throwaway dir (overridden per-test via
# tmp_path fixtures where it matters).
DATA_DIR = BASE_DIR / ".data-test"  # noqa: F405

# The suite stubs subprocess.Popen and never has a Docker daemon; exercise the
# in-process spawn path. Docker-mode behaviour is covered with a fake client in
# test_sandbox.py and by the docker_sandbox-marked integration smoke in CI.
TRELLUM_SANDBOX = "off"
