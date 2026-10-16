"""Serve the reviewed, identity-independent report scripts without a session.

Built reports retain these stable URLs across portal upgrades. Like ordinary
static files, reading their public bytes must not renew a login or consult the
database. Protected API calls made by those scripts use the normal middleware.

A view opts in with ``@public_asset``. That one marker is the whole list: this
middleware serves marked views without the session chain, and the
Content-Security-Policy middleware leaves their responses alone. Nothing is
looked up by URL name, so a route cannot be made public by naming it like one,
and adding an asset is a decorator on its view rather than an edit in three
files. Mark only a view that reads no request identity, no database and no
cookie: it will run with none of that machinery.
"""
from django.middleware.clickjacking import XFrameOptionsMiddleware
from django.middleware.common import CommonMiddleware
from django.urls import Resolver404, resolve

#: Applied to a successful public read whose view did not choose its own.
#: Without ``Vary: Cookie`` (the session middleware used to add it) an
#: unmarked response was heuristically cacheable at best, so every built report
#: page re-fetched every widget. Five minutes keeps an upgrade visible quickly.
PUBLIC_ASSET_CACHE_CONTROL = "public, max-age=300, must-revalidate"


def public_asset(view):
    """Mark ``view`` as identity-independent static bytes (see module docstring)."""
    view.is_public_asset = True
    return view


def is_public_asset(view) -> bool:
    return getattr(view, "is_public_asset", False) is True


class PublicAssetMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response
        # Preserve the existing host, response length and frame protections.
        # Request ID, CSP and SecurityMiddleware already wrap this middleware.
        self.serve = CommonMiddleware(XFrameOptionsMiddleware(self._dispatch))

    @staticmethod
    def _dispatch(request):
        match = request.resolver_match
        return match.func(request, *match.args, **match.kwargs)

    def __call__(self, request):
        if request.method in {"GET", "HEAD"}:
            try:
                match = resolve(request.path_info, getattr(request, "urlconf", None))
            except Resolver404:
                pass
            else:
                if not match.namespace and is_public_asset(match.func):
                    request.resolver_match = match
                    response = self.serve(request)
                    if response.status_code == 200:
                        # A view that sets its own policy (the vendored files do)
                        # keeps it; a missing file is never cached.
                        response.setdefault("Cache-Control", PUBLIC_ASSET_CACHE_CONTROL)
                    return response
        return self.get_response(request)
