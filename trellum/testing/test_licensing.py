import json

import pytest

from trellum import __version__, licensing
from trellum.rendering.artifacts import _write_meta
from trellum.rendering.html_builder import render_report
from trellum.report import ReportContext


def test_official_source_is_the_exact_release_tag(monkeypatch):
    monkeypatch.delenv("TRELLUM_SOURCE_URL", raising=False)
    assert licensing.source_url() == (
        f"https://github.com/trellumhq/trellum/tree/v{__version__}"
    )


def test_fork_source_override_is_used_and_html_escaped(monkeypatch):
    url = 'https://code.example/owner/project/tree/release?q="source"'
    monkeypatch.setenv("TRELLUM_SOURCE_URL", url)
    assert licensing.source_url() == url
    notice = licensing.source_notice_html()
    assert 'q=&quot;source&quot;' in notice
    assert 'href="javascript:' not in notice


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,bad",
        "/local/source",
        "https://user:password@example.com/source",
        "https://example.com/source\nmalicious",
    ],
)
def test_source_override_rejects_unsafe_urls(url):
    with pytest.raises(ValueError, match=r"HTTP\(S\)"):
        licensing.source_url(override=url)


def test_source_notice_is_injected_into_custom_html(monkeypatch):
    monkeypatch.delenv("TRELLUM_SOURCE_URL", raising=False)
    rendered = licensing.inject_source_notice("<html><body>Report</body></html>")
    assert rendered.index("Trellum runtime") < rendered.index("</body>")
    assert f"tree/v{__version__}" in rendered
    assert licensing.OUTPUT_LICENSE in rendered


def test_report_metadata_and_license_carry_runtime_provenance(tmp_path, monkeypatch):
    source = "https://code.example/trellum/tree/exact-commit"
    monkeypatch.setenv("TRELLUM_SOURCE_URL", source)
    _write_meta(str(tmp_path), "report", "Report", {})

    metadata = json.loads((tmp_path / "_meta.json").read_text(encoding="utf-8"))
    assert metadata["framework_license"] == licensing.LICENSE_ID
    assert metadata["framework_source_url"] == source
    text = (tmp_path / licensing.OUTPUT_LICENSE).read_text(encoding="utf-8")
    assert "GNU AFFERO GENERAL PUBLIC LICENSE" in text


@pytest.mark.parametrize("field", ["html_filename", "data_filename"])
@pytest.mark.parametrize(
    "filename",
    [
        "../escape.html",
        r"..\escape.html",
        "/escape.html",
        r"C:\escape.html",
        "D:escape.html",
        "report.html:stream",
        "nested/report.html",
        ".. ",
        "report.html.",
        "NUL.html",
    ],
)
def test_custom_output_rejects_nonportable_filenames_before_writing(
    tmp_path, field, filename
):
    output = tmp_path / "output"
    ctx = ReportContext({}, "report", str(output))

    with pytest.raises(ValueError, match="single path-safe name"):
        ctx.set_custom_output("<html></html>", **{field: filename})

    assert ctx.custom_html is None
    assert not output.exists()


def test_custom_output_keeps_named_files_with_a_working_license_link(tmp_path):
    output = tmp_path / "output"
    ctx = ReportContext({"name": "Custom"}, "custom", str(output))
    ctx.set_custom_output(
        "<html><body>Custom</body></html>",
        {"answer": 42},
        html_filename="dashboard.html",
        data_filename="dashboard-data.json",
    )

    html_path, data_path = render_report(ctx, str(output), auto_refresh=False)

    assert html_path == str(output / "dashboard.html")
    assert data_path == str(output / "dashboard-data.json")
    assert (output / licensing.OUTPUT_LICENSE).is_file()
    html = (output / "dashboard.html").read_text(encoding="utf-8")
    assert f'href="{licensing.OUTPUT_LICENSE}"' in html
