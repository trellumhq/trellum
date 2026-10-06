"""Base class for filter plugins.

Each filter type (dropdown, toggle, flag, date_range) is a subclass of
``BaseFilter`` that knows how to render its own HTML, CSS, and client JS.
The ``FilterBar`` orchestrator delegates to these plugins via the registry
in ``__init__.py``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict

import pandas as pd


class BaseFilter(ABC):
    """Abstract base for filter plugins.

    Subclasses must set ``filter_type`` and implement the abstract methods.
    """

    filter_type: str = ""

    @abstractmethod
    def build_config(self, fid: str, col: str, f: Dict[str, Any], df: pd.DataFrame) -> dict:
        """Return the config dict written to ``data.json`` for this filter."""

    @abstractmethod
    def render_html(self, fid: str, col: str, label: str, f: Dict[str, Any], df: pd.DataFrame) -> str:
        """Return the HTML fragment for this filter control."""

    @classmethod
    def css(cls) -> str:
        """CSS rules specific to this filter type."""
        return ""

    @classmethod
    def client_js(cls) -> str:
        """JS handler for initialization and interaction.

        The function receives ``(el, fc, dsId, helpers)`` where:
        - ``el``: the FilterBar DOM element
        - ``fc``: this filter's config from ``data.json``
        - ``dsId``: the dataset ID
        - ``helpers``: ``{setFilter, setFilterTransient, propagate,
          propagateTransient, urlWrite, findFc, urlLookup}`` --
          ``setFilterTransient``/``propagateTransient`` are for mid-drag
          ticks only (the slider ``slide`` path): they record state for the
          readout/eventual URL write but never trigger a live-dataset query
          and never re-notify subscribers of one, unlike ``setFilter``/
          ``propagate`` which commit (auto-query on a live dataset, local
          re-filter + notify on a normal one). A plugin whose events are
          all real commits (dropdown, toggle, flag, date_range, text) only
          ever needs ``setFilter``/``propagate``.
        """
        return ""

    @classmethod
    def cdn_deps(cls) -> list[str]:
        """CDN dependency keys needed by this filter type."""
        return []
