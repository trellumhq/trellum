"""Redact report content without rewriting the publication protocol.

Generated tags, runtime code, schema keys and routing identities are public
structure. A common secret (``1``, ``a`` or ``true``) can coincide with that
structure; replacing those bytes would destroy a report. Content-bearing
fields and raw report data are filtered separately, before serialization.
"""
from __future__ import annotations

import html
import re
from html.parser import HTMLParser

import orjson

from trellum.redaction import _secret_values, redact

# These values are references/enums/layout settings, not report cell content.
# Keep the same identifiers in markup, component configs and dataset schemas.
_PROTOCOL_FIELDS = frozenset("""
    type cid component_ids metric metric_id agg aggregation format delta_format delta_direction
    alignment position time_agg grain definition_hash definitionHash definition_version
    orientation mode version schema_version page pages build build_id
    generated_at timestamp level groups_run refresh_seconds monitored static sortable searchable live_context
    defaultScope _scopeFiles _chunk_manifests _freshness _framework_version
    _content_kind _metrics_workspace _metric_execution
""".split())
_CONTENT_FIELDS = frozenset({'value', 'values', 'data', 'rows', 'labels', 'forecast', 'delta'})
_PAYLOAD_PROTOCOL_FIELDS = frozenset('''
    defaultScope _scopeFiles _chunk_manifests _freshness _framework_version
    _content_kind _metrics_workspace _metric_execution
'''.split())


def content(value):
    """Filter report-authored data before it joins framework-owned envelopes."""
    if not _secret_values():
        return value
    normalized = orjson.loads(orjson.dumps(
        value, option=orjson.OPT_SERIALIZE_NUMPY | orjson.OPT_NON_STR_KEYS))
    return redact(normalized)


def record(value):
    """Filter content in a framework record; preserve its public schema."""
    if isinstance(value, dict):
        return {key: child if key in _PROTOCOL_FIELDS else
                validation(child) if key == 'validation' else
                redact(child) if key in _CONTENT_FIELDS else record(child)
                for key, child in value.items()}
    if isinstance(value, (list, tuple)):
        return [record(child) for child in value]
    return redact(value) if isinstance(value, str) else value


def dataset(value):
    """Filter columnar cells, retaining dictionary codes and column identity."""
    if not isinstance(value, dict) or '_data' not in value:
        return redact(value)
    dictionaries = value.get('_dict', {})
    columns = value.get('_cols', [])
    return {**value, '_cols': redact(columns),
            '_dict': {redact(key): redact(items) for key, items in dictionaries.items()},
            '_data': [[cell if index < len(columns) and columns[index] in dictionaries
                       else redact(cell) for index, cell in enumerate(row)]
                      for row in value['_data']]}


def payload(value):
    """Filter standard report data, distinguishing user data from envelopes."""
    result = {}
    for key, child in value.items():
        if key == 'components':
            result[key] = {cid: record(config) for cid, config in child.items()}
        elif key.startswith('_ds_'):
            result['_ds_' + redact(key[4:])] = dataset(child)
        elif key == '_metrics':
            result[key] = {name: record(config) for name, config in child.items()}
        elif key in _PAYLOAD_PROTOCOL_FIELDS:
            result[key] = child
        elif key == '_header_meta':
            result[key] = record(child)
        else:
            result[redact(key)] = redact(child)
    return result


def json_bytes(value, *, kind='record', **kwargs):
    """Normalize numpy/date objects, then filter only the artifact's content."""
    encoded = orjson.dumps(value, **kwargs)
    if not _secret_values():
        return encoded
    normalized = orjson.loads(encoded)
    transform = {'record': record, 'payload': payload, 'dataset': dataset}[kind]
    return orjson.dumps(transform(normalized), **kwargs)


def validation(value):
    """Validator identifiers/counts are protocol; finding text is content."""
    if not isinstance(value, dict) or not isinstance(value.get('checks'), list):
        return record(value) if isinstance(value, dict) else redact(value)
    return {**value, 'checks': [
        {key: redact(child) if key in {'message', 'component', 'section', 'dataset_id'} else child
         for key, child in check.items()} if isinstance(check, dict) else redact(check)
        for check in value['checks']]}


