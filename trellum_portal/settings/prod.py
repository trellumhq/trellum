"""Production settings: everything must come from the environment."""
from .base import *  # noqa: F401,F403

DEBUG = False

if not SECRET_KEY:  # noqa: F405
    raise RuntimeError("SESSION_SECRET_KEY must be set in production")
if not SECRET_ENCRYPTION_KEY:  # noqa: F405
    raise RuntimeError("SECRET_ENCRYPTION_KEY must be set in production")

# Behind the compose network the portal terminates plain HTTP; TLS is the
# reverse proxy's job. Only trust the standard proxy header for scheme.
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
CSRF_TRUSTED_ORIGINS = [PORTAL_BASE_URL]  # noqa: F405

_HTTPS = PORTAL_BASE_URL.startswith("https://")  # noqa: F405

# Derived from the base URL by default, but overridable: an operator who knows
# TLS terminates in front should be able to say so, and one who is genuinely on
# plain HTTP internally should not be silently downgraded without being able to
# see why. Previously this was a string prefix check with no escape hatch, so a
# mistyped PORTAL_BASE_URL shipped session cookies in the clear.
SESSION_COOKIE_SECURE = env.bool("SESSION_COOKIE_SECURE", default=_HTTPS)  # noqa: F405
CSRF_COOKIE_SECURE = env.bool("CSRF_COOKIE_SECURE", default=_HTTPS)  # noqa: F405

SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"

# Referrer: full URLs here contain org and studio slugs, and report paths
# describe what a customer measures. Same-origin keeps that out of the Referer
# header on any outbound link a report happens to contain.
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"

# HSTS: off unless the deployment is actually on HTTPS, because sending it over
# plain HTTP is meaningless, and sending it from a host that later has to serve
# HTTP locks that host out of browsers for the duration. Default one year.
# Subdomains and preload stay opt-in: the portal may share a parent domain with
# hosts we know nothing about, and preload is close to irreversible.
SECURE_HSTS_SECONDS = env.int("SECURE_HSTS_SECONDS", default=31536000 if _HTTPS else 0)  # noqa: F405
SECURE_HSTS_INCLUDE_SUBDOMAINS = env.bool("SECURE_HSTS_INCLUDE_SUBDOMAINS", default=False)  # noqa: F405
SECURE_HSTS_PRELOAD = env.bool("SECURE_HSTS_PRELOAD", default=False)  # noqa: F405

# Redirecting to HTTPS is the proxy's job in the documented deployment, and
# doing it here as well breaks the compose healthcheck, which hits
# http://127.0.0.1:8050/healthz inside the container. Available for anyone whose
# proxy does not do it.
SECURE_SSL_REDIRECT = env.bool("SECURE_SSL_REDIRECT", default=False)  # noqa: F405
SECURE_REDIRECT_EXEMPT = [r"^healthz$"]

# SESSION_COOKIE_AGE (12h default) is set in base.py now -- every environment
# gets it, not just prod. What stays prod-only here is SESSION_SAVE_EVERY_REQUEST:
# with it on, the DB session row's expire_date is refreshed on every request, so
# SESSION_COOKIE_AGE behaves as an idle window there too and mirrors whatever
# apps.accounts.session_policy's DB-configured idle timeout currently says.
# The actual idle/absolute/revocation enforcement is SessionSecurityMiddleware's
# job (per-request, DB-configured, retroactive) -- this setting is only the
# outer bound for a session that middleware never gets another request from.
SESSION_SAVE_EVERY_REQUEST = True
