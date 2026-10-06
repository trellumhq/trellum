"""``{% docs_url %}`` -- see :mod:`apps.core.docs` for why links are not
hard-coded to trellum.dev/docs/latest any more."""
from django import template

from apps.core import docs

register = template.Library()


@register.simple_tag(name="docs_url")
def docs_url_tag(path: str = "") -> str:
    return docs.docs_url(path)
