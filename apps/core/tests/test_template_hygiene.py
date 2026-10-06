"""Static hygiene checks over every template in the tree.

Django's ``{# ... #}`` comment is single-line only: an "unclosed" one is not a
syntax error, it simply renders itself into the page as literal text. Two of
those shipped before this test existed (the operator menu in ``_shell.html``
and the annotations list header), each discovered by a human reading the
rendered page. Multi-line commentary belongs in ``{% comment %}`` blocks.
"""
from pathlib import Path

from django.conf import settings


def _template_files():
    for base in settings.TEMPLATES[0]["DIRS"]:
        yield from Path(base).rglob("*.html")


def test_short_comments_never_span_lines():
    offenders = []
    for path in _template_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "{#" in line and "#}" not in line:
                offenders.append(f"{path}:{lineno}")
    assert not offenders, (
        "unclosed {# ... #} on these lines renders as literal page text; "
        "use {% comment %} for multi-line notes: " + ", ".join(offenders)
    )
