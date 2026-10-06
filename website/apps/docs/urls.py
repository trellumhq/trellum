from django.urls import path, re_path

from . import views

urlpatterns = [
    path("", views.root, name="docs-root"),
    re_path(r"^(?P<version>[\w.-]+)/$", views.index, name="docs-index"),
    re_path(
        r"^(?P<version>[\w.-]+)/search\.json$",
        views.search_index,
        name="docs-search",
    ),
    # Slugs are multi-segment (install/docker-compose); nav.source_path
    # validates the shape and confines it to the version directory.
    re_path(
        r"^(?P<version>[\w.-]+)/(?P<slug>[\w/-]+)/$",
        views.page,
        name="docs-page",
    ),
]
