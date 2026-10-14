"""Bounded declaration/result normalization, with no request-time artifact I/O."""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from .models import ReportExperimentSummary, ReportSourceSummary
from .overviews.experiments import _ab_entries, _active_body, _component_for_entry

AB_BYTES = 26 * 1024 * 1024
MAX_DECLARATIONS = 256
MAX_COMPONENTS = 256
MAX_KPIS = 256
MAX_SCOPES = 16
PROJECTION_BYTES = 256 * 1024
OVERSIZED_DECLARATION_DIGEST = hashlib.sha256(b'oversized-declarations-v1').hexdigest()
DECLARATION_KEYS = ('name', 'test_name', 'split', 'control_label', 'test_label', 'start_date', 'end_date', 'planned_end', 'closed', 'primary_metric')


class ProjectionOversized(ValueError):
    pass


def text(value, limit=200):
    if not isinstance(value, (str, int, float, bool, date, datetime, type(None))):
        raise ProjectionOversized('nested projection scalar')
    if isinstance(value, str) and len(value) > limit:
        raise ProjectionOversized('projection text limit exceeded')
    try:
        value = '' if value is None else str(value)
    except ValueError as exc:
        raise ProjectionOversized('projection scalar exceeds conversion limit') from exc
    if len(value) > limit:
        raise ProjectionOversized('projection text limit exceeded')
    return value


def has_declarations(config):
    raw = (config or {}).get('ab_test')
    return isinstance(raw, dict) or isinstance(raw, list) and bool(raw)


def declarations(config):
    raw = (config or {}).get('ab_test')
    if isinstance(raw, list) and len(raw) > MAX_DECLARATIONS:
        raise ProjectionOversized('declaration input count exceeded')
    entries = _ab_entries(config)
    normalized = []
    serialized_bytes = 2
    for entry in entries:
        values = {}
        for key in DECLARATION_KEYS:
            if key in entry:
                # Admission precedes JSON conversion/hash; preserve valid raw
                # scalar types so normal declaration identity remains sensitive.
                text(entry[key])
                values[key] = entry[key]
        serialized_bytes += len(json.dumps(values, sort_keys=True, default=str).encode()) + (2 if normalized else 0)
        if serialized_bytes > PROJECTION_BYTES:
            raise ProjectionOversized('declaration byte limit exceeded')
        normalized.append(values)
    digest = hashlib.sha256(json.dumps(normalized, sort_keys=True, default=str).encode()).hexdigest()
    return normalized, digest


def declaration_identity(config):
    try:
        return declarations(config)[1]
    except ProjectionOversized:
        # Only usable with an explicit oversized state and no experiment rows.
        # Unsupported edits cannot publish numeric results under this sentinel.
        return OVERSIZED_DECLARATION_DIGEST


def parsed_date(value):
    try:
        return date.fromisoformat(str(value)) if value else None
    except (ValueError, TypeError):
        return None


def declared_rows(config):
    entries, digest = declarations(config)
    if len(entries) > MAX_DECLARATIONS:
        raise ProjectionOversized('declaration count exceeded')
    if len(json.dumps(entries, default=str).encode()) > PROJECTION_BYTES:
        raise ProjectionOversized('declaration byte limit exceeded')
    rows = []
    for ordinal, declaration in enumerate(entries):
        # Dates and scalar identity only; never retain arbitrary nested config.
        declaration = {k: bool(v) if k == 'closed' else text(v) for k, v in declaration.items()}
        rows.append(dict(ordinal=ordinal, declaration_digest=digest, declaration=declaration,
            start_date=parsed_date(declaration.get('start_date')),
            end_date=parsed_date(declaration.get('end_date')) or parsed_date(declaration.get('planned_end')),
            closed=bool(declaration.get('closed')), test_name=declaration.get('test_name', ''),
            split=declaration.get('split', ''), control_label=declaration.get('control_label', ''),
            test_label=declaration.get('test_label', ''), payload_state='pending'))
    return rows, digest


