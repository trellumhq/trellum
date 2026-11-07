"""Independent, fully observed synthetic acquisition-cohort geography example."""
import pandas as pd

from trellum.components import (
    DataSource,
    DataTable,
    FilterBar,
    Grid,
    MetricView,
    MiniKpi,
    Panel,
    RawHTML,
    Section,
    TabGroup,
)

SOURCE = 'showcase_marketing_facts'
RESULT = 'showcase_marketing_geography'
GROUPS = [
    {'id': 'worldwide', 'label': 'Worldwide'},
    {'id': 'tier1', 'label': 'Tier 1', 'where': {'column': 'country', 'values': ['US', 'GB', 'DE']}},
    {'id': 'us', 'label': 'US', 'where': {'column': 'country', 'values': ['US']}},
    {'id': 'uk', 'label': 'UK', 'where': {'column': 'country', 'values': ['GB']}},
]
METRICS = {**{key: {'agg': 'sum', 'column': key} for key in ('installs', 'cost', 'd7_revenue', 'd30_revenue', 'd7_retained')},
           'cpi': {'agg': 'ratio', 'numerator': 'cost', 'denominator': 'installs'},
           'd30_recovery': {'agg': 'ratio', 'numerator': 'd30_revenue', 'denominator': 'cost'},
           'd7_retention': {'agg': 'ratio', 'numerator': 'd7_retained', 'denominator': 'installs'},
           'facts': {'agg': 'count'}}


def facts():
    bases = {'US': (100, 200, 150, 250, 30), 'GB': (80, 120, 100, 140, 20),
             'DE': (60, 60, 48, 72, 12), 'BR': (40, 100, 40, 60, 4)}
    rows = []
    for day in (1, 2):
        for country, values in bases.items():
            for platform, multiplier in (('iOS', 1), ('Android', 2)):
                rows.append({'cohort_date': f'2026-08-{24 + day}', 'country': country, 'platform': platform,
                             **{key: value * day * multiplier for key, value in zip(
                                 ('installs', 'cost', 'd7_revenue', 'd30_revenue', 'd7_retained'), values)}})
    return pd.DataFrame(rows)


def add_sections(ctx):
    frame = facts()
    view = MetricView(RESULT, parent=SOURCE, groups=GROUPS, metrics=METRICS)
    panels = []
    for group in GROUPS:
        children = [MiniKpi(label, format=fmt, stacked=True, full_number=True, precision=precision,
                            binding={'dataset_id': RESULT, 'group': group['id'], 'metric': key})
                    for key, label, fmt, precision in (
                        ('installs', 'Installs', 'number', 0), ('cost', 'Cost (USD)', 'currency', 2),
                        ('d7_revenue', 'D7 revenue (USD)', 'currency', 2), ('d30_revenue', 'D30 revenue (USD)', 'currency', 2),
                        ('d7_retained', 'D7 retained', 'number', 0), ('cpi', 'CPI (USD/install)', 'currency', 2),
                        ('d30_recovery', 'D30 recovery', 'percent', 2), ('d7_retention', 'D7 retention', 'percent', 2))]
        panels.append(Panel([Grid(children, columns=2, min_width=110)], title=group['label']))
    formats = {key: {'format': 'currency' if key in {'cost', 'd7_revenue', 'd30_revenue', 'cpi'} else
                                      'percent' if key in {'d30_recovery', 'd7_retention'} else 'number',
                     'precision': 2 if key in {'cost', 'd7_revenue', 'd30_revenue', 'cpi', 'd30_recovery', 'd7_retention'} else 0}
               for key in METRICS}
    def table(title):
        return DataTable(view.empty_frame(), title, dataset_id=RESULT, sortable=True, searchable=True,
                                    columns=['group_label', *METRICS, 'Notes'], column_formats=formats)
    ctx.add_section('Marketing geography', [
        DataSource(SOURCE, frame),
        FilterBar(SOURCE, frame, filters=[{'column': 'platform', 'type': 'toggle'},
                                        {'column': 'cohort_date', 'type': 'date_range'}, {'column': 'country'}]),
        view,
        RawHTML('<p>Synthetic acquisition cohorts · 25–26 August 2026 UTC · fully observed as of 5 November 2026 · USD. '
                'Worldwide includes US, GB, DE and BR; Tier 1 means US + GB + DE; UK means GB. '
                'Groups overlap: never add their summaries. Calculations describe delivered selected facts only.</p>'
                '<p>CPI = cost / installs; D30 recovery = D30 revenue / cost; D7 retention = D7 retained / installs. '
                'Ratios use sums; CSV ratios use fraction units. Summary search/sort changes its matching-row CSV, never panel totals.</p>'),
        TabGroup([{'label': 'Summary table', 'content': [table('Geography summary')]},
                  {'label': 'Geography panels', 'content': [Grid(panels, columns=4, min_width=230),
                       Section('Summary rows and CSV', [table('Geography panel summary export')], collapsible=True, default_collapsed=True)]}]),
        Section('Selected source facts and CSV', [DataTable(frame, 'Selected acquisition facts', dataset_id=SOURCE,
                  sortable=True, searchable=True)], collapsible=True, default_collapsed=True),
    ])
