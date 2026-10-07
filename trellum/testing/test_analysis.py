"""Offline article and portable capture trust boundaries."""

from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from PIL import Image

from trellum.analysis_capture import import_capture
from trellum.cli import main
from trellum.cli.commands.analysis import new_analysis
from trellum.report import BaseReport, ReportContext
from trellum.runner.discovery import discover_report, scan_report_configs
from trellum.runner.execute import run_report
from trellum.validation import validate_report


@pytest.fixture
def article(tmp_path, monkeypatch):
    monkeypatch.setenv("FW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr("trellum.project._project_root_override", None)
    return new_analysis("decision")


def _png():
    stream = io.BytesIO()
    Image.new("RGB", (12, 8), "white").save(stream, format="PNG")
    return stream.getvalue()


def _capture(tmp_path, **source_updates):
    source = dict(report_slug="source", report_name="<Revenue>",
                  url="https://example.test/reports/source", component_id="fw_c1",
                  component_title="Revenue & margin", captured_at="2026-10-07T10:00:00Z",
                  source_built_at="2026-10-06T09:00:00Z", filters={"region": "<EMEA>"})
    source.update(source_updates)
    path = tmp_path / "capture.json"
    path.write_text(json.dumps(dict(format="trellum-analysis-capture", version=1,
                                   image=dict(mime_type="image/png", data_base64=base64.b64encode(_png()).decode()),
                                   source=source)), encoding="utf-8")
    return path


def test_cli_scaffold_import_build_roundtrip(article, tmp_path, capsys, monkeypatch):
    capture = _capture(tmp_path)
    assert main(["analysis", "import", str(article), str(capture), "--name", "revenue"]) == 0
    markdown = capsys.readouterr().out.strip()
    assert markdown == "![Evidence](evidence/revenue.png)"
    (article / "content.md").write_text(
        "## Question\n\n**Recommendation** & context.\n\n## Question\n\n"
        "| Metric | Value |\n| --- | --- |\n| Revenue | 12 |\n\n" + markdown,
        encoding="utf-8")
    config = BaseReport.load_config(str(article))
    config.update(author='<Author>', name='<Decision>')
    (article / "report.yaml").write_text(json.dumps(config), encoding="utf-8")
    # All executable project hooks would fail if analysis touched them.
    for path in (article / "generator.py", article / "__init__.py",
                 tmp_path / "components" / "custom.py", tmp_path / "themes" / "custom.py"):
        path.parent.mkdir(exist_ok=True)
        path.write_text('raise AssertionError("article executed project code")', encoding="utf-8")
    monkeypatch.setattr("trellum.runner.execute._load_events", lambda *a: pytest.fail("queried events"))
    monkeypatch.setattr("trellum.report.ReportContext.get_connection", lambda *a: pytest.fail("queried warehouse"))
    output = Path(run_report(str(article), fail_on_validation=True))
    page = (output / "index.html").read_text(encoding="utf-8")
    assert 'data-content-kind="analysis"' in page
    assert '<title>&lt;Decision&gt;</title>' in page
    assert '<h1 class="fw-article-title">&lt;Decision&gt;</h1>' in page
    assert 'By &lt;Author&gt;' in page
    assert '<h2 id="article-question">' in page
    assert '<h2 id="article-question-2">' in page
    assert '<table>' in page and '<strong>Recommendation</strong>' in page
    assert 'title="Enlarge image"' in page
    assert '&lt;Revenue&gt;' in page and '&lt;EMEA&gt;' in page
    assert 'Source built: 6 Oct 2026, 09:00 UTC' in page
    assert (output / "evidence" / "revenue.png").read_bytes() == _png()
    assert json.loads((output / "_meta.json").read_bytes())["kind"] == "analysis"
    assert json.loads((output / "data.json").read_bytes())["_content_kind"] == "analysis"
    assert json.loads((output / "_validation.json").read_bytes())["summary"]["fail"] == 0
    assert discover_report(str(article)).__name__ == "AnalysisReport"
    assert [entry["slug"] for entry in scan_report_configs(str(tmp_path / "reports"))] == ["decision"]


@pytest.mark.parametrize("markdown", [
    '<script>alert(1)</script>\n<img src=x onerror=alert(1)>',
    '[bad](javascript:alert%281%29) [file](file:///etc/passwd) [data](data:text/html,hello)',
    '[bad](vbscript:hello) [remote](//example.test/path)',
    '![bad](//example.test/image.png) ![bad](C:/image.png)',
])
def test_unsafe_markup_cannot_execute(article, markdown):
    (article / "content.md").write_text(markdown, encoding="utf-8")
    page = Path(run_report(str(article))).joinpath("index.html").read_text(encoding="utf-8").split('<article ', 1)[1].split('</article>', 1)[0]
    assert '<script>alert(1)</script>' not in page
    assert '<img src=x' not in page
    assert 'href="javascript:' not in page and 'href="file:' not in page
    assert 'href="data:' not in page and 'href="vbscript:' not in page
    assert 'href="//example.test' not in page


@pytest.mark.parametrize("asset", ["../outside.png", "https://example.test/image.png",
                                    "evidence/CON.png"])
def test_unsafe_images_fail_without_output(article, asset):
    (article / "content.md").write_text(f"![Evidence]({asset})", encoding="utf-8")
    with pytest.raises(ValueError):
        run_report(str(article))
    assert not (article.parents[1] / "output" / "decision" / "index.html").exists()


def test_failed_rebuild_preserves_every_previous_artifact(article, tmp_path, monkeypatch):
    markdown = import_capture(str(article), str(_capture(tmp_path)), "revenue")
    (article / "content.md").write_text("## Evidence\n\n" + markdown, encoding="utf-8")
    output = Path(run_report(str(article)))
    original = {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}
    (article / "evidence" / "revenue.png").write_bytes(_png() + b"changed")
    def fail_render(ctx, out, **kwargs):
        Path(out, "index.html").write_text("partial build")
        raise RuntimeError("render failed")
    monkeypatch.setattr("trellum.runner.execute.render_report", fail_render)
    with pytest.raises(RuntimeError, match="render failed"):
        run_report(str(article))
    assert original == {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}


@pytest.mark.parametrize("changes", [{"kind": "unknown"}, {"kind": None},
                                    {"schedule": {}}, {"data_sources": []},
                                    {"author": {"name": "x"}}, {"extra_cdn": {}}])
def test_invalid_analysis_manifest_is_rejected(article, changes):
    config = BaseReport.load_config(str(article))
    config.update(changes)
    (article / "report.yaml").write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(ValueError):
        run_report(str(article))
    result = validate_report(ReportContext(config, "decision", "unused"))
    assert any(check.id == "yaml-content-kind" and check.level == "fail" for check in result.checks)


@pytest.mark.parametrize("url", ["javascript:alert(1)", "https://user:pass@example.test/",
                                 "https://example.test/?token=secret", "https://example.test/share/secret",
                                 "https://example.test/#secret", "https://example.test/%73hare/secret"])
def test_capture_private_or_unsafe_urls_rejected(article, tmp_path, url):
    with pytest.raises(ValueError):
        import_capture(str(article), str(_capture(tmp_path, url=url)), "capture")
    assert not list((article / "evidence").iterdir())


def test_import_never_overwrites_and_accepts_missing_optional_metadata(article, tmp_path):
    capture = _capture(tmp_path, report_name=None, url="", component_id=None,
                       component_title="", source_built_at=None)
    import_capture(str(article), str(capture), "capture")
    with pytest.raises(FileExistsError):
        import_capture(str(article), str(capture), "capture")
    with pytest.raises(FileExistsError):
        new_analysis("decision")
    assert json.loads((article / "evidence" / "capture.json").read_bytes())["report_name"] is None


def test_browser_capture_default_filename_imports_without_name(article, tmp_path):
    capture = _capture(tmp_path)
    browser_capture = tmp_path / "player-overview-fw_c1.trellum-capture.json"
    capture.rename(browser_capture)
    source = json.loads(browser_capture.read_bytes())["source"]
    markdown = import_capture(str(article), str(browser_capture))
    assert markdown == "![Evidence](evidence/player-overview-fw_c1.png)"
    assert (article / "evidence" / "player-overview-fw_c1.png").read_bytes() == _png()
    assert json.loads((article / "evidence" / "player-overview-fw_c1.json").read_bytes()) == source
    with pytest.raises(FileExistsError):
        import_capture(str(article), str(browser_capture))


@pytest.mark.parametrize("name", ["con", "aux", "lpt1"])
def test_browser_capture_default_name_rejects_windows_devices(article, tmp_path, name):
    capture = _capture(tmp_path)
    browser_capture = tmp_path / f"{name}.trellum-capture.json"
    capture.rename(browser_capture)
    with pytest.raises(ValueError):
        import_capture(str(article), str(browser_capture))
    assert not list((article / "evidence").iterdir())


@pytest.mark.parametrize("name", ["CON", "con", "aux", "lpt1", "../escape", "bad/name", "bad."])
def test_portable_names_reject_windows_devices_and_traversal(article, tmp_path, name):
    with pytest.raises(ValueError):
        new_analysis(name)
    with pytest.raises(ValueError):
        import_capture(str(article), str(_capture(tmp_path)), name)


def test_invalid_png_and_provenance_types(article, tmp_path):
    path = _capture(tmp_path)
    capture = json.loads(path.read_bytes())
    capture["image"]["data_base64"] = base64.b64encode(b"\x89PNG\r\n\x1a\nbroken").decode()
    path.write_text(json.dumps(capture))
    with pytest.raises(ValueError, match="decode"):
        import_capture(str(article), str(path), "bad")
    with pytest.raises(ValueError, match="filters"):
        import_capture(str(article), str(_capture(tmp_path, filters=[])), "bad")
    with pytest.raises(ValueError, match="timestamp"):
        import_capture(str(article), str(_capture(tmp_path, captured_at="2026-10-07")), "bad")


def test_image_symlink_escape_is_rejected(article, tmp_path, monkeypatch):
    from trellum.analysis_capture import confined_file
    outside = tmp_path / "outside.png"
    outside.write_bytes(_png())
    local = article / "evidence" / "linked.png"
    try:
        local.symlink_to(outside)
    except OSError:
        local.write_bytes(_png())
        resolve = Path.resolve
        monkeypatch.setattr(Path, "resolve", lambda self, *a, **k: outside if self == local else resolve(self, *a, **k))
    with pytest.raises(ValueError, match="outside"):
        confined_file(article, "evidence/linked.png")


def test_legacy_report_without_kind_still_builds_and_discovers(tmp_path, monkeypatch):
    monkeypatch.setenv("FW_PROJECT_ROOT", str(tmp_path))
    monkeypatch.setattr("trellum.project._project_root_override", None)
    directory = tmp_path / "reports" / "plain"
    directory.mkdir(parents=True)
    (directory / "report.yaml").write_text(json.dumps(dict(slug="plain", name="Plain")))
    (directory / "generator.py").write_text(
        'from trellum import BaseReport\nfrom trellum.components import KpiRow\n'
        'class Plain(BaseReport):\n'
        '    def generate(self, ctx):\n'
        '        ctx.add_section("Overview", [KpiRow([{"label": "Value", "value": 12}])])\n')
    assert scan_report_configs(str(tmp_path / "reports"))[0]["config"].get("kind", "report") == "report"
    output = Path(run_report(str(directory)))
    assert 'data-content-kind="report"' in (output / "index.html").read_text(encoding="utf-8")
    assert json.loads((output / "_meta.json").read_bytes())["kind"] == "report"


def test_custom_theme_is_not_rendered_in_article(article, monkeypatch, capsys):
    from trellum.themes import Theme
    monkeypatch.setitem(__import__("trellum.themes", fromlist=["THEME_REGISTRY"]).THEME_REGISTRY,
                        "private-custom", Theme(bg_main="custom-theme-marker"))
    config = BaseReport.load_config(str(article))
    config["theme"] = "private-custom"
    (article / "report.yaml").write_text(json.dumps(config))
    output = Path(run_report(str(article), fail_on_validation=True))
    assert "custom-theme-marker" not in (output / "index.html").read_text(encoding="utf-8")
    assert "using framework default" in capsys.readouterr().out


def test_batch_test_runner_discovers_analysis(article, tmp_path, monkeypatch):
    from trellum.testing.runner import test_all_reports
    monkeypatch.setattr("trellum.runner._load_events", lambda *a: pytest.fail("analysis queried events"))
    results = test_all_reports(str(tmp_path / "reports"), output_base=str(tmp_path / "test-output"))
    assert len(results) == 1
    assert results[0].passed, results[0].details


def test_failed_publish_preserves_previous_article(article, monkeypatch):
    output = Path(run_report(str(article)))
    previous = {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}
    class FailedBackend:
        def publish(self, path, slug):
            assert Path(path, "index.html").is_file()
            raise RuntimeError("publish failed")
    monkeypatch.setattr("trellum.output_backends.backends.get_output_backend", lambda: FailedBackend())
    (article / "content.md").write_text("## Changed\nNew content")
    with pytest.raises(RuntimeError, match="publish failed"):
        run_report(str(article), production=True)
    assert previous == {p.relative_to(output): p.read_bytes() for p in output.rglob("*") if p.is_file()}


def test_evidence_size_limits_reject_before_write(article, tmp_path):
    from trellum.analysis_capture import MAX_IMAGE_BYTES, validate_png
    with pytest.raises(ValueError, match="20 MiB"):
        validate_png(b"\x89PNG\r\n\x1a\n" + b"x" * MAX_IMAGE_BYTES)
    path = _capture(tmp_path)
    capture = json.loads(path.read_bytes())
    capture["image"]["data_base64"] = base64.b64encode(b"x" * (MAX_IMAGE_BYTES + 1)).decode()
    path.write_text(json.dumps(capture))
    with pytest.raises(ValueError, match="20 MiB"):
        import_capture(str(article), str(path), "huge")
    assert not list((article / "evidence").iterdir())


def test_readable_capture_caption_preserves_exact_sidecar(article, tmp_path):
    filters = {"activity": {
        "region-id": {"column": "region", "mode": "equals", "value": "<EMEA>", "flagValue": None},
        "platform-id": {"column": "platform", "mode": "in", "value": ["ios", "web", None], "flagValue": None},
        "period-id": {"column": "event_date", "mode": "range",
                      "value": {"min": "2026-09-01", "max": "2026-09-30"}, "flagValue": None},
        "amount-id": {"column": "revenue", "mode": "numrange", "value": {"min": 0, "max": None}, "flagValue": None},
        "unset-id": {"column": "country", "mode": "equals", "value": None, "flagValue": None},
        "flag-id": {"column": "is_bot", "mode": "flag", "value": "exclude", "flagValue": "true"},
    }}
    capture = _capture(tmp_path, filters=filters, captured_at="2026-10-07T12:34:56.123456+02:00",
                       source_built_at="2026-10-06T09:10:11.654321Z")
    markdown = import_capture(str(article), str(capture), "snapshot")
    sidecar = (article / "evidence" / "snapshot.json").read_bytes()
    config = BaseReport.load_config(str(article))
    config["name"] = 'A full <script>title</script> & a long recommendation for mobile readers'
    (article / "report.yaml").write_text(json.dumps(config), encoding="utf-8")
    (article / "content.md").write_text("## Findings\n\n" + markdown, encoding="utf-8")
    output = Path(run_report(str(article)))
    page = (output / "index.html").read_text(encoding="utf-8")
    article_html = page.split('<article ', 1)[1].split('</article>', 1)[0]
    assert '<h1 class="fw-article-title">A full &lt;script&gt;title&lt;/script&gt; &amp; a long recommendation for mobile readers</h1>' in article_html
    assert '<li class="fw-toc-h2"><a href="#article-findings">Findings</a></li>' in article_html
    assert 'Captured: 7 Oct 2026, 12:34 UTC+02:00' in article_html
    assert 'Source built: 6 Oct 2026, 09:10 UTC' in article_html
    assert 'activity: region equals &lt;EMEA&gt;' in article_html
    assert 'platform is one of ios, web, null' in article_html
    assert 'event date from 2026-09-01 to 2026-09-30' in article_html
    assert 'revenue from 0' in article_html and 'country equals null' in article_html
    assert 'is bot excludes true' in article_html
    assert 'flagValue' not in article_html and 'region-id' not in article_html
    assert '.123456' not in article_html and '<script>title</script>' not in article_html
    assert (output / "evidence" / "snapshot.json").read_bytes() == sidecar
    assert (article / "evidence" / "snapshot.json").read_bytes() == sidecar


@pytest.mark.parametrize("filters, expected", [
    ({"region": "<EMEA>", "platforms": ["ios", None]}, "region: &lt;EMEA&gt;; platforms: [&quot;ios&quot;, null]"),
    ({"custom": {"operator": "<unknown>", "options": {"enabled": False}, "value": [1, 2]}},
     '&quot;operator&quot;: &quot;&lt;unknown&gt;&quot;'),
    ({"ds": {"unfamiliar": {"column": "region", "mode": "equals", "value": "ios", "extra": 7}}},
     '&quot;extra&quot;: 7'),
])
def test_legacy_caption_filters_keep_safe_fallback_information(filters, expected):
    from trellum.analysis import _caption
    source = dict(report_slug="source", report_name=None, component_id="fw_c1", component_title=None,
                  url=None, captured_at="2026-10-07T10:00:00Z", source_built_at=None, filters=filters)
    caption = _caption(source)
    assert expected in caption
    assert 'Report: source' in caption and 'Component: fw_c1' in caption
    assert '<unknown>' not in caption and '<EMEA>' not in caption


@pytest.mark.parametrize("value, expected", [
    ({"min": None, "max": "2026-09-30"}, "event date up to 2026-09-30"),
    ({"min": None, "max": None}, "event date all values"),
    (None, "event date range: null"),
    (["2026-09-01", "2026-09-30"], 'event date range: [&quot;2026-09-01&quot;, &quot;2026-09-30&quot;]'),
])
def test_caption_range_values_and_nulls_are_readable(value, expected):
    from trellum.analysis import _caption
    source = dict(report_slug=None, report_name=None, component_id=None, component_title=None,
                  url=None, captured_at="2026-10-07T10:00:00Z", source_built_at=None,
                  filters={"activity": {"range": {"column": "event_date", "mode": "range", "value": value, "flagValue": None}}})
    assert expected in _caption(source)


def _run_cli(project: Path, *arguments: str, production: bool = False):
    # Retain only OS necessities: no inherited warehouse credentials or project settings.
    environment = {key: value for key, value in os.environ.items()
                   if key.upper() in {"PATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "COMSPEC", "PATHEXT"}}
    environment.update(PYTHONPATH=str(Path(__file__).resolve().parents[2]),
                       FW_PROJECT_ROOT=str(project), FW_UPDATE_CHECK="0")
    if production:
        environment["BI_STORAGE_BACKEND"] = "local"
    return subprocess.run([sys.executable, "-m", "trellum.run", *arguments], cwd=project,
                          env=environment, capture_output=True, text=True, encoding="utf-8", timeout=30)


@pytest.mark.parametrize("production", [False, True])
def test_actual_analysis_cli_skips_executable_bootstrap(article, tmp_path, production):
    markdown = import_capture(str(article), str(_capture(tmp_path)), "snapshot")
    (article / "content.md").write_text("## Findings\n\n" + markdown, encoding="utf-8")
    (tmp_path / "bootstrap.py").write_text(
        'from pathlib import Path\nPath("bootstrap-ran").write_text("executed")\n'
        'raise RuntimeError("analysis must never run bootstrap")\n', encoding="utf-8")
    arguments = ["reports/decision", "--no-serve"]
    if production:
        arguments.append("--production")
    completed = _run_cli(tmp_path, *arguments, production=production)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert not (tmp_path / "bootstrap-ran").exists()
    assert not (article / "generator.py").exists()
    assert "bootstrap.py failed" not in completed.stdout
    output = tmp_path / "output" / "decision"
    assert 'data-content-kind="analysis"' in (output / "index.html").read_text(encoding="utf-8")
    assert (output / "evidence" / "snapshot.png").read_bytes() == _png()
    assert (output / "evidence" / "snapshot.json").read_bytes() == (article / "evidence" / "snapshot.json").read_bytes()


@pytest.mark.parametrize("batch", [False, True])
def test_actual_report_cli_keeps_project_bootstrap(tmp_path, batch):
    directory = tmp_path / "reports" / "plain"
    directory.mkdir(parents=True)
    (directory / "report.yaml").write_text(json.dumps(dict(name="Plain", slug="plain")), encoding="utf-8")
    (tmp_path / "bootstrap.py").write_text(
        'import os\nfrom pathlib import Path\n'
        'Path("bootstrap-ran").write_text("executed")\n'
        'os.environ["TEST_BOOTSTRAP_CONTENT"] = "Registered project plugin"\n', encoding="utf-8")
    (directory / "generator.py").write_text(
        'import os\nfrom trellum import BaseReport\n'
        'class Plain(BaseReport):\n'
        '    def generate(self, ctx):\n'
        '        ctx.add_section("Overview", [os.environ["TEST_BOOTSTRAP_CONTENT"]])\n', encoding="utf-8")
    completed = _run_cli(tmp_path, "--all" if batch else "reports/plain", "--no-serve")
    assert completed.returncode == 0, completed.stdout + completed.stderr
    assert (tmp_path / "bootstrap-ran").read_text() == "executed"
    assert 'Registered project plugin' in (tmp_path / "output" / "plain" / "index.html").read_text(encoding="utf-8")
