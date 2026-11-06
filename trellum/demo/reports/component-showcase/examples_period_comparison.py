"""Complete synthetic UTC calendar activity, with independent platform filtering."""
from datetime import date
import calendar

import pandas as pd

from trellum.components import (ComparisonTable, DataSource, DataTable, FilterBar, Grid, LineChart,
                                Panel, RawHTML, Section, TabGroup, Toggle, Visible)
from trellum.components.period_comparison import PeriodComparison

SOURCE = 'showcase_period_facts'
RESULT = 'showcase_period_comparison'
PERIOD = 'showcase_period'
CONTEXT = 'showcase_period_context'
PRESETS = {
    'WoW': {'A': ['2026-10-22', '2026-10-28'], 'B': ['2026-10-15', '2026-10-21']},
    'MoM': {'A': ['2026-10-01', '2026-10-31'], 'B': ['2026-09-01', '2026-09-30']},
    'YoY': {'A': ['2026-10-01', '2026-10-31'], 'B': ['2025-10-01', '2025-10-31']},
}
GROUPS = {'spend': 'Spend', 'volume': 'Volume', 'revenue': 'Revenue', 'efficiency': 'Efficiency'}


def metric(name, group, definition, fmt='number', direction='higher', daily=False, change='relative'):
    return {'name': name, 'group': group, 'definition': definition, 'format': fmt, 'direction': direction,
            'precision': 2, 'change_precision': 2, 'daily': daily, 'change_measure': change}


METRICS = {
    'cost': metric('Cost (USD)', 'spend', {'agg': 'sum', 'column': 'cost'}, 'currency', 'neutral', True),
    'daily_cost': metric('Daily cost (USD/day)', 'spend', {'agg': 'calendar_mean', 'column': 'cost'}, 'currency', 'neutral'),
    'cpi': metric('CPI (USD/install)', 'spend', {'agg': 'ratio', 'numerator': 'cost', 'denominator': 'installs'}, 'currency', 'lower'),
    'conversions': metric('Conversions (installs)', 'volume', {'agg': 'sum', 'column': 'installs'}, daily=True),
    'daily_conversions': metric('Daily conversions', 'volume', {'agg': 'calendar_mean', 'column': 'installs'}),
    'revenue': metric('Revenue (USD)', 'revenue', {'agg': 'sum', 'column': 'revenue'}, 'currency', daily=True),
    'daily_revenue': metric('Daily revenue (USD/day)', 'revenue', {'agg': 'calendar_mean', 'column': 'revenue'}, 'currency'),
    'recovery': metric('Recovery', 'efficiency', {'agg': 'ratio', 'numerator': 'revenue', 'denominator': 'cost'}, 'percent', daily=True),
    'click_conversion': metric('Click conversion', 'efficiency', {'agg': 'ratio', 'numerator': 'installs', 'denominator': 'clicks'}, 'percent', change='percentage_points'),
}


def facts():
    rows = []
    for year, month in ((2025, 10), (2026, 9), (2026, 10)):
        current = (year, month) == (2026, 10)
        for day in range(1, calendar.monthrange(year, month)[1] + 1):
            u = (100 if current else 80) + day
            for platform, installs, cost, revenue, clicks in (
                ('iOS', u, 2*u, (3 if current else 2.5)*u, (5 if current else 6)*u),
                ('Android', 2*u, 2*u, (4 if current else 3)*u, (8 if current else 10)*u),
            ):
                rows.append({'date': date(year, month, day).isoformat(), 'platform': platform,
                             'installs': installs, 'cost': cost, 'revenue': revenue, 'clicks': clicks})
    return pd.DataFrame(rows)


def projection():
    return PeriodComparison(RESULT, parent=SOURCE, toggle_id=PERIOD, default='MoM', date_column='date',
                            presets=PRESETS, groups=GROUPS, metrics=METRICS)


def add_sections(ctx):
    frame, view = facts(), projection()
    def table(title, dataset=RESULT):
        return ComparisonTable(view.empty_frame(), title, 'A', ['change'], dataset_id=dataset,
                               columns=['name', 'A', 'B', 'change'], comparison_context=view.comparison_context(), row_limit='all')
    def charts(context):
        output = []
        for key, title, fmt in [('cost', 'Daily cost (USD)', 'currency'), ('conversions', 'Daily conversions (installs)', 'number'),
                                ('revenue', 'Daily revenue (USD)', 'currency'), ('recovery', 'Daily recovery', 'percent')]:
            output.append(LineChart(view.empty_frame(daily=True), 'day', [key+'_A', key+'_B'], title,
                                    y_labels=['A', 'B'], dataset_id=RESULT+'_daily', y_format=fmt,
                                    point_context=[{'series': i, 'date_column': side+'_date', 'window_column': side+'_window'}
                                                   for i, side in enumerate(('A', 'B'))] if context else None,
                                    series_availability=[{'series': i, 'required_columns': [key+'_'+side],
                                                          'state_column': key+'_'+side+'_state',
                                                          'reason': 'Outside-period positions remain gaps; absent calendar days are incomplete.'}
                                                         for i, side in enumerate(('A', 'B'))]))
        return Grid(output, columns=2, min_width=280)
    contexts = [Visible([Panel([RawHTML(
        f'<p>A: {preset["A"][0]} through {preset["A"][1]} inclusive · '
        f'B: {preset["B"][0]} through {preset["B"][1]} inclusive · UTC calendar activity.</p>'
    )], title=name + ' original windows')], PERIOD, name) for name, preset in PRESETS.items()]
    ctx.add_section('Period comparison', [
        DataSource(SOURCE, frame), FilterBar(SOURCE, frame, filters=[{'column': 'platform', 'type': 'toggle'}]),
        Toggle(PERIOD, list(PRESETS), 'MoM'), view,
        RawHTML('<p>Synthetic complete daily activity · USD · two platforms per date. Calendar means divide by each window’s inclusive days. '
                'Revenue is calendar activity, not acquisition-cohort revenue; installs are not unique people. '
                'Aligned days represent positions inside each independent window. September has no Day 31: its B value is a gap, never zero or forecast.</p>'),
        TabGroup([{'label': 'Flat comparison', 'content': [table('All nine metrics')]},
                  {'label': 'Grouped comparison', 'content': [Grid([
                      Panel([table(label, RESULT+'_group_'+key)], title=label) for key, label in GROUPS.items()
                  ], columns=2, min_width=280)]}], id='showcase_period_layout'),
        Section('All metrics CSV', [table('All nine metrics export')], collapsible=True, default_collapsed=True),
        RawHTML('<p>Original A/B windows and hover dates (optional):</p>'),
        TabGroup([{'label': 'Original dates off', 'content': [charts(False)]},
                  {'label': 'Original dates on', 'content': [*contexts, charts(True)]}], id=CONTEXT),
        Section('Selected calendar source facts and CSV', [DataTable(frame, 'Selected daily facts', dataset_id=SOURCE)],
                collapsible=True, default_collapsed=True),
    ])
