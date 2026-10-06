"""Where this instance sends someone who needs the documentation.

Two problems with the hard-coded ``https://trellum.dev/docs/latest/...`` links
this replaces, and the second is the serious one:

1. **``latest`` is the wrong version.** A customer running 0.1.0 who clicks
   "how do I upgrade" should not be reading instructions written for 0.9. The
   documentation is versioned on the website precisely so that it can match,
   and every link was throwing that away. So links carry *this instance's*
   version, and the site resolves an unknown one back to ``latest`` itself.

2. **Some installs have no internet at all.** An air-gapped customer gets the
   documentation as files in their bundle (``scripts/airgap_bundle.sh``), and
   a link to trellum.dev is worse than useless there -- it looks like the
   product is broken. ``TRELLUM_DOCS_BASE_URL`` lets that operator point every
   link in the product at wherever they actually put those files, without
   patching templates.

Deliberately NOT solved here: the portal does not render documentation itself.
That would mean a markdown pipeline, a nav, and a second theme to keep in
step, to serve pages the customer already has as files. If it ever becomes
worth it, this module is the seam -- everything already asks it for a URL.

Usage::

    from apps.core.docs import docs_url
    docs_url("install/docker-compose/")

or in a template::

    {% load docs %}
    <a href="{% docs_url 'install/docker-compose/' %}">Installing</a>
"""
from __future__ import annotations

from django.conf import settings

#: Where the published documentation lives when nobody has said otherwise.
DEFAULT_BASE_URL = "https://trellum.dev/docs"


def docs_base_url() -> str:
    """The root every documentation link is built from, without a trailing /."""
    configured = (getattr(settings, "TRELLUM_DOCS_BASE_URL", "") or "").strip()
    return (configured or DEFAULT_BASE_URL).rstrip("/")


def docs_version() -> str:
    """Which version's documentation this instance should point at.

    The release tag, so the pages describe what is actually running. An
    operator who has published only one copy of the documentation can set
    ``TRELLUM_DOCS_VERSION=latest`` and stop thinking about it.
    """
    override = (getattr(settings, "TRELLUM_DOCS_VERSION", "") or "").strip()
    if override:
        return override
    from apps.core.version import framework_tag

    return framework_tag()


def docs_url(path: str = "") -> str:
    """A documentation URL for this instance.

    ``path`` is relative to the version root, e.g. ``install/upgrades/``.

    If ``TRELLUM_DOCS_BASE_URL`` is set, it is used verbatim as the root and
    the version is still appended -- an operator mirroring the docs keeps the
    same shape as the public site, so a bundle can be served by any static
    file server without rewriting anything.
    """
    joined = "/".join(
        part for part in (docs_base_url(), docs_version(), path.strip("/")) if part
    )
    # Preserve the caller's trailing slash: the docs site serves directory-style
    # URLs and a missing slash costs a redirect on every link in the product.
    return joined + "/" if path.endswith("/") or not path else joined
