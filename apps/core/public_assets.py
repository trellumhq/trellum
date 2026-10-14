"""Serve the reviewed, identity-independent report scripts without a session.

Built reports retain these stable URLs across portal upgrades. Like ordinary
static files, reading their public bytes must not renew a login or consult the
database. Protected API calls made by those scripts use the normal middleware.
"""
from django.middleware.clickjacking import XFrameOptionsMiddleware
from django.middleware.common import CommonMiddleware
from django.urls import Resolver404, resolve


class PublicAssetMiddleware:
    def __init__(self, get_response):
        from apps.assistant.views import widget_js
        from apps.reports import views
        from apps.reports.vendor import vendor_asset

        self.get_response = get_response
        self.views = {
            "vendor-asset": vendor_asset,
            "assistant-widget": widget_js,
            "reports-menu-widget": views.menu_widget_js,
            "reports-delivery-widget": views.delivery_widget_js,
            "reports-theme-widget": views.theme_widget_js,
            "reports-share-widget": views.share_widget_js,
            "reports-embed-widget": views.embed_widget_js,
            "reports-embed-element": views.embed_element_js,
            "reports-views-widget": views.views_widget_js,
            "reports-metrics-widget": views.metrics_widget_js,
        }
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
                if not match.namespace and self.views.get(match.url_name) is match.func:
                    request.resolver_match = match
                    return self.serve(request)
        return self.get_response(request)
