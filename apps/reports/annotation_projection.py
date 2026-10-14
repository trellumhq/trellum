"""Bounded declaration normalization and atomic calendar publication.

This projection is independent of report builds. Failed observations retain the
last successful relation; consumers must check source status before counting it.
"""
from __future__ import annotations

import datetime
import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta

import yaml
from django.db import transaction
from django.utils import timezone
from yaml.nodes import MappingNode, ScalarNode

RAW_BYTES = 16 * 1024 * 1024
NORMALIZED_BYTES = 32 * 1024 * 1024
EXPANDED_BYTES = 64 * 1024 * 1024
MAX_EVENTS = 100000
MAX_NODES = 1500000
MAX_VISITS = 2000000
MAX_ALIASES = 100000
MAX_DEPTH = 32
SCHEMA_VERSION = 1
MISSING_DIGEST = hashlib.sha256(b"annotation-source:missing:v1").hexdigest()


class AdmissionError(Exception):
    def __init__(self, code="oversized"):
        self.code = code
        super().__init__(code)


@dataclass
class Declaration:
    digest: str
    events: list
    defaults: dict
    status: str = "ready"


def _date(value):
    if isinstance(value, datetime.datetime):
        return value.date()
    if isinstance(value, datetime.date):
        return value
    try:
        return datetime.date.fromisoformat(str(value).strip()[:10]) if value is not None else None
    except ValueError:
        return None


def _bounded_graph(value):
    """Count expanded objects, including repeated aliases, without copying them."""
    visits = scalar_bytes = 0
    active = set()
    stack = [(value, 0, False)]
    while stack:
        item, depth, leaving = stack.pop()
        container = isinstance(item, (dict, list, tuple))
        if leaving:
            active.remove(id(item))
            continue
        visits += 1
        if visits > MAX_VISITS or depth > MAX_DEPTH:
            raise AdmissionError()
        if container:
            if id(item) in active:
                raise AdmissionError()
            active.add(id(item))
            stack.append((item, depth, True))
            children = list(item.items()) if isinstance(item, dict) else item
            # Mapping pairs are visited explicitly, preserving key budgets.
            for child in reversed(children):
                stack.append((child, depth + 1, False))
        else:
            scalar_bytes += len(str(item).encode("utf-8"))
            if scalar_bytes > EXPANDED_BYTES:
                raise AdmissionError()


def load_yaml(raw):
    if len(raw) > RAW_BYTES:
        raise AdmissionError()
    try:
        text = raw.decode("utf-8")
        aliases = nodes = 0
        for event in yaml.parse(text, Loader=yaml.SafeLoader):
            if isinstance(event, yaml.events.AliasEvent):
                aliases += 1
            if isinstance(event, (yaml.events.ScalarEvent, yaml.events.SequenceStartEvent, yaml.events.MappingStartEvent)):
                nodes += 1
            if aliases > MAX_ALIASES or nodes > MAX_NODES:
                raise AdmissionError()
        loader = yaml.SafeLoader(text)
        try:
            node = loader.get_single_node()
            # Inspect composed graph before construction: aliases can amplify
            # merge mappings during SafeLoader construction itself.
            if node is not None:
                visits = byte_count = 0
                stack = [(node, 0, frozenset())]
                while stack:
                    current, depth, ancestors = stack.pop()
                    visits += 1
                    if visits > MAX_VISITS or depth > MAX_DEPTH or id(current) in ancestors:
                        raise AdmissionError()
                    if isinstance(current, ScalarNode):
                        byte_count += len(current.value.encode("utf-8"))
                        if byte_count > EXPANDED_BYTES:
                            raise AdmissionError()
                    else:
                        children = current.value
                        if isinstance(current, MappingNode):
                            children = [n for pair in children for n in pair]
                        lineage = ancestors | {id(current)}
                        stack.extend((child, depth + 1, lineage) for child in children)
                result = loader.construct_document(node)
            else:
                result = None
        finally:
            loader.dispose()
        _bounded_graph(result)
        return result
    except (yaml.YAMLError, UnicodeError, RecursionError) as exc:
        raise AdmissionError("parse-error") from exc


def _text(value, maximum):
    result = str(value)
    if len(result) > maximum:
        raise AdmissionError()
    return result


