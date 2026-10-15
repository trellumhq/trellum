"""Explicit single-report details, with bounded I/O and paged presentation."""
import gzip
import hashlib
import io
import json
import math
import os
import stat

from django.db.models import Func, JSONField
from django.db.models.functions import Length, Substr
from django.http import Http404

from apps.core import storage
from .catalog import PAGE_SIZE, bounded_payload, page_number, pagination
from .catalog_indicators import authorized_ids
from .models import ReportSourceSummary

META_BYTES = 2 * 1024 * 1024
RESPONSE_BYTES = 128 * 1024
TEXT_CHUNK = 600
PREVIEW_TEXT = 80


class DetailChanged(ValueError):
    pass


def _fence(report_id):
    return ReportSourceSummary.objects.filter(report_id=report_id).values("generation", "source_identity", "output_digest", "state").first()


def _descriptor_meta(studio, slug):
    """One descriptor read; metadata stability is observation, not atomic proof."""
    if not slug or slug in (".", "..") or os.path.basename(slug) != slug or "/" in slug or "\\" in slug:
        raise ValueError("Invalid report slug")
    directory = os.open(studio.output_dir / slug, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    descriptor = None
    try:
        descriptor = os.open("_meta.json", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise ValueError("Metadata is not a regular file")
        if before.st_size > META_BYTES:
            raise ValueError("Metadata byte limit exceeded")
        # Use one buffered bounded read, never reopen the pathname.
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            raw = source.read(META_BYTES + 1)
        after = os.fstat(descriptor)
        key = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns)
        if key(before) != key(after):
            raise DetailChanged("Metadata changed")
        return raw
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory)


def _metadata(report, before):
    remote = storage.is_remote()
    identity = ""
    if remote:
        budget = storage.SummaryReadBudget(META_BYTES + 16 * 1024)
        # Pointer helper has its own 4096-byte admission cap. Reserve both.
        budget.charge(8194, 8194)
        identity = storage.summary_build_identity(report.studio, report.slug)
        raw = storage.read_summary_artifact(report.studio, report.slug, "_meta.json", build=identity, max_bytes=META_BYTES, budget=budget) if identity else None
        if storage.summary_build_identity(report.studio, report.slug) != identity:
            raise DetailChanged("Serving identity changed")
        if identity and raw is None:
            raise ValueError("Referenced metadata unavailable")
    else:
        try:
            raw = _descriptor_meta(report.studio, report.slug)
        except FileNotFoundError:
            raw = None
        if raw and raw[:2] == b"\x1f\x8b":
            with gzip.GzipFile(fileobj=io.BytesIO(raw)) as source:
                raw = source.read(META_BYTES + 1)
        if raw is not None and len(raw) > META_BYTES:
            raise ValueError("Metadata byte limit exceeded")
        observed = "observed:" + hashlib.sha256(raw or b"").hexdigest()
        if before and before["source_identity"].startswith("observed:") and before["source_identity"] != observed:
            raise DetailChanged("Observation identity changed")
        identity = observed
    if _fence(report.pk) != before:
        raise DetailChanged("Summary generation changed")
    if raw is None:
        return None, identity, "verified" if remote else "observed"
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("Invalid metadata shape")
    return value, identity, "verified" if remote else "observed"


class TagPage(Func):
    output_field = JSONField()
    def as_sql(self, compiler, connection, **extra_context):
        field, args = compiler.compile(self.source_expressions[0])
        start, stop = self.extra["start"], self.extra["stop"]
        return f"(SELECT COALESCE(jsonb_agg(jsonb_build_object('index', n-1, 'text', left(tag,80), 'length', char_length(tag)) ORDER BY n),'[]'::jsonb) FROM jsonb_array_elements_text(CASE WHEN jsonb_typeof({field})='array' THEN {field} ELSE '[]'::jsonb END) WITH ORDINALITY AS t(tag,n) WHERE n > %s AND n <= %s)", (*args, *args, start, stop)


class TagText(Func):
    output_field = JSONField()
    def as_sql(self, compiler, connection, **extra_context):
        field, args = compiler.compile(self.source_expressions[0])
        return f"jsonb_build_object('text', substring({field}->>%s FROM %s FOR 600), 'length', char_length({field}->>%s))", (*args, self.extra["index"], self.extra["start"], *args, self.extra["index"])


def _declaration(relation, params):
    description_page = page_number(params.get("description_page"))
    tag_page = page_number(params.get("tag_page"))
    from .catalog import TagCount
    declaration = relation.annotate(description_text=Substr("description", (description_page-1)*TEXT_CHUNK+1, TEXT_CHUNK), description_count=Length("description"), tag_count=TagCount("tags"), tag_rows=TagPage("tags", start=(tag_page-1)*PAGE_SIZE, stop=tag_page*PAGE_SIZE)).values("description_text", "description_count", "tag_count", "tag_rows").get()
    declaration["description_pagination"] = dict(page=description_page, per_page=TEXT_CHUNK, total=declaration["description_count"], has_previous=description_page > 1, has_next=description_page*TEXT_CHUNK < declaration["description_count"])
    declaration["tag_pagination"] = pagination(tag_page, declaration["tag_count"])
    if params.get("tag_index") is not None:
        try:
            index = int(params["tag_index"])
        except (TypeError, ValueError):
            raise ValueError("Invalid tag") from None
        if not 0 <= index < declaration["tag_count"]:
            raise ValueError("Invalid tag")
        text_page = page_number(params.get("text_page"))
        declaration["tag_text"] = relation.annotate(text=TagText("tags", index=index, start=(text_page-1)*TEXT_CHUNK+1)).values_list("text", flat=True).get()
        declaration["tag_text"]["page"] = text_page
        declaration["tag_text"]["has_next"] = text_page*TEXT_CHUNK < declaration["tag_text"]["length"]
    return declaration


