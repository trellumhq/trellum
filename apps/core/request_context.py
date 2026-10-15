"""Identity boundaries for caches owned by a single HTTP request."""


def actor_key(request):
    """Include actor and bearer scope so impersonation cannot reuse authority."""
    user = getattr(request, "user", None)
    key = getattr(request, "api_key", None)
    return (
        getattr(user, "pk", None),
        bool(user and user.is_authenticated),
        bool(user and user.is_superuser),
        bool(user and user.is_active),
        getattr(key, "pk", None),
        getattr(key, "org_id", None),
        key is not None,
    )
