"""Bounded synthetic observations through the registered live-query path."""
from trellum import BaseReport
from trellum.components import (
    DataTable,
    FilterBar,
    KpiRow,
    LineChart,
    LiveDataSource,
    RawHTML,
    ScopedDataSource,
)

# Literal rows keep this teaching query independent of source tables/fixtures.
# Each row is one synthetic platform/day observation, with value in USD.
SYNTHETIC_SQL = """
SELECT selection, day, platform, value FROM (
    SELECT 'Current' AS selection, '2026-10-01' AS day, 'iOS' AS platform, 10 AS value
    UNION ALL SELECT 'Current', '2026-10-02', 'Web', 25
    UNION ALL SELECT 'Current', '2026-10-03', 'iOS', 0
    UNION ALL SELECT 'Alternative', '2026-10-01', 'iOS', 40
    UNION ALL SELECT 'Alternative', '2026-10-02', 'Web', 60
    UNION ALL SELECT 'Alternative', '2026-10-03', 'iOS', 20
) AS synthetic WHERE selection = :selection ORDER BY day
"""


class LiveResultExamplesReport(BaseReport):
    def generate(self, ctx):
        frame = ctx.declare_live_query(
            'live_result_example', SYNTHETIC_SQL, datasource='demo_db',
            params=[{'name': 'selection', 'type': 'enum', 'required': True,
                     'values': ['Current', 'Alternative', 'Empty']}],
            snapshot_params={'selection': 'Current'},
        )
        ctx.set_header(subtitle='Synthetic observations · displayed selection and returned-row scope')
        ctx.add_section('Reading these results', [RawHTML(html=(
            '<p>Every row is one synthetic platform/day observation. Value is in USD; '
            'Returned value = sum(value), and Observations = returned row count. '
            'The line sums value by day. The fixed build snapshot is Current.</p>'
            '<p>Standalone and shared pages keep disabled live controls and show '
            'the build snapshot with unknown completeness. A serving host with '
            'live queries enabled runs the registered query for Current, Alternative '
            'or Empty. Empty is a valid result, distinct from a missing result.</p>'
            '<p>Loading, failure and limited-result context appears only when actual '
            'responses have those states. Returned rows may be bounded by a host or '
            'authored SQL; they do not establish entire warehouse totals. Response '
            'receipt time does not establish source freshness.</p>'
        ))])
        ctx.add_section('', [
            LiveDataSource('live_result_rows', query='live_result_example', df=frame),
            FilterBar('live_result_rows', frame, filters=[
                {'type': 'toggle', 'column': 'selection', 'param': 'selection',
                 'label': 'Selection', 'default': 'Current', 'no_all': True,
                 'options': ['Current', 'Alternative', 'Empty']},
            ]),
        ])
        ctx.add_section('Returned observations', [
            KpiRow([{'label': 'Returned value (USD)', 'column': 'value', 'agg': 'sum', 'format': 'currency'},
                    {'label': 'Observations', 'agg': 'count'}], dataset_id='live_result_rows'),
            LineChart(frame, x='day', y='value', title='Returned value by day (USD)',
                      y_format='currency', dataset_id='live_result_rows'),
            DataTable(frame, title='Returned observations', dataset_id='live_result_rows', sortable=True,
                      columns=['day', 'platform', 'value']),
        ])
        ctx.add_section('Local platform subset', [
            ScopedDataSource('live_result_platform', parent='live_result_rows'),
            FilterBar('live_result_platform', frame, filters=[
                {'type': 'toggle', 'column': 'platform', 'label': 'Local platform',
                 'options': ['iOS', 'Web'], 'default': 'iOS'},
            ]),
            KpiRow([{'label': 'Local returned value (USD)', 'column': 'value', 'agg': 'sum', 'format': 'currency'},
                    {'label': 'Local observations', 'agg': 'count'}], dataset_id='live_result_platform'),
            DataTable(frame, title='Local returned observations', dataset_id='live_result_platform', sortable=True,
                      columns=['day', 'platform', 'value']),
        ])
