"""The report read path: how built report bytes reach a viewer's browser.

Three postures, chosen by ``TRELLUM_REPORT_ACCESS_MODEL``. Only the first is the
default, and it is the only one where the portal serves the bytes itself:

    proxy          The portal serves report content through its own views,
                   behind its own permission check. The bucket (if any) is
                   never exposed to a browser. Safe by construction — there is
                   no public surface to misconfigure. The single-VM and
                   self-host default.

    edge-signed    A CDN/edge worker in front of the bucket serves the bytes.
                   The portal runs its usual permission check when a viewer
                   opens a report and, on success, issues a short-lived signed
                   **grant** scoped to that report's content prefix. The edge
                   verifies the grant on every request and serves from the
                   bucket or refuses. This is the hosted (Cloudflare R2 +
                   Worker) posture: report bytes never touch the web tier.

    edge-external  The customer fronts the (public) bucket with THEIR OWN
                   identity layer — Azure AD / Entra, an identity-aware proxy.
                   The portal emits the content URLs and issues no grant of its
                   own; the external layer is what authenticates. A legitimate
                   clustered shape, but the portal is not the thing protecting
                   the bytes, so it says so loudly and refuses to pretend
                   otherwise (see ``probe_exposure``).

Why the split (edge-signed): at hosted scale, proxying every report byte
through a gunicorn worker is what falls over first — a handful of viewers
pulling multi-megabyte ``data.json`` files occupies the whole web tier.
Moving the bytes to the edge while keeping the decision here is the fix.

The grant (edge-signed): a single **cookie**, not a signed URL, because report
HTML references its assets by relative path — one cookie scoped to the report's
content prefix covers a whole build without rewriting a single link. The token
is an **Ed25519** signature over a tiny ``{scope, exp}`` payload. Asymmetric on
purpose: the portal holds the private key and is the only thing that can *mint*
a grant; the edge worker holds only the public key and can only *verify* one.
If the worker's environment ever leaked, no grant could be forged with it.

Content URLs are ``/content/{org}/{studio}/{slug}/builds/{build}/...`` —
mapping 1:1 onto bucket keys once the edge strips the leading ``/content/``.
Builds are immutable and the build id is in the URL, so the edge may cache
content forever; currency lives in the redirect this app issues, never in
anything cacheable.

The portal never imports an AWS/S3 client here (CI enforces the no-AWS-SDK
contract by grep): a grant is an Ed25519 signature from ``cryptography``, and
the edge worker verifies it with WebCrypto. See ``edge/report-access-worker.js``
for the verifier and ``install/configuration.md`` for the deployment.

Deliberately absent: a grant-refresh endpoint. The TTL is the revocation
latency, so it stays short; a report tab open past it re-auths on reload, which
is the trade we accept while a grant cannot be recalled from the edge.
"""
from __future__ import annotations

import base64
import json
import time

from django.conf import settings

#: The one grant cookie the edge worker reads.
GRANT_COOKIE = "trellum_grant"

MODEL_PROXY = "proxy"
MODEL_EDGE_SIGNED = "edge-signed"
MODEL_EDGE_EXTERNAL = "edge-external"
_EDGE_MODELS = (MODEL_EDGE_SIGNED, MODEL_EDGE_EXTERNAL)


def access_model() -> str:
    """Declared read-path posture. Unknown values read as the safe default."""
    model = (getattr(settings, "TRELLUM_REPORT_ACCESS_MODEL", "") or MODEL_PROXY).strip().lower()
    return model if model in (MODEL_PROXY, *_EDGE_MODELS) else MODEL_PROXY


def serves_from_edge() -> bool:
    """Whether report pages hand the viewer to the edge instead of serving.

    Requires an edge posture AND the remote store — there is no bucket to sit
    in front of on the local backend. Not the same as :func:`signs_grants`:
    ``edge-external``
    serves from the edge but issues no grant of its own.
    """
    from apps.core import storage

    return access_model() in _EDGE_MODELS and storage.is_remote()


