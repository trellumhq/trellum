"""Independent arithmetic and native diagnostics for the authored example."""
import importlib.util
from pathlib import Path

from trellum.report import ReportContext
from trellum.reporting.diagnostics import compute_details
from trellum.validation import validate_report
from trellum.validation.checks.metric_views import check_metric_views
from trellum.testing.test_metric_views import node
import json
import pytest

ROOT = Path(__file__).resolve().parents[2]


def example_context(output):
    spec=importlib.util.spec_from_file_location('examples_marketing',ROOT/'trellum/demo/reports/component-showcase/examples_marketing.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    ctx=ReportContext(config={'name':'Marketing geography','studio':'test','category':'Test',
        'description':'Complete synthetic acquisition-cohort facts; overlapping geography groups.'},slug='marketing-geography',output_dir=str(output))
    module.add_sections(ctx)
    return ctx,module


def test_native_marketing_arithmetic_and_diagnostics(tmp_path):
    ctx,module=example_context(tmp_path)
    frame=module.facts()
    assert len(frame)==16
    assert frame[['installs','cost','d7_revenue','d30_revenue','d7_retained']].sum().tolist()==[2520,4320,3042,4698,594]
    cfg={'groups':module.GROUPS,'metrics':module.METRICS}
    rows=node('console.log(JSON.stringify(_fwMetricViews.derive(ROWS,CFG)));'.replace('ROWS',frame.to_json(orient='records')).replace('CFG',json.dumps(cfg)))
    assert [r['installs'] for r in rows]==[2520,2160,900,720]
    assert [r['cost'] for r in rows]==[4320,3420,1800,1080]
    assert [r['d30_revenue'] for r in rows]==[4698,4158,2250,1260]
    assert sum(r['installs'] for r in rows)==6300
    assert rows[0]['cpi']==pytest.approx(12/7)
    assert rows[0]['d30_recovery']==pytest.approx(1.0875)
    assert rows[0]['d7_retention']==pytest.approx(594/2520)
    details=compute_details(ctx)
    assert details['totals']['total_rows']==16
    derived=next(d for d in details['datasets'] if d['id']==module.RESULT)
    assert derived['rows']==derived['size_bytes_inline']==0 and derived['derived_group_rows']==4
    bound=[r for r in details['filter_matrix'] if r.get('dataset_id')==module.RESULT]
    assert len(bound)==34
    assert all(c['status']=='active' for row in bound for c in row['filters'])
    result=validate_report(ctx,details=details);check_metric_views(ctx,result)
    issues=[(c.id,c.level,c.component,c.section,c.message) for c in result.checks if c.level in {'warn','fail'}]
    assert issues==[],issues
