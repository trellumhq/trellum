"""Build-time, host-independent publication of bounded metric workspace pages."""
from __future__ import annotations

import os
import re
import shutil
import tempfile
from html import escape
from pathlib import Path

import orjson

PAGE_SIZE = 25
VERSION = 1
PREFIX = "_metrics-workspace-"
_OWNED = re.compile(r"_metrics-workspace-[0-9a-f]{32}-(?:page-[1-9][0-9]*\.(?:html|json)|catalog\.json)\Z")
BLOCKS = "<!-- metrics-workspace-blocks -->"
CONTROLS = "<!-- metrics-workspace-controls -->"
DEFINITIONS = "<!-- metrics-workspace-definitions -->"


def project_series(payload, components):
    """Keep rows exact while excluding columns used only by off-page metrics."""
    required = {}
    for value in components.values():
        dataset = value.get('dataset_id')
        if not dataset:
            continue
        columns = required.setdefault(dataset, set())
        for key in ('x', 'y', 'column', 'date_col', 'numerator', 'denominator'):
            if isinstance(value.get(key), str):
                columns.add(value[key])
        columns.update(column for column in value.get('value_cols', []) if isinstance(column, str))
        columns.update(column for column in value.get('stack_by_options', {}).values() if column)
        for child in value.get('kpis', []) + value.get('filters', []):
            for key in ('column', 'date_col', 'numerator', 'denominator'):
                if isinstance(child.get(key), str):
                    columns.add(child[key])
            columns.update(column for column in child.get('columns', []) if isinstance(column, str))
        for target, mapping in value.get('propagate_to', {}).items():
            required.setdefault(target, set()).update(mapping.values())
    for dataset, columns in required.items():
        key = '_ds_' + dataset
        raw = payload.get(key)
        if not isinstance(raw, dict) or not isinstance(raw.get('_cols'), list) or not isinstance(raw.get('_data'), list):
            continue
        indices = [index for index, name in enumerate(raw['_cols']) if name in columns]
        if not indices and raw['_cols']:
            indices = [0]  # A pure count still needs the exact number of rows.
        names = [raw['_cols'][index] for index in indices]
        payload[key] = {**raw, '_cols': names,
            '_data': [[row[index] for index in indices] for row in raw['_data']],
            '_dict': {name: values for name, values in raw.get('_dict', {}).items() if name in names}}


