"""Private validation for DataTable's existing-dropdown row action."""

from collections.abc import Mapping


def drill_down_config(option, df, displayed):
    if option is None:
        return None
    required = ("dataset_id", "column", "value_column", "label_column")
    if not isinstance(option, Mapping) or not set(required) <= option.keys() or option.keys() - set(required) - {"context"}:
        raise ValueError("DataTable drill_down requires dataset_id, column, value_column, label_column and optional context")
    if any(not isinstance(option[k], str) or not option[k].strip() for k in required):
        raise ValueError("DataTable drill_down names must be nonempty strings")
    if option["value_column"] not in df.columns:
        raise ValueError("DataTable drill_down value_column must name a supplied raw column")
    if option["label_column"] not in df.columns or option["label_column"] not in displayed:
        raise ValueError("DataTable drill_down label_column must name a supplied displayed column")
    context = option.get("context", [{"column": option["column"], "label": option["column"]}])
    if not isinstance(context, list):
        raise ValueError("DataTable drill_down context must be a list")
    seen = set()
    copied = []
    for item in context:
        if (not isinstance(item, Mapping) or set(item) != {"column", "label"}
                or any(not isinstance(item[k], str) or not item[k].strip() for k in ("column", "label"))
                or item["column"] in seen):
            raise ValueError("DataTable drill_down context requires unique columns and nonempty column/label strings")
        seen.add(item["column"])
        copied.append(dict(item))
    return {**{key: option[key] for key in required}, "context": copied}


def raw_action_key(value):
    """Never turn formatted labels, numbers or missing values into identities."""
    return value if isinstance(value, str) and value.strip() else None
