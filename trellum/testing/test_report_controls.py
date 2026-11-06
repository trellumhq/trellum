"""Control author APIs, escaping, asset isolation and real showcase validation."""

from dataclasses import dataclass
import importlib.util
import inspect
from pathlib import Path

from trellum.components import Dropdown, TabGroup, RawHTML
from trellum.components.base import RenderContext
from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.themes import Theme
from trellum.validation import validate_report


def example_context(output):
    path = Path(__file__).resolve().parents[1] / 'demo/reports/component-showcase/examples_controls.py'
    spec = importlib.util.spec_from_file_location('examples_controls', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    ctx = ReportContext(config={
        'name': 'Report controls', 'studio': 'test', 'category': 'Test',
        'description': 'Synthetic daily revenue and order counts by platform, with standalone choices and report view navigation.',
    }, slug='controls', output_dir=str(output))
    module.add_sections(ctx)
    return ctx


def test_control_positions_and_subclasses():
    @dataclass
    class NamedDropdown(Dropdown):
        name: str = ''

    @dataclass
    class NamedTabs(TabGroup):
        name: str = ''

    dropdown = NamedDropdown('choice', ['a', 'b'], 'b', 'caption', label='Metric')
    tabs = NamedTabs([], 'views', 'legacy', 'caption', label='View')
    assert (dropdown.id, dropdown.default, dropdown.name, dropdown.label) == ('choice', 'b', 'caption', 'Metric')
    assert (tabs.id, tabs.mode, tabs.name, tabs.label) == ('views', 'legacy', 'caption', 'View')
    for cls in (Dropdown, TabGroup):
        assert inspect.signature(cls).parameters['label'].kind is inspect.Parameter.KEYWORD_ONLY


def test_control_text_and_attribute_escaping():
    ctx = RenderContext(Theme())
    danger = '\"><img src=x onerror=alert(1)>&'
    html = Dropdown(danger, [danger], danger, label=danger).render_html(ctx)
    assert '<img' not in html and '&lt;img' in html
    assert html.count(' selected') == 1
    html = TabGroup([{'label': danger, 'content': [RawHTML('<p>Real panel</p>')]}], danger, label=danger).render_html(ctx)
    assert '<img' not in html and '<p>Real panel</p>' in html
    assert 'role="tablist"' in html and 'aria-controls=' in html


def test_empty_and_legacy_mode():
    ctx = RenderContext(Theme())
    empty = Dropdown('empty', []).render_html(ctx)
    assert ' disabled' in empty and 'No choices available' in empty
    assert 'No report views available' in TabGroup([]).render_html(ctx)
    assert 'data-tab-mode="tabs"' in TabGroup([{'label': 'A'}], mode='historical-mode').render_html(ctx)
    assert 'data-fw-choice' in TabGroup([{'label': 'A'}], mode='dropdown').render_html(ctx)
    assert Dropdown.cdn_deps() == TabGroup.cdn_deps() == ['slim_select_js', 'slim_select_css']


def test_showcase_controls_validate_without_warnings(tmp_path):
    result = validate_report(ctx := example_context(tmp_path), details=compute_details(ctx))
    assert not [c for c in result.checks if c.level in ('fail', 'warn')], result.checks
