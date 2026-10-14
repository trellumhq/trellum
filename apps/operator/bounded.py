"""Database-backed presentation for the operator's native lists."""
from django.core.exceptions import BadRequest
from django.core.paginator import Paginator

from apps.orgs import quotas

PAGE_SIZE = 25


def search(params, name="q"):
    value = params.get(name, "")
    if len(value) > 200 or any(ord(char) < 32 for char in value):
        raise BadRequest("Invalid search")
    return value.strip()


def choice(params, name, choices):
    value = params.get(name, "")
    if value not in ("", *choices):
        raise BadRequest("Invalid filter")
    return value


def paginate(query, params, *, name="page", total=None):
    raw = params.get(name, "1")
    if not raw.isascii() or not raw.isdecimal() or len(raw) > 10:
        raise BadRequest("Invalid page")
    number = int(raw)
    if not 1 <= number <= 2**31 - 1:
        raise BadRequest("Invalid page")
    paginator = Paginator(query, PAGE_SIZE)
    page = paginator.get_page(number)
    # Evaluate only the selected page; templates never evaluate the base list.
    page.object_list = list(page.object_list)

    def url(number):
        values = params.copy()
        values[name] = str(number)
        return "?" + values.urlencode()

    return {
        "page": page,
        "total": paginator.count if total is None else total,
        "matching": paginator.count,
        "previous": url(page.previous_page_number()) if page.has_previous() else None,
        "next": url(page.next_page_number()) if page.has_next() else None,
    }


def preserved(params, names):
    """Successful controls for the neighboring independently paged section."""
    return [(name, params[name]) for name in names if name in params]


def usage(org, quota):
    """Live DB usage; storage remains unknown until separately observed.

    Keep this presentation separate from quota admission and tenant usage,
    which retain their authoritative file measurements.
    """
    values = {
        "active_runs": quotas.active_runs(org),
        "build_minutes": quotas.build_minutes_this_month(org),
        "studios": quotas.studio_count(org),
        "reports": quotas.report_count(org),
        "month": quotas.month_start().date().isoformat(),
    }
    # Display recorded limits even when enforcement is off.
    values.update({name: getattr(quota, name, 0) for name in quotas.LIMIT_FIELDS})
    return values
