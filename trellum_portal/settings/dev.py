"""Local development settings."""
from .base import *  # noqa: F401,F403

DEBUG = True
SECRET_KEY = SECRET_KEY or "dev-only-insecure-secret-key"  # noqa: F405
ALLOWED_HOSTS = ["*"]

# A fixed dev encryption key so the DB survives restarts. Never use in prod.
SECRET_ENCRYPTION_KEY = SECRET_ENCRYPTION_KEY or "5oyYd0zpeq5F1zqLBTXCzCUJ9WQ0P84qBcNPXuMUsUE="  # noqa: F405

STORAGES["staticfiles"] = {"BACKEND": "whitenoise.storage.CompressedStaticFilesStorage"}  # noqa: F405

# Local dev has no Docker-in-Docker: run report builds as an in-process
# subprocess. Override with TRELLUM_SANDBOX=docker to exercise the real sandbox.
TRELLUM_SANDBOX = env("TRELLUM_SANDBOX", default="off")  # noqa: F405
