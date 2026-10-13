"""Explicit opt-in console fragments, sharing the native page content block."""
from django.http import JsonResponse
from django.shortcuts import render
from django.template.loader import render_to_string
from django.utils.cache import patch_vary_headers


def render_navigation(request, template, context, *, controller, title):
    """Call only after the ordinary route's authorization and data preparation.

    Controllers are static client adapters; this helper neither grants access
    nor extracts or executes a full document's scripts.
    """
    context = {**context, "navigation_controller": controller}
    if request.method == "GET" and request.headers.get("X-Trellum-Navigation") == "1":
        context["navigation_fragment"] = True
        response = JsonResponse({
            "version": 1, "controller": controller, "title": title,
            "url": request.get_full_path(),
            "html": render_to_string(template, context, request=request),
        })
        response["Cache-Control"] = "private, no-store"
    else:
        response = render(request, template, context)
    patch_vary_headers(response, ["X-Trellum-Navigation"])
    return response
