"""Registry input bounds and field diagnostics, independent of metric objects."""

import json


def check_document_budget(raw: object):
    """Check decoded JSON incrementally without a large intermediate encoding."""
    nodes = size = 0

    def check(value, depth=0):
        nonlocal nodes, size
        nodes += 1
        if nodes > 5000 or depth > 12:
            raise ValueError("Registry document exceeds complexity limits.")
        if type(value) is dict:
            size += 2 + max(0, len(value) - 1) * 2 + len(value) * 2
        elif type(value) is list:
            size += 2 + max(0, len(value) - 1) * 2
        elif value is None or type(value) in (str, int, float, bool):
            if type(value) is str and len(value) > 64 * 1024:
                raise ValueError("Registry document exceeds byte limits.")
            size += len(json.dumps(value, allow_nan=False).encode("utf-8"))
        else:
            raise ValueError("Registry document must contain JSON values.")
        if size > 64 * 1024:
            raise ValueError("Registry document exceeds byte limits.")
        if type(value) is dict:
            for key, item in value.items():
                if type(key) is not str:
                    raise ValueError("Registry document keys must be strings.")
                check(key, depth + 1)
                check(item, depth + 1)
        elif type(value) is list:
            for item in value:
                check(item, depth + 1)

    check(raw)


def check_aggregation(entry, name, agg, problems, valid_aggs):
    """Record aggregation diagnostics before operand normalization."""
    column = entry.get("column")
    numerator = entry.get("numerator")
    denominator = entry.get("denominator")
    if agg is not None:
        if agg not in valid_aggs:
            problems.append(
                ("fail", f"metric '{name}' agg '{agg}' is not one of "
                         f"{sorted(valid_aggs)}.")
            )
        elif agg == "ratio":
            for key, val in (("numerator", numerator),
                             ("denominator", denominator)):
                if not val:
                    problems.append(
                        ("fail", f"metric '{name}' agg 'ratio' is missing "
                                 f"`{key}`.")
                    )
        elif agg in ("sum", "abssum") and not column:
            problems.append(
                ("fail", f"metric '{name}' agg '{agg}' is missing `column`.")
            )


def parse_metadata(entry, name, problems):
    """Normalize prose and version fields, recording missing or invalid values."""
    label = str(entry.get("label") or "")
    description = str(entry.get("description") or "").strip()
    if not label:
        problems.append(
            ("warn", f"metric '{name}' has no `label`; claims will fall "
                     f"back to the raw id as the card title.")
        )
    if not description:
        problems.append(
            ("warn", f"metric '{name}' has no `description`. The definition "
                     f"is the point of this file -- state what the number "
                     f"means, at what grain, and what it includes.")
        )

    try:
        version = int(entry.get("version", 1))
    except (TypeError, ValueError):
        problems.append(
            ("fail", f"metric '{name}' version {entry.get('version')!r} "
                     f"must be an integer.")
        )
        version = 1

    return label, description, version


def check_execution_binding(execution, bound, name, problems):
    """Check explicit execution grain and dimensions against the bound dataset."""
    if execution is not None and bound is not None:
        if (not isinstance(execution, dict) or execution.get("grain") != bound.grain
                or not isinstance(execution.get("dimensions"), list)
                or any(not isinstance(d, str) for d in execution["dimensions"])
                or set(execution["dimensions"]) != set(bound.dimensions)):
            problems.append(("fail", f"metric '{name}' execution grain/dimensions differ from its dataset."))