def metadata(value):
    """Keep host protocol and generated build identity intact across updates."""
    public = {'schema_version', 'slug', 'kind', 'version', 'last_run', 'last_status',
              'framework_version', 'framework_license', 'framework_source_url',
              'metrics_workspace', 'metric_execution_build'}
    result = {key: child if key in public else validation(child) if key == 'validation'
              else record(child) for key, child in value.items()}
    if isinstance(value.get('metrics_used'), list):
        # These three fields identify registry definitions to the host; labels
        # and arbitrary extra metadata still go through content filtering.
        result['metrics_used'] = [{key: child if key in {'id', 'definition_hash', 'version'}
                                   else record(child) for key, child in entry.items()} if isinstance(entry, dict) else entry
                                  for entry in value['metrics_used']]
    return result


class _ContentHTML(HTMLParser):
    def __init__(self, trusted_code):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.code = None
        self.trusted_code = trusted_code

    def handle_starttag(self, tag, attrs):
        raw = self.get_starttag_text()
        # Do not redact markup grammar or generated component identities.
        # Attribute offsets are replaced in the original tag to retain quoting.
        def attribute(match):
            name, spacing, quote, value, unquoted = match.groups()
            value = unquoted if quote is None else value
            generated = self.trusted_code and (
                name in {'class', 'style', 'type', 'role', 'xmlns', 'viewBox', 'd',
                         'lang', 'data-theme', 'data-content-kind', 'aria-expanded', 'aria-current',
                         'width', 'height', 'x', 'y', 'x1', 'x2', 'y1', 'y2',
                         'cx', 'cy', 'r', 'rx', 'fill', 'stroke', 'stroke-width'} or (
                tag in {'script', 'link'} and name in {'src', 'href'}) or (
                tag == 'meta' and dict(attrs).get('name') in {'viewport', 'theme-color'}) or (
                name in {'id', 'href', 'aria-controls', 'for'} and re.fullmatch(
                    r'(?:#?fw[_A-Za-z][\w-]*|#?(?:metric|definition)-[\w-]+|_metrics-workspace-[0-9a-f]{32}-[^/]+)', value)))
            if generated or (self.trusted_code and name.startswith(('data-fw-kind', 'data-csv-target', 'data-table-'))):
                return match.group()
            filtered = html.escape(redact(html.unescape(value)), quote=True)
            if filtered == value:
                return match.group()
            quote = quote or '"'
            return name + spacing + quote + filtered + quote
        raw = re.sub(r'''([^\s=<>]+)(\s*=\s*)(?:(["'])(.*?)\3|([^\s>]+))''', attribute, raw, flags=re.DOTALL)
        self.parts.append(raw)
        if tag in {'script', 'style'}:
            self.code = tag

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        self.code = None

    def handle_endtag(self, tag):
        self.parts.append(f'</{tag}>')
        if tag == self.code:
            self.code = None

    def handle_data(self, data):
        self.parts.append((data if self.trusted_code else redact(data)) if self.code
                          else html.escape(redact(data), quote=False))

    def handle_entityref(self, name):
        self.parts.append('&' + name + ';')

    def handle_charref(self, name):
        self.parts.append('&#' + name + ';')

    def handle_comment(self, data):
        markers = {'metrics-workspace-blocks', 'metrics-workspace-controls', 'metrics-workspace-definitions'}
        self.parts.append('<!--' + (data if data.strip() in markers else redact(data)) + '-->')

    def handle_decl(self, decl):
        self.parts.append('<!' + decl + '>')


def html_content(value, *, trusted_code=True):
    """Filter content nodes, retaining tags and trusted framework JS/CSS.

    Report-authored RawHTML/code is filtered before it enters this shell.
    Arbitrary custom code still must not interpolate secrets into executable
    syntax; there is no general way to infer provenance from JavaScript text.
    """
    if not _secret_values():
        return value
    parser = _ContentHTML(trusted_code)
    # Decode references in data nodes together, so an escaped secret spanning
    # entity boundaries is recognized; the parser leaves the markup itself.
    parser.feed(value)
    parser.close()
    return ''.join(parser.parts)


def component_output(method, value, *, markup=False):
    """Bundled renderers own public code; report/plugin code remains content.

    Inspect the implementation, so inheriting a native renderer does not turn
    its framework-owned markup or assets into report-authored code.
    """
    if getattr(method, '__module__', '').startswith('trellum.'):
        return value
    return html_content(value, trusted_code=False) if markup else redact(value)
