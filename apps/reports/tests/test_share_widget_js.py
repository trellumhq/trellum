"""The shipped share widget source (static/report_share.js).

The file is served verbatim from /api/reports/share-widget.js into every built
report page. Its "Copy embed code" snippet runs on a customer's site and is
built as a string by hand, so one text assertion pins the origin check that
snippet must carry; the element's own check is exercised behaviourally in
test_embed_element_js.py.
"""
import shutil
import subprocess

import pytest
from django.conf import settings

SHARE_JS = settings.BASE_DIR / "static" / "report_share.js"


@pytest.fixture(scope="module")
def source() -> str:
    return SHARE_JS.read_text(encoding="utf-8")


class TestEmbedSnippet:
    def test_snippet_validates_source_and_origin(self, source):
        assert "var portal = new URL(f.src).origin;" in source
        assert "e.source !== f.contentWindow || e.origin !== portal" in source


class TestSyntax:
    @pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
    def test_node_check_passes(self):
        result = subprocess.run(
            ["node", "--check", str(SHARE_JS)], capture_output=True, text=True
        )
        assert result.returncode == 0, result.stderr

    def test_bundle_contains_the_current_source(self, client, db):
        body = client.get("/api/reports/share-widget.js").content.decode()
        assert "function embedSnippet(" in body
        assert '"trellum:height"' in body