def signs_grants() -> bool:
    """Whether this app mints Ed25519 grants (the ``edge-signed`` posture).

    Half-configured is off: a redirect to an edge that has no key to verify
    against is an outage by another name, so serving falls back to proxy until
    the signing material is present. (``probe_exposure`` is what makes that
    fallback *safe* to rely on rather than a silent hole.)
    """
    return (
        serves_from_edge()
        and access_model() == MODEL_EDGE_SIGNED
        and bool(
            getattr(settings, "TRELLUM_CDN_SIGNING_KEY", "")
            or getattr(settings, "TRELLUM_CDN_SIGNING_KEY_FILE", "")
        )
    )


# ── Ed25519 signing ──────────────────────────────────────────────────────────

_KEY_CACHE = None


def _reset_key_cache() -> None:
    """Tests swap keys between cases; nothing else should call this."""
    global _KEY_CACHE
    _KEY_CACHE = None


def _private_key():
    """The Ed25519 signing key, parsed once per process.

    A file path wins over the inline setting: hosted deployments mount secrets
    as files; the inline form exists for development and tests.
    """
    global _KEY_CACHE
    if _KEY_CACHE is not None:
        return _KEY_CACHE

    from cryptography.hazmat.primitives.serialization import load_pem_private_key

    path = getattr(settings, "TRELLUM_CDN_SIGNING_KEY_FILE", "")
    if path:
        with open(path, "rb") as f:
            pem = f.read()
    else:
        pem = getattr(settings, "TRELLUM_CDN_SIGNING_KEY", "").encode("utf-8")
    _KEY_CACHE = load_pem_private_key(pem, password=None)
    return _KEY_CACHE


def public_key_b64() -> str:
    """The raw Ed25519 public key, base64 — what the edge worker is configured
    with. Exposed so ``doctor`` can print it during setup."""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    raw = _private_key().public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(raw).decode("ascii")


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def sign_grant(scope: str, ttl: int | None = None) -> str:
    """A signed grant token for *scope*, valid for *ttl* seconds.

    Format: ``<b64url(payload)>.<b64url(signature)>`` where payload is compact
    JSON ``{"scope": ..., "exp": <epoch>}``. The signature covers the exact
    payload bytes, so the serialisation is part of the protocol — the edge
    worker re-encodes the same way before verifying.
    """
    if ttl is None:
        ttl = settings.TRELLUM_CDN_COOKIE_TTL_SECONDS
    payload = json.dumps(
        {"scope": scope, "exp": int(time.time()) + int(ttl)},
        separators=(",", ":"),
    ).encode("utf-8")
    signature = _private_key().sign(payload)
    return f"{_b64url(payload)}.{_b64url(signature)}"


# ── Paths and scope ──────────────────────────────────────────────────────────

def studio_prefix(studio) -> str:
    """The content path prefix one studio's grant covers.

    The trailing behaviour matters: a grant is scoped to this prefix *plus a
    slash* (see :func:`grant_scope`), so a studio ``casino`` can never unlock
    ``casino-secret``.
    """
    return f"/content/{studio.org.slug}/{studio.slug}"


def report_prefix(studio, slug: str) -> str:
    return f"{studio_prefix(studio)}/{slug}"


def grant_scope(studio, slug: str) -> str:
    """The report content prefix with a trailing slash."""
    return f"{report_prefix(studio, slug)}/"


def content_path(studio, slug: str, build: str, filename: str) -> str:
    """The immutable URL of one file of one build.

    Mirrors the bucket key ``{org}/{studio}/{slug}/builds/{build}/{file}``
    exactly (the edge strips ``/content/``), so nothing translates paths at the
    edge beyond that one prefix.
    """
    return f"{studio_prefix(studio)}/{slug}/builds/{build}/{filename}"


def attach_grant(response, request, studio, slug: str) -> None:
    """Set the grant cookie on *response*, scoped as tightly as it signs.

    ``Path`` limits where the browser *sends* it (the report prefix — it must
    never ride along to Django views); the signed scope is what limits what it
    *unlocks* at the edge. HttpOnly because no script has any business reading
    it; SameSite=Lax so top-level navigation to a report still carries it.

    A no-op unless this posture mints grants — ``edge-external`` relies on the
    customer's own layer and must not set a portal cookie the edge ignores.
    """
    if not signs_grants():
        return
    ttl = settings.TRELLUM_CDN_COOKIE_TTL_SECONDS
    response.set_cookie(
        GRANT_COOKIE,
        sign_grant(grant_scope(studio, slug), ttl),
        max_age=ttl,
        path=report_prefix(studio, slug),
        secure=request.is_secure(),
        httponly=True,
        samesite="Lax",
    )