def normalize(value, *, local=False):
    if local:
        original = value if isinstance(value, dict) else {}
        annotations = original.get("annotations")
        annotations = {} if annotations is None else annotations
        if not isinstance(annotations, dict):
            raise AdmissionError("parse-error")
        inputs = annotations.get("events")
        inputs = [] if inputs is None else inputs
        config = {"annotations": {"events": inputs}}
        if "studio" in original:
            config["studio"] = original["studio"]
        _bounded_graph(config)
        defaults = {}
    else:
        _bounded_graph(value)
        if value is None:
            value = {}
        if not isinstance(value, dict):
            raise AdmissionError("parse-error")
        inputs = value.get("events")
        inputs = [] if inputs is None else inputs
        defaults = value.get("type_defaults")
        defaults = {} if defaults is None else defaults
    if not isinstance(inputs, list) or not isinstance(defaults, dict):
        raise AdmissionError("parse-error")
    if len(inputs) > MAX_EVENTS or len(defaults) > 1000:
        raise AdmissionError()
    defaults = {_text(k, 256): bool(v) for k, v in defaults.items()}
    if local:
        # Framework normalization preserves explicit empty scope and its local
        # default/type/date rules; admission occurs before allocating that list.
        from trellum.runner import local_annotation_events
        if any(not isinstance(item, dict) for item in inputs):
            raise AdmissionError("parse-error")
        normalized = local_annotation_events(config)
        # Recreate the framework's skipped-input ordinals rather than renumber.
        ordinals = [i for i, e in enumerate(inputs) if isinstance(e, dict) and e.get("date")]
        inputs = zip(ordinals, normalized)
    else:
        inputs = enumerate(inputs)
    events = []
    size = len(json.dumps(defaults, ensure_ascii=False).encode("utf-8"))
    for ordinal, event in inputs:
        if not isinstance(event, dict):
            continue
        start = _date(event.get("date"))
        if start is None:
            continue
        end = _date(event.get("end_date"))
        kind = _text(event.get("type") if local else event.get("type") or "release", 256)
        scope = _text(event.get("studio") if local else event.get("studio") or "shared", 256)
        label = _text(event.get("label") if local else event.get("label") or "", 2048)
        visible = bool(event.get("default_visible", defaults.get(kind, True)))
        row = dict(ordinal=ordinal, start_date=start, end_date=end, label=label, event_type=kind, scope=scope, visible_default=visible)
        size += len(json.dumps(row, default=str, ensure_ascii=False).encode("utf-8"))
        if size > NORMALIZED_BYTES:
            raise AdmissionError()
        events.append(row)
    return events, defaults


def declaration(raw):
    events, defaults = normalize(load_yaml(raw))
    return Declaration(hashlib.sha256(raw).hexdigest(), events, defaults)


def _catalog(studio_id):
    from .models import AnnotationCatalogState
    AnnotationCatalogState.objects.get_or_create(studio_id=studio_id)
    return AnnotationCatalogState.objects.select_for_update().get(studio_id=studio_id)


def _publish(source, catalog, result, *, error=""):
    from .models import AnnotationEvent
    now = timezone.now()
    changed = source.digest != result.digest or source.status != result.status or (not error and source.schema_version != SCHEMA_VERSION)
    if not error and changed:
        AnnotationEvent.objects.filter(source=source).delete()
        AnnotationEvent.objects.bulk_create([
            AnnotationEvent(source=source, studio_id=source.studio_id, generation=source.desired_generation, **event)
            for event in result.events
        ], batch_size=500)
        source.schema_version = SCHEMA_VERSION
        source.published_generation = source.desired_generation
        source.row_count = len(result.events)
        source.indexed_at = now
        if source.kind == "global":
            catalog.type_defaults = result.defaults
    source.attempted_schema_version = SCHEMA_VERSION
    source.digest = result.digest
    source.status = result.status
    source.error_code = error
    source.observed_at = now
    source.next_check_at = now + timedelta(seconds=60 if not error else min(300, 60 * (source.failures + 1)))
    source.failures = source.failures + 1 if error else 0
    source.lease_token = ""
    source.lease_until = None
    source.save()
    if changed:
        catalog.revision += 1
        catalog.published_revision = catalog.revision
        catalog.save()


def sync_report_declarations(report):
    from .models import AnnotationSourceState
    with transaction.atomic():
        catalog = _catalog(report.studio_id)
        source, _ = AnnotationSourceState.objects.get_or_create(studio_id=report.studio_id, report=report, kind="report")
        source = AnnotationSourceState.objects.select_for_update().get(pk=source.pk)
        try:
            events, defaults = normalize(report.config, local=True)
            digest = hashlib.sha256(json.dumps([events, defaults], default=str, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            result = Declaration(digest, events, defaults)
            error = ""
        except AdmissionError as exc:
            result = Declaration(source.digest, [], {}, exc.code)
            error = exc.code
        if result.digest != source.digest or result.status != source.status or (not error and source.schema_version != SCHEMA_VERSION):
            source.desired_generation += 1
        _publish(source, catalog, result, error=error)


def invalidate_report_declarations(report_ids):
    from .models import AnnotationSourceState
    studio_ids = AnnotationSourceState.objects.filter(report_id__in=report_ids).values_list("studio_id", flat=True).distinct()
    for studio_id in studio_ids:
        with transaction.atomic():
            catalog = _catalog(studio_id)
            changed = AnnotationSourceState.objects.filter(studio_id=studio_id, report_id__in=report_ids).exclude(status="removed").update(status="removed")
            if changed:
                catalog.revision += 1
                catalog.published_revision = catalog.revision
                catalog.save()