def page_count(monitored):
    """Native pages for ``monitored`` metric blocks; always at least one.

    Only monitored blocks are paged. The definitions table is complete in the
    catalog and pages client-side, so it never adds pages of its own.
    """
    return max(1, (monitored + PAGE_SIZE - 1) // PAGE_SIZE)


def owned_name(name):
    """Never accept paths from a prior artifact's untrusted ownership manifest."""
    return isinstance(name, str) and bool(_OWNED.fullmatch(name))


def controls(catalog, page, pages, build):
    monitored = [row for row in catalog if row.get('monitored', True)]
    rows = monitored[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
    choices = "".join(
        f'<label><input type="checkbox" value="{escape(row["anchor"], quote=True)}" '
        f'data-category="{escape(row["category"], quote=True)}" checked> '
        f'{escape(row["category"])} · {escape(row["label"])}</label>' for row in rows)
    current = 'aria-current="page"'
    links = "".join(
        f'<a href="{PREFIX}{build}-page-{number}.html" '
        f'{current if number == page else ""}>Page {number}</a>'
        for number in range(max(1, page - 2), min(pages, page + 2) + 1))
    return (
        '<div id="fwMetricsWorkspace"><details><summary>Show / hide metrics</summary>'
        '<label>Search all metrics <input type="search" id="fwMetricsSearch" maxlength="200"></label>'
        '<div id="fwMetricsSearchResults" aria-live="polite"></div>'
        f'<div class="fw-metric-choices">{choices}</div></details>'
        '<div class="fw-metric-actions"><button type="button" id="fwMetricsShowAll">Show all</button>'
        '<button type="button" id="fwMetricsHideAll">Hide all</button>'
        '<span id="fwMetricsCount" role="status" aria-live="polite"></span></div>'
        # One page of metrics is the common case: nothing to page through.
        + (f'<nav aria-label="Metric pages">{links}</nav><p>Page {page} of {pages}</p>' if pages > 1 else '')
        + '</div>'
        '<p id="fwMetricsEmpty" hidden>No metrics selected. Use Show all to restore them.</p>')


def early_route(catalog, build, page, pages):
    """Route focus before bootstrapping an obsolete page's vendors or consumers."""
    if not isinstance(build, str) or not re.fullmatch(r'[0-9a-f]{32}', build) or type(page) is not int or type(pages) is not int or not 1 <= page <= pages:
        raise ValueError('invalid generated workspace routing identity')
    # Definitions all live on one page, so a prefix rule replaces one entry per
    # definition: the inline map must not grow with the number of definitions.
    routes = {row['anchor']: row['page'] for row in catalog if row.get('monitored', True)}
    definitions = {row['page'] for row in catalog if not row.get('monitored', True)}
    if len(definitions) > 1:
        raise ValueError('invalid generated workspace route page')
    definition_page = definitions.pop() if definitions else 0
    if any(type(number) is not int or not 1 <= number <= pages for number in (*routes.values(), *([definition_page] if definition_page else []))):
        raise ValueError('invalid generated workspace route page')
    encoded = orjson.dumps(routes).decode().replace('<', '\\u003c').replace('\u2028', '\\u2028').replace('\u2029', '\\u2029')
    return ('<script data-metrics-early-route>(function(){'
        'var url=new URL(location.href),target=url.searchParams.get("only");'
        'if(!target){target=url.hash.slice(1);try{target=decodeURIComponent(target)}catch(e){}}'
        'if(!target)return;var routes=' + encoded + ',page=routes[target];'
        + (f'if(page===undefined&&target.indexOf("definition-")===0)page={definition_page};' if definition_page else '') +
        f'if(!Number.isInteger(page)||page<1||page>{pages}||page==={page})return;'
        f'url.pathname=url.pathname.replace(/[^/]*$/, "{PREFIX}{build}-page-"+page+".html");'
        'window.stop();location.replace(url.href);})();</script>')


def write_pages(out, html, report_data, render_ctx, catalog, build):
    """Keep root data complete; each immutable page carries only its consumers.

    The "defined but not monitored" table appears once, on the last page, where
    the catalog routes every definition. Its rows come from the catalog in the
    browser, so a page carries no definition rows of its own.
    """
    blocks = render_ctx._metric_workspace_blocks
    metric_ids = {cid for block in blocks for cid in block['ids']}
    common = {cid: value for cid, value in report_data['components'].items() if cid not in metric_ids}
    definition_id = getattr(render_ctx, '_metric_workspace_definition_id', None)
    if definition_id:
        indexed = {row[0]: row[2:] for row in common[definition_id]['rows']}
        catalog = [{**row, 'details': indexed[row['name']]} if not row.get('monitored', True) else row
                   for row in catalog]
    pages = page_count(len(blocks))
    files = []
    catalog_name = f'{PREFIX}{build}-catalog.json'
    Path(out, catalog_name).write_bytes(orjson.dumps({'version': VERSION, 'build': build, 'metrics': catalog}))
    files.append(catalog_name)
    first = None
    for page in range(1, pages + 1):
        visible = blocks[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
        components = dict(common)
        for block in visible:
            components.update({cid: report_data['components'][cid] for cid in block['ids'] if cid in report_data['components']})
        if definition_id:
            original = components.pop(definition_id)
            if page == pages:
                components[definition_id] = {**original, 'rows': [], 'totalRows': 0,
                    'maxRows': PAGE_SIZE, 'workspacePageSize': PAGE_SIZE}
        needed = {value['dataset_id'] for cid, value in components.items()
                  if cid not in common and value.get('dataset_id')}
        needed.update(value['dataset_id'] for value in components.values()
                      if value.get('type') == 'filter_bar' and value.get('dataset_id'))
        components = {cid: value for cid, value in components.items()
                      if value.get('type') != 'data_source' or value.get('dataset_id') in needed}
        for cid, value in tuple(components.items()):
            if value.get('type') == 'filter_bar' and value.get('propagate_to'):
                components[cid] = {**value, 'propagate_to': {dataset: columns for dataset, columns
                    in value['propagate_to'].items() if dataset in needed}}
        payload = {key: value for key, value in report_data.items() if key not in ('components', '_metrics')
                   and (not key.startswith('_ds_') or key[4:] in needed)}
        selected = set(components)
        payload['_metrics'] = {name: value for name, value in report_data.get('_metrics', {}).items()
                               if selected.intersection(value.get('component_ids', []))}
        payload['components'] = components
        project_series(payload, components)
        payload['_metrics_workspace'] = {'version': VERSION, 'build': build, 'page': page}
        data_name = f'{PREFIX}{build}-page-{page}.json'
        html_name = f'{PREFIX}{build}-page-{page}.html'
        config = {'version': VERSION, 'build': build, 'page': page, 'pages': pages,
                  'data': data_name, 'catalog': catalog_name, 'root': 'index.html',
                  'definitionId': definition_id}
        # The fixed JSON script contains only validated generated file names.
        boot = (early_route(catalog, build, page, pages) + '<script>window._fwMetricsWorkspace=' + orjson.dumps(config).decode() + ';</script>'
                '<noscript><style>.fw-loading{display:none!important}</style></noscript>')
        definition_html = ''
        if definition_id and page == pages:
            definition_html = ('<section class="fw-section"><h2>Defined but not monitored</h2>'
                + getattr(render_ctx, '_metric_workspace_definition_html', '') + '</section>')
        page_html = html.replace(CONTROLS, controls(catalog, page, pages, build)).replace(
            BLOCKS, '\n'.join(block['html'] for block in visible)).replace(
            DEFINITIONS, definition_html).replace('<head>', '<head>' + boot, 1)
        Path(out, data_name).write_bytes(orjson.dumps(payload, option=orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_NON_STR_KEYS))
        Path(out, html_name).write_text(page_html, encoding='utf-8')
        files.extend((data_name, html_name))
        if first is None:
            first = page_html
    Path(out, 'index.html').write_text(first, encoding='utf-8')
    meta_path = Path(out, '_meta.json')
    meta = orjson.loads(meta_path.read_bytes())
    meta['metrics_workspace'] = {'version': VERSION, 'build': build, 'pages': pages, 'files': files}
    meta_path.write_bytes(orjson.dumps(meta, option=orjson.OPT_INDENT_2))
    return files


def collect_old_pages(out):
    try:
        marker = orjson.loads(Path(out, '_meta.json').read_bytes()).get('metrics_workspace', {})
        names = marker.get('files', []) if isinstance(marker, dict) else []
        return {name for name in names if owned_name(name)} if isinstance(names, list) else set()
    except (OSError, ValueError):
        return set()


def owned_files(out):
    """Owned page files actually present, however a killed build left them."""
    try:
        return {name for name in os.listdir(out) if owned_name(name)}
    except OSError:
        return set()


def gc_old_pages(out, previous, current):
    for name in previous - set(current):
        path = Path(out, name)
        if owned_name(name) and not path.is_symlink():
            try:
                path.unlink()
            except FileNotFoundError:
                pass


def staged_render(ctx, out, render, **kwargs):
    """Stage first; expose success only after immutable pages and roots publish.

    Readers of the previous index retain its immutable payload during replacement.
    The mutable metadata says uncertain during root replacement. A failed publish
    restores the previous roots and removes only this attempt's owned pages.
    """
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    # The manifest lists the last published build; a build killed mid-publish
    # can also leave owned pages the manifest never learned about. Both are
    # removed once this build has published.
    previous = collect_old_pages(out) | owned_files(out)
    # Stage inside `out`: the build sandbox mounts only this report's output
    # directory writable, so a sibling of it (out.parent) is read-only there.
    # Dot-prefixed and removed on exit. A killed build leaves its stage
    # directory and publish temporaries behind; clear them before staging.
    for leftover in out.glob('.metrics-stage-*'):
        if leftover.is_dir() and not leftover.is_symlink():
            shutil.rmtree(leftover, ignore_errors=True)
    for leftover in out.glob('.metrics-publish-*'):
        if leftover.is_file() or leftover.is_symlink():
            leftover.unlink(missing_ok=True)
    with tempfile.TemporaryDirectory(prefix='.metrics-stage-', dir=out) as stage:
        ctx._metric_workspace_staging = True
        ctx._metric_workspace_chunks = None
        try:
            render(ctx, output_dir=stage, **kwargs)
        finally:
            del ctx._metric_workspace_staging
        current = collect_old_pages(stage)
        staged = Path(stage)
        # This render owns files it actually produced, never arbitrary manifest paths.
        roots = [path.name for path in staged.iterdir() if path.is_file() and not owned_name(path.name)]
        saved = {}
        for name in roots:
            path = out / name
            if path.is_symlink():
                raise ValueError('workspace output root must not be a symlink')
            saved[name] = path.read_bytes() if path.exists() else None
        published = []
        try:
            for name in current:
                target = out / name
                if target.is_symlink():
                    raise ValueError('workspace page must not be a symlink')
                shutil.copyfile(staged / name, target)
                published.append(name)
            if saved.get('_meta.json'):
                marker = orjson.loads(saved['_meta.json'])
                marker['last_status'] = 'uncertain'
                _replace(out / '_meta.json', orjson.dumps(marker))
            for name in roots:
                if name not in ('index.html', '_meta.json'):
                    _replace(out / name, (staged / name).read_bytes())
            _replace(out / 'index.html', (staged / 'index.html').read_bytes())
            _replace(out / '_meta.json', (staged / '_meta.json').read_bytes())
        except BaseException:
            for name, content in saved.items():
                if content is None:
                    (out / name).unlink(missing_ok=True)
                else:
                    _replace(out / name, content)
            for name in published:
                (out / name).unlink(missing_ok=True)
            raise
        gc_old_pages(out, previous, current)
        # The stage render collected stale chunks from the (empty) stage
        # directory; collect them from the published directory instead.
        chunks = getattr(ctx, '_metric_workspace_chunks', None)
        if chunks is not None:
            from trellum.rendering.artifacts import _gc_stale_chunks
            _gc_stale_chunks(str(out), chunks)
    return str(out / 'index.html'), str(out / 'data.json')


def _replace(path, content):
    with tempfile.NamedTemporaryFile(dir=path.parent, prefix='.metrics-publish-', delete=False) as pending:
        temporary = pending.name
        pending.write(content)
    try:
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)
