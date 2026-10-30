"""Timing summaries and an opt-in real-process protocol smoke check."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from scripts.review_response_timing import summarize


def test_summary_keeps_surfaces_modes_and_signed_races_separate():
    rows = [
        {'surface': 'report', 'mode': 'waiting', 'ms': {'ack_to_pickup': value}}
        for value in (-4, -1, 2)
    ] + [{'surface': 'analysis', 'mode': 'busy', 'ms': {'ack_to_pickup': 600}}]
    result = summarize(rows)
    assert result['report/waiting']['ack_to_pickup'] == {'median_ms': -1, 'max_ms': 2}
    assert result['analysis/busy']['ack_to_pickup'] == {'median_ms': 600, 'max_ms': 600}
    assert len(result) == 2


@pytest.mark.heavy
def test_real_chromium_both_surfaces_and_correlated_completion(tmp_path):
    output = tmp_path / 'timing.json'
    script = Path(__file__).with_name('review_response_timing.py')
    subprocess.run([sys.executable, str(script), '--samples', '1', '--rebuild',
                    '--deadline', '90', '--output', str(output)], check=True, timeout=100)
    data = json.loads(output.read_text())
    assert len(data['samples']) == 4
    assert len(data['summary']) == 4
    assert len({(r['session'], r['slug'], r['feedback_id']) for r in data['samples']}) == 4
    assert all(r['milestones_monotonic_s']['completion_visible'] >=
               r['milestones_monotonic_s']['reply_ack'] for r in data['samples'])
    assert {b['slug'] for b in data['builds_separate'] if b['phase'] == 'simple_rebuild'} == {
        'timing-report', 'timing-analysis'}
    assert 'synthetic' in data['provider']
    assert {row['surface'] for row in data['polling_checks']} == {'report', 'analysis'}
    assert all(row['hidden_suppressed'] and row['resume_ms'] <= 800
               for row in data['polling_checks'])
    polling = {row['surface']: row for row in data['polling_checks']}
    assert 1850 <= polling['report']['idle_gap_ms'] <= 2700
    assert 2350 <= polling['analysis']['idle_gap_ms'] <= 3200
    assert all(row['ms']['activity_ack_to_next_status'] <= 800 for row in data['samples'])
