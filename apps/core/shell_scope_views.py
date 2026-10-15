"""Authenticated search over the organizations and studios a user may switch to.

The shell lists every choice inline; only a list longer than the inline limit
uses this endpoint, as search-as-you-type."""
from functools import wraps

from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponseNotFound, JsonResponse
from django.views.decorators.http import require_GET

from apps.core.shell_scope import choice_page, organization_choices, studio_choices
from apps.orgs.models import Organization
from apps.studios.models import Studio


def _private_response(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        try:
            response = view(request, *args, **kwargs)
        except Http404:
            response = HttpResponseNotFound('Scope is unavailable.')
        response['Cache-Control'] = 'private, no-store'
        return response
    return wrapped


def _parameters(request):
    data = request.GET
    if set(data) - {'kind', 'org', 'q', 'page', 'selected'}:
        raise ValueError('Unknown scope parameter.')
    for name in ('kind', 'org', 'q', 'page', 'selected'):
        if len(data.getlist(name)) > 1:
            raise ValueError('Duplicate scope parameter.')
    kind = data.get('kind')
    if kind not in ('org', 'studio'):
        raise ValueError('Invalid scope kind.')
    q = data.get('q', '')
    if '\x00' in q:
        raise ValueError('Search contains an invalid character.')
    if len(q) > 200:
        raise ValueError('Search must contain at most 200 characters.')
    raw_page = data.get('page', '1')
    if not raw_page or len(raw_page) > 10 or not raw_page.isascii() or not raw_page.isdecimal() or not 1 <= int(raw_page) <= 2147483647:
        raise ValueError('Invalid page.')
    org, selected = data.get('org', ''), data.get('selected', '')
    for value, model in ((org, Organization), (selected, Organization if kind == 'org' else Studio)):
        if value:
            field = model._meta.get_field('slug')
            if len(value) > field.max_length:
                raise ValueError('Invalid scope slug.')
            try:
                field.run_validators(value)
            except ValidationError as exc:
                raise ValueError('Invalid scope slug.') from exc
    if kind == 'studio' and not org:
        raise ValueError('A scope selection is required.')
    return {'kind': kind, 'org': org, 'q': q.strip(), 'page': int(raw_page), 'selected': selected}


def _rows(request, params):
    orgs = organization_choices(request)
    if params['kind'] == 'org':
        return orgs, None
    org = orgs.filter(slug=params['org']).first()
    if org is None:
        raise Http404
    return studio_choices(request, org), org


def _auth(request):
    if request.user.is_authenticated:
        return None
    return JsonResponse({'error': 'authentication required'}, status=401)


def _item(row):
    return {'value': row.slug, 'label': row.name, 'hint': row.slug}


@_private_response
@require_GET
def api_choices(request):
    denied = _auth(request)
    if denied is not None:
        return denied
    try:
        params = _parameters(request)
    except ValueError as exc:
        return JsonResponse({'error': str(exc)}, status=400)
    rows, _ = _rows(request, params)
    result = choice_page(rows, q=params['q'], page=params['page'], selected=params['selected'])
    page = result.page
    response = JsonResponse({'results': [_item(row) for row in page.object_list],
        'selected': _item(result.selected) if result.selected else None,
        'pagination': {'page': page.number, 'pages': page.paginator.num_pages, 'total': page.paginator.count,
                       'has_next': page.has_next(), 'has_previous': page.has_previous()}})
    response['Cache-Control'] = 'private, no-store'
    return response
