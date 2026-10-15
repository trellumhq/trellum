"""Opt-in bounded native checkbox transport; packets confer no authority."""
import json

from django import forms
from django.forms.renderers import get_default_renderer
from django.template.loader import get_template
from django.utils.safestring import mark_safe

from apps.core.choice_requests import INLINE_CHOICE_LIMIT


class CompactSelection(list):
    def __init__(self, values=(), *, error=None, packet=None):
        super().__init__(values)
        self.error = error
        self.packet = packet


def decode_compact_selection(data, name):
    submitted = data.getlist(name) if hasattr(data, "getlist") else data.get(name, [])
    if not isinstance(submitted, (list, tuple)):
        submitted = [submitted]
    submitted = [str(value) for value in submitted]
    key = name + "__selection"
    if key not in data:
        return CompactSelection(submitted)
    raw = data.get(key)
    try:
        if hasattr(data, "getlist") and len(data.getlist(key)) != 1:
            raise ValueError
        if not isinstance(raw, str) or len(raw.encode("utf-8")) > 1024 * 1024:
            raise ValueError
        packet = json.loads(raw)
        if not isinstance(packet, dict) or set(packet) != {"version", "selected", "editable"} or type(packet["version"]) is not int or packet["version"] != 1:
            raise ValueError
        for values in (packet["selected"], packet["editable"], submitted):
            if not isinstance(values, list) or any(not isinstance(value, str) or not value.isascii() or not value.isdecimal() or len(value) > 19 or int(value) < 1 or int(value) > 9223372036854775807 for value in values):
                raise ValueError
        if len(packet["editable"]) > INLINE_CHOICE_LIMIT:
            raise ValueError
        editable = set(packet["editable"])
        values = dict.fromkeys(value for value in packet["selected"] if value not in editable)
        values.update(dict.fromkeys(submitted))
        return CompactSelection(values)
    except (ValueError, TypeError, KeyError, RecursionError):
        return CompactSelection(submitted, error="The report selection could not be read. Review the selection and try again.", packet=raw if isinstance(raw, str) and len(raw) <= 1024 * 1024 else "Invalid oversized selection packet")


class CompactCheckboxSelectMultiple(forms.CheckboxSelectMultiple):
    template_name = "widgets/compact_checkbox_select.html"
    page_values = None
    removal_urls = None

    def _render(self, template_name, context, renderer=None):
        renderer = renderer or get_default_renderer()
        if template_name != self.template_name:
            return mark_safe(renderer.render(template_name, context))
        context["widget"]["checkbox_fragment"] = mark_safe(
            renderer.render("django/forms/widgets/multiple_input.html", context)
        )
        return mark_safe(get_template(template_name).render(context))

    def value_from_datadict(self, data, files, name):
        return decode_compact_selection(data, name)

    def get_context(self, name, value, attrs):
        selected = list(dict.fromkeys(str(item) for item in (value or [])))
        choices = list(self.choices)
        editable = {str(item) for item in self.page_values} if self.page_values is not None else {str(item[0]) for item in choices[:INLINE_CHOICE_LIMIT]}
        original = self.choices
        try:
            self.choices = [(identity, label) for identity, label in choices if str(identity) in editable][:INLINE_CHOICE_LIMIT]
            context = super().get_context(name, selected, attrs)
        finally:
            self.choices = original
        widget = context["widget"]
        editable = [str(item[0]) for item in choices if str(item[0]) in editable][:INLINE_CHOICE_LIMIT]
        labels = {str(identity): str(label) for identity, label in choices}
        widget["selection_name"] = name + "__selection"
        widget["selection_packet"] = value.packet if isinstance(value, CompactSelection) and value.error else json.dumps({"version": 1, "selected": selected, "editable": editable}, separators=(",", ":"))
        widget["selected_rows"] = [{"value": identity, "label": labels.get(identity, "Selected report is unavailable"), "remove_url": (self.removal_urls or {}).get(identity, "")} for identity in selected]
        editable_set = set(editable)
        widget["selected_offpage"] = [row for row in widget["selected_rows"] if row["value"] not in editable_set]
        widget["selection_invalid"] = isinstance(value, CompactSelection) and bool(value.error)
        return context

    def format_value(self, value):
        # Preserve malformed packet metadata for a reviewable validation rerender.
        return value if isinstance(value, CompactSelection) else super().format_value(value)
