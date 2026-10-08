"""Preview HTTP readers deny host manifests for both GET and HEAD."""
import os
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from trellum.artifacts import is_private_artifact
from trellum.runner import serve

PRIVATE_PATHS = [
    "_live_queries.json", "_live_queries.json.gz", "_LIVE_QUERIES.JSON",
    "_live_queries.json.", "_live_queries.json%20", "%5flive_queries.json",
    "%255flive_queries.json", "sub%2f_live_queries.json", "sub%5c_live_queries.json",
    "%ff", "%broken", "_live_queries.json?download=1",
    "_live_queries.json::$DATA", "_live_queries.json%3A%3A%24DATA",
    "_live_queries.json.gz::$DATA", "_live_queries.json.gz%3A%3A%24DATA",
]


@pytest.mark.parametrize("path", PRIVATE_PATHS)
def test_reserved_manifest_aliases(path):
    assert is_private_artifact(path)


@pytest.mark.parametrize("path", ["data.json", "data.json.gz", "rawdata.csv", "_meta.json", "_validation.json", "manifest.json", "index.html"])
def test_public_artifacts(path):
    assert not is_private_artifact(path)


@pytest.fixture(params=[False, True], ids=["single", "all"])
def preview(request, tmp_path, monkeypatch):
    output = tmp_path / "output"
    report = output / "sales" if request.param else output
    report.mkdir(parents=True)
    (report / "index.html").write_text("<html><head></head><body>public</body></html>")
    (report / "data.json").write_text("{}")
    (report / "_live_queries.json").write_text('{"version":1,"queries":{}}')
    (report / "_live_queries.json.gz").write_bytes(b"private sibling")
    captured = {}

    def capture(address, handler):
        captured["handler"] = handler
        return SimpleNamespace(serve_forever=lambda: None)

    monkeypatch.setattr(serve, "_ReuseHTTPServer", capture)
    monkeypatch.setattr(serve, "_claim_port", lambda *args: None)
    (serve._serve_all if request.param else serve._serve)(str(output), 0)
    server = ThreadingHTTPServer(("127.0.0.1", 0), captured["handler"])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}" + ("/sales/" if request.param else "/")
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_preview_denies_private_but_serves_public(preview, method):
    for path in PRIVATE_PATHS:
        req = urllib.request.Request(preview + path, method=method, headers={"Accept-Encoding": "gzip"})
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(req, timeout=5)
        assert error.value.code == 404, path
    for path in ("index.html", "data.json"):
        with urllib.request.urlopen(urllib.request.Request(preview + path, method=method), timeout=5) as response:
            assert response.status == 200


@pytest.mark.skipif(os.name != "nt", reason="NTFS stream alias requires Windows")
def test_ntfs_default_stream_is_an_actual_file_alias(preview, tmp_path):
    manifest = next((tmp_path / "output").rglob("_live_queries.json"))
    for suffix in ("", ".gz"):
        target = manifest.with_name(manifest.name + suffix)
        try:
            alias_bytes = target.with_name(target.name + "::$DATA").read_bytes()
        except OSError:
            pytest.skip("Temporary filesystem does not support NTFS streams")
        assert alias_bytes == target.read_bytes()
        for stream in ("::$DATA", "%3A%3A%24DATA"):
            with pytest.raises(urllib.error.HTTPError) as error:
                urllib.request.urlopen(preview + target.name + stream, timeout=5)
            assert error.value.code == 404


def test_preview_denies_resolved_manifest_symlink(preview, tmp_path):
    manifest = next((tmp_path / "output").rglob("_live_queries.json"))
    alias = manifest.with_name("apparently-public.json")
    try:
        alias.symlink_to(manifest)
    except OSError:
        pytest.skip("Temporary filesystem does not permit symlinks")
    for method in ("GET", "HEAD"):
        req = urllib.request.Request(preview + alias.name, method=method)
        with pytest.raises(urllib.error.HTTPError) as error:
            urllib.request.urlopen(req, timeout=5)
        assert error.value.code == 404
