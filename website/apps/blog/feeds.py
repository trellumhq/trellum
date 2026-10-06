"""RSS for the blog.

Django's syndication framework needs no database and no sites app — with
django.contrib.sites uninstalled it derives the feed's domain from the request
instead — so a site with DATABASES={} gets a standards-compliant feed for a
dozen lines and no dependency.
"""

import datetime as dt

from django.conf import settings
from django.contrib.syndication.views import Feed

from apps.docs import markdown

from . import posts as blog


class BlogFeed(Feed):
    link = "/blog/"

    def title(self):
        return f"{settings.SITE_BRAND} blog"

    def description(self):
        return f"Notes from building {settings.SITE_BRAND}."

    def items(self):
        # The index already defines newest-first publication order.
        return blog.posts()

    def item_title(self, item):
        source = blog.source_path(item.slug)
        if source is None:
            return item.slug
        return markdown.render(source, blog.figures_dir()).title

    def item_description(self, item):
        return item.summary

    def item_link(self, item):
        return item.url

    def item_author_name(self, item):
        return item.author or None

    def item_pubdate(self, item):
        # A post is dated to the day; RSS wants a timestamp, and USE_TZ means
        # an aware one. Midnight UTC is the honest reading of "20 August".
        return dt.datetime.combine(item.date, dt.time(), tzinfo=dt.timezone.utc)
