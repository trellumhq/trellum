from django.conf import settings


def _asset_version() -> str:
    """Cache-bust assets using their newest source modification time."""
    newest = 0.0
    for name in ("site.css", "site.js", "tour.js"):
        path = settings.BASE_DIR / "static" / name
        if path.is_file():
            newest = max(newest, path.stat().st_mtime)
    return str(int(newest))


def brand(request):
    """Values shared by every rendered page."""
    from apps.docs.markdown import agent_prompt

    return {
        "BRAND": settings.SITE_BRAND,
        "TAGLINE": settings.SITE_TAGLINE,
        "SITE_BASE_URL": settings.SITE_BASE_URL,
        "CONTACT_EMAIL": settings.CONTACT_EMAIL,
        "github_href": settings.GITHUB_URL,
        "WEBSITE_REPO_URL": settings.WEBSITE_REPO_URL,
        "demo_href": settings.DEMO_URL,
        "agent_prompt": agent_prompt(),
        "ASSET_V": _asset_version(),
    }
