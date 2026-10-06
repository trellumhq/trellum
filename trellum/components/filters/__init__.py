"""Filter plugin registry.

Register filter types with ``@register_filter`` and look them up with
``get_filter(type_name)``.  Built-in filters are imported at the bottom
so they self-register on first import.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from trellum.components.filters.base import BaseFilter

_FILTER_REGISTRY: dict[str, type[BaseFilter]] = {}


def register_filter(cls: type[BaseFilter]) -> type[BaseFilter]:
    """Class decorator that registers a filter plugin by its ``filter_type``."""
    _FILTER_REGISTRY[cls.filter_type] = cls
    return cls


def get_filter(filter_type: str) -> BaseFilter:
    """Instantiate and return a filter plugin by type name."""
    cls = _FILTER_REGISTRY.get(filter_type)
    if cls is None:
        raise ValueError(
            f"Unknown filter type {filter_type!r}. "
            f"Registered types: {sorted(_FILTER_REGISTRY)}"
        )
    return cls()


def get_all_filters() -> list[type[BaseFilter]]:
    """Return all registered filter plugin classes."""
    return list(_FILTER_REGISTRY.values())


# Import built-in filters so they self-register on module load.
from trellum.components.filters.date_range import DateRangeFilter  # noqa: E402, F401
from trellum.components.filters.dropdown import DropdownFilter  # noqa: E402, F401
from trellum.components.filters.flag import FlagFilter  # noqa: E402, F401
from trellum.components.filters.slider import SliderFilter  # noqa: E402, F401
from trellum.components.filters.text import TextFilter  # noqa: E402, F401
from trellum.components.filters.toggle import ToggleFilter  # noqa: E402, F401