# ── The exposure guard ───────────────────────────────────────────────────────

class ExposureError(RuntimeError):
    """The bucket/edge is serving report content to an anonymous request.

    Raised by :func:`probe_exposure`. In an edge posture this is the one
    failure that means real data is readable without authentication, so it is
    surfaced as a hard ``doctor`` failure and, at runtime, a refusal to emit
    content URLs.
    """


def probe_exposure(timeout: float = 5.0) -> str:
    """Prove a stranger cannot read report content. Returns a detail line or
    raises :class:`ExposureError` / ``RuntimeError``.

    An anonymous, credential-less request to the public content base must be
    *denied* — 401/403, or (``edge-external``) a redirect into the identity
    provider. A 200 means the bytes are readable by anyone, which is the exact
    accident this guard exists to catch. Provider-agnostic: it is an ordinary
    HTTP GET, so it holds for Cloudflare R2, S3+CloudFront, MinIO, or a
    self-hosted CDN alike.

    Only meaningful in an edge posture; the proxy posture exposes nothing and
    returns a note saying so.
    """
    from apps.core.report_access import selected_report_access_configuration_error

    compatibility_error = selected_report_access_configuration_error()
    if compatibility_error:
        raise RuntimeError(f"selected report access is unsafe: {compatibility_error}")

    model = access_model()
    if model == MODEL_PROXY:
        return "proxy (report content is served by the portal; nothing is exposed)"

    base = (getattr(settings, "TRELLUM_CDN_BASE_URL", "") or "").rstrip("/")
    if not base:
        raise RuntimeError(
            f"TRELLUM_REPORT_ACCESS_MODEL={model} but TRELLUM_CDN_BASE_URL is empty — "
            f"the portal cannot emit content URLs, nor prove they are protected."
        )

    import urllib.error
    import urllib.request

    # A path shaped like real content but under a reserved probe prefix, so it
    # never collides with a tenant and a well-formed edge treats it like any
    # other content request (deny without a grant).
    url = f"{base}/content/_probe/_probe/probe.txt"
    req = urllib.request.Request(url, method="GET")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 - fixed https base
        status = resp.getcode()
    except urllib.error.HTTPError as exc:
        status = exc.code
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"could not reach {base} to check exposure: {exc.reason}. "
            f"Report serving is refused until this can be verified."
        ) from exc

    if status in (401, 403):
        return f"protected — anonymous request denied ({status})"
    if model == MODEL_EDGE_EXTERNAL and 300 <= status < 400:
        return f"protected — anonymous request redirected to auth ({status})"
    raise ExposureError(
        f"report content at {base}/content/ answered an anonymous request with "
        f"HTTP {status} — the bucket/edge is NOT protected and report data is "
        f"publicly readable. Refusing to serve. Seal the bucket (edge-signed) "
        f"or put your identity layer in front of it (edge-external)."
    )


#: Cache the last probe verdict so the runtime refusal below is not an outbound
#: request per report view. (fetched_at, ok, detail)
_PROBE_TTL_SECONDS = 300.0
_probe_cache: tuple[float, bool, str] | None = None


def exposure_ok() -> tuple[bool, str]:
    """Cached exposure verdict for the request path: (ok, detail).

    ``ok`` is False only when the probe positively found content readable
    without auth, or could not verify it — either way the caller must refuse to
    emit content URLs rather than risk leaking. Proxy posture is always ok.
    """
    global _probe_cache
    if access_model() == MODEL_PROXY:
        return True, "proxy"
    now = time.monotonic()
    if _probe_cache is not None and now - _probe_cache[0] < _PROBE_TTL_SECONDS:
        return _probe_cache[1], _probe_cache[2]
    try:
        detail = probe_exposure()
        _probe_cache = (now, True, detail)
    except Exception as exc:  # noqa: BLE001 - any failure means "do not serve"
        _probe_cache = (now, False, str(exc))
    return _probe_cache[1], _probe_cache[2]


def _reset_probe_cache() -> None:
    global _probe_cache
    _probe_cache = None
