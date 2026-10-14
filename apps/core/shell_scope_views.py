"""Authenticated native and enhanced scope browsing with fixed destinations."""
from functools import wraps
from urllib.parse import urlencode

from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import ValidationError
from django.http import Http404, HttpResponseBadRequest, HttpResponseNotFound, JsonResponse
from django.shortcuts import redirect, render
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


def _parameters(request, *, opening=False):
    data = request.GET
    if set(data) - {'kind', 'intent', 'org', 'q', 'page', 'selected'}:
        raise ValueError('Unknown scope parameter.')
    for name in ('kind', 'intent', 'org', 'q', 'page', 'selected'):
        if len(data.getlist(name)) > 1:
            raise ValueError('Duplicate scope parameter.')
    kind, intent = data.get('kind'), data.get('intent', 'navigate')
    if kind not in ('org', 'studio') or intent not in ('navigate', 'assistant') or (kind == 'org' and intent != 'navigate'):
        raise ValueError('Invalid scope kind or intent.')
    q = data.get('q', '')
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
    if (kind == 'studio' and not org) or (opening and not selected):
        raise ValueError('A scope selection is required.')
    return {'kind': kind, 'intent': intent, 'org': org, 'q': q.strip(), 'page': int(raw_page), 'selected': selected}


def _rows(request, params):
    orgs = organization_choices(request)
    if params['kind'] == 'org':
        return orgs, None
    org = orgs.filter(slug=params['org']).first()
    if org is None:
        raise Http404
    return studio_choices(request, org), org


def _destination(row, org, intent):
    if org is None:
        return '/?' + urlencode({'org': row.slug})
    return f'/s/{org.slug}/{row.slug}/' + ('?assistant=open' if intent == 'assistant' else '')


def _auth(request, *, json=False):
    if request.user.is_authenticated:
        return None
    return JsonResponse({'error': 'authentication required'}, status=401) if json else redirect_to_login(request.get_full_path())


def _item(row):
    return {'value': row.slug, 'label': row.name, 'hint': row.slug}


@_private_response
@require_GET
def api_choices(request):
    denied = _auth(request, json=True)
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


@_private_response
@require_GET
def browse(request):
    denied = _auth(request)
    if denied is not None:
        return denied
    try:
        params = _parameters(request)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))
    rows, org = _rows(request, params)
    if org is not None:
        # The native browse page has an authorized org scope, never a studio scope.
        request.org = org
    result = choice_page(rows, q=params['q'], page=params['page'], selected=params['selected'])
    items = [{'row': row, 'url': _destination(row, org, params['intent'])} for row in result.page.object_list]
    def page_url(number):
        return '/scopes/?' + urlencode({**params, 'page': number})
    response = render(request, 'core/shell_scope_choices.html', {
        'scope_params': params, 'scope_org': org, 'scope_items': items, 'scope_page': result.page,
        'scope_selected': result.selected, 'scope_selected_url': _destination(result.selected, org, params['intent']) if result.selected else '',
        'scope_previous': page_url(result.page.number - 1), 'scope_next': page_url(result.page.number + 1),
    })
    response['Cache-Control'] = 'private, no-store'
    return response


@_private_response
@require_GET
def open_scope(request):
    denied = _auth(request)
    if denied is not None:
        return denied
    try:
        params = _parameters(request, opening=True)
    except ValueError as exc:
        return HttpResponseBadRequest(str(exc))
    rows, org = _rows(request, params)
    row = rows.filter(slug=params['selected']).first()
    if row is None:
        raise Http404
    return redirect(_destination(row, org, params['intent']))