def _detail_entries(meta, section):
    validation = meta.get("validation") or {}
    details = meta.get("details") or meta.get("health") or {}
    if not isinstance(validation, dict) or not isinstance(details, dict):
        raise ValueError("Invalid detail shape")
    if section == "checks":
        rows = validation.get("checks", [])
        fields = ("id", "level", "message", "component", "section", "dataset_id", "suppressed")
    elif section == "charts":
        rows = details.get("filter_matrix", [])
        fields = ("scope", "chart_title", "chart_kind", "dataset_id")
    elif section == "filters":
        matrix = details.get("filter_matrix", [])
        if not isinstance(matrix, list) or any(not isinstance(r, dict) or not isinstance(r.get("filters", []), list) or any(not isinstance(f, dict) for f in r.get("filters", [])) for r in matrix):
            raise ValueError("Invalid filter shape")
        rows = [dict(f, scope=r.get("scope", ""), chart_title=r.get("chart_title", "")) for r in matrix for f in r.get("filters", []) if isinstance(f, dict)]
        fields = ("scope", "chart_title", "column", "target_column", "status", "reason", "suppressed")
    else:
        raise ValueError("Invalid detail section")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Invalid detail entries")
    return [{key: row[key] for key in fields if key in row and isinstance(row[key], (str, int, float, bool))} for row in rows]


def catalog_details_payload(request, report_id, params):
    for key, limit in (("q", 1000), ("section", 10), ("field", 32)):
        if params.get(key) is not None and (not isinstance(params[key], str) or len(params[key]) > limit):
            raise ValueError("Invalid detail state")
    if params.get("section", "checks") not in ("checks", "charts", "filters") or params.get("metadata", "1") not in ("0", "1"):
        raise ValueError("Invalid detail state")
    if params.get("field", "message") not in ("id", "level", "message", "component", "section", "dataset_id", "scope", "chart_title", "chart_kind", "column", "target_column", "status", "reason"):
        raise ValueError("Invalid detail field")
    for key in ("page", "description_page", "tag_page", "text_page"):
        page_number(params.get(key))
    for key in ("entry", "tag_index"):
        if params.get(key) is not None:
            value = params[key]
            if not isinstance(value, str) or not value.isascii() or not value.isdigit() or len(value) > 10:
                raise ValueError("Invalid detail selection")
            if int(value) > 2147483647:
                raise ValueError("Invalid detail selection")
    relation = authorized_ids(request, [report_id])
    report = relation.select_related("studio__org").only("pk", "slug", "studio__id", "studio__slug", "studio__org_id", "studio__org__id", "studio__org__slug").first()
    if report is None:
        raise Http404
    result = dict(version=1, report_id=report_id, declaration=_declaration(relation, params))
    if params.get("metadata", "1") == "0":
        return bounded_payload(result, RESPONSE_BYTES)
    before = _fence(report_id)
    try:
        meta, identity, quality = _metadata(report, before)
        result.update(state="missing" if meta is None else "ready", identity=identity, quality=quality)
        if meta is not None:
            section = params.get("section", "checks")
            entries = _detail_entries(meta, section)
            q = str(params.get("q", "")).lower()
            indexed = [(i, row) for i, row in enumerate(entries) if not q or q in " ".join(str(v) for v in row.values()).lower()]
            page = page_number(params.get("page"))
            if params.get("entry") is not None:
                index = int(params["entry"])
                field = str(params.get("field", "message"))
                if not 0 <= index < len(entries) or field not in entries[index] or not isinstance(entries[index][field], str):
                    raise ValueError("Invalid detail field")
                text_page = page_number(params.get("text_page"))
                text = entries[index][field]
                result["entry_text"] = dict(entry=index, field=field, text=text[(text_page-1)*TEXT_CHUNK:text_page*TEXT_CHUNK], page=text_page, length=len(text), has_next=text_page*TEXT_CHUNK < len(text))
            else:
                rows = []
                for index, row in indexed[(page-1)*PAGE_SIZE:page*PAGE_SIZE]:
                    preview = {k: v[:PREVIEW_TEXT] if isinstance(v, str) else v for k, v in row.items() if not isinstance(v, float) or math.isfinite(v)}
                    preview["index"] = index
                    preview["more_fields"] = [k for k, v in row.items() if isinstance(v, str) and len(v) > PREVIEW_TEXT]
                    rows.append(preview)
                result.update(section=section, entries=rows, pagination=pagination(page, len(indexed)))
    except DetailChanged:
        result.update(state="changed", entries=[])
    except Exception as exc:
        # Provider/path/credential exception details never reach the response.
        result.update(state="oversized" if isinstance(exc, ValueError) and "byte limit" in str(exc) else "unavailable", entries=[])
    return bounded_payload(result, RESPONSE_BYTES)
