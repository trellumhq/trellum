"""Routes rendered by the static exporter."""

from django.urls import include, path

from apps.pages import views as pages

urlpatterns = [
    path("", pages.landing, name="landing"),
    path("privacy/", pages.privacy, name="privacy"),
    path("terms/", pages.terms, name="terms"),
    path("cookies/", pages.cookies, name="cookies"),
    path("tour/", pages.tour, name="tour"),
    path("robots.txt", pages.robots, name="robots"),
    path("sitemap.xml", pages.sitemap, name="sitemap"),
    path("docs/", include("apps.docs.urls")),
    path("blog/", include("apps.blog.urls")),
]

handler404 = "apps.pages.views.not_found"
