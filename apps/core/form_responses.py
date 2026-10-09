"""Explicit JSON responses for progressively enhanced settings saves."""

from django.http import JsonResponse


def is_settings_request(request):
    return request.headers.get("X-Trellum-Form") == "1"


def settings_success(request, message="Saved.", *, redirect_url=None, **data):
    payload = {"ok": True, "message": message, **data}
    if redirect_url is not None:
        payload["redirect"] = redirect_url
    return JsonResponse(payload)


def settings_error(request, message="Please correct the highlighted fields.", *,
                   form=None, errors=None, status=400, **data):
    if form is not None:
        errors = {name: [str(error) for error in items] for name, items in form.errors.items()}
    return JsonResponse({"ok": False, "message": message, "errors": errors or {}, **data}, status=status)
