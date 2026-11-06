"""Project setup retains knowledge and keeps result exports out of Git."""
import subprocess

from trellum.agent.agentsetup import write_project_files
from trellum.scaffold.init_project import init_project


def test_setup_retains_authored_context_and_agent_rules(tmp_path):
    (tmp_path / 'context').mkdir()
    knowledge = tmp_path / 'context/warehouse.md'
    knowledge.write_text('Verified: orders are unique on order_id.\n')
    rules = tmp_path / 'AGENTS.md'
    rules.write_text('Team rule: review all published conclusions.\n')
    init_project(str(tmp_path))
    assert knowledge.read_text() == 'Verified: orders are unique on order_id.\n'
    assert rules.read_text().startswith('Team rule: review all published conclusions.')
    assert 'context/README.md' in rules.read_text()
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    write_project_files(tmp_path)
    assert before == {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}


def test_exports_ignored_but_queries_and_context_can_be_versioned(tmp_path):
    write_project_files(tmp_path)
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    for name in ['analysis/results/probe.csv', 'analysis/probe.sql']:
        (tmp_path / name).write_text('synthetic fixture')
    ignored = subprocess.run(['git', 'check-ignore', 'analysis/results/probe.csv'],
                             cwd=tmp_path, capture_output=True, text=True)
    assert ignored.returncode == 0
    for name in ['analysis/probe.sql', 'context/warehouse.md', 'analysis/results/.gitignore']:
        assert subprocess.run(['git', 'check-ignore', '-q', name], cwd=tmp_path).returncode == 1


def test_demo_without_data_also_installs_context(tmp_path):
    from trellum.demo.__main__ import main
    assert main(['--dest', str(tmp_path), '--no-data']) == 0
    assert (tmp_path / 'context/warehouse.md').is_file()
    assert (tmp_path / 'analysis/results/.gitignore').is_file()
    assert 'analysis/README.md' in (tmp_path / 'AGENTS.md').read_text()