def sync_declarations(report, prior_config=None):
    """Caller owns the Report update transaction; changes cancel any old lease."""
    from .source_summary import _revision
    digest = declaration_identity(report.config)
    summary = ReportSourceSummary.objects.select_for_update().filter(report=report).first()
    if summary and summary.declaration_digest == digest:
        return
    if summary is None:
        summary = ReportSourceSummary.objects.create(report=report)
    try:
        rows, _ = declared_rows(report.config)
        state = 'pending' if rows else 'not-applicable'
    except ProjectionOversized:
        rows, state = [], 'oversized'
    summary.experiments.all().delete()
    ReportExperimentSummary.objects.bulk_create([ReportExperimentSummary(summary=summary, **r) for r in rows])
    summary.declaration_digest = digest
    summary.experiment_state = state
    summary.generation += 1
    summary.lease_token = ''
    summary.lease_until = None
    summary.next_check_at = timezone.now()
    summary.save()
    _revision(report.studio_id)


def finite(value):
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    except OverflowError:
        return False


def project_results(config, payload, components):
    rows, digest = declared_rows(config)
    if len(components) > MAX_COMPONENTS:
        raise ProjectionOversized('component count exceeded')
    freshness = payload.get('_freshness') or {}
    try:
        stamp = parse_datetime(freshness.get('generated_at')) if isinstance(freshness, dict) and isinstance(freshness.get('generated_at'), str) else None
    except (ValueError, TypeError):
        stamp = None
    if stamp and timezone.is_naive(stamp):
        stamp = timezone.make_aware(stamp)
    components = [{**component, 'header': component.get('header') if isinstance(component.get('header'),dict) else {}} for component in components]
    for row in rows:
        declaration = row['declaration']
        component = _component_for_entry(declaration, components, row['ordinal'], len(rows))
        row['payload_state'] = 'missing'
        if component is None:
            continue
        header = component.get('header') or {}
        if not isinstance(header, dict):
            row['payload_state'] = 'invalid'
            continue
        for key in ('test_name', 'split', 'control_label', 'test_label'):
            row[key] = declaration.get(key) or text(header.get(key))
        row['generated_at'] = stamp
        row['framework_version'] = text(payload.get('_framework_version'))
        try:
            body, mode = _active_body(component)
        except (TypeError, ValueError):
            row['payload_state'] = 'invalid'
            continue
        row['mode_label'] = text(mode)
        row['payload_state'] = 'ready'
        if not body:
            continue
        kpis = body.get('kpi_rows') or []
        if not isinstance(kpis, list) or any(not isinstance(k, dict) for k in kpis):
            row['payload_state'] = 'invalid'
            continue
        if len(kpis) > MAX_KPIS:
            raise ProjectionOversized('KPI count exceeded')
        if not kpis:
            continue
        wanted = str(declaration.get('primary_metric') or '').strip().lower()
        primary = next((k for k in kpis if wanted and wanted in (str(k.get('key') or '').lower(), str(k.get('metric') or '').lower())), kpis[0])
        row.update(metric=text(primary.get('metric')), control_val=text(primary.get('control_val'),500),
                   test_val=text(primary.get('test_val'),500), higher_is_better=bool(primary.get('higher_is_better',True)))
        delta = primary.get('delta_pct')
        if finite(delta):
            row['delta_pct'] = delta
        elif delta is not None:
            row['payload_state'] = 'invalid'
        ci = primary.get('ci_pct')
        if ci is not None:
            if isinstance(ci,(list,tuple)) and len(ci)==2 and all(finite(v) for v in ci) and ci[0]<=ci[1]:
                row.update(ci_low=ci[0],ci_high=ci[1],significant=not(ci[0]<=0<=ci[1]))
            else:
                row['payload_state'] = 'invalid'
                row['delta_pct'] = None
    if len(json.dumps(rows,default=str).encode()) > PROJECTION_BYTES:
        raise ProjectionOversized('normalized result byte limit exceeded')
    return rows, digest
