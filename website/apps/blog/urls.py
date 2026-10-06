from django.urls import path

from . import views
from .feeds import BlogFeed

urlpatterns = [
    path("", views.index, name="blog-index"),
    # Before the slug route, which would otherwise claim it.
    path("rss.xml", BlogFeed(), name="blog-feed"),
    path("<slug:slug>/", views.post, name="blog-post"),
]
