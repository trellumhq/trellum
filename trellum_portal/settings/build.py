"""Image-build settings: used ONLY for `collectstatic` in the Dockerfile.

Inherits the production storage config (manifest + compression) so the
manifest that runtime prod settings expect actually gets generated, with
dummy secrets because no real configuration exists at build time.
"""
from .base import *  # noqa: F401,F403

SECRET_KEY = "build-time-only-never-used-at-runtime"
SECRET_ENCRYPTION_KEY = "build-time-only"
