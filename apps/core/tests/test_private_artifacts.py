"""Private build publication and retention, without an external store."""
import shutil
import subprocess
from pathlib import Path

import pytest

from apps.core import storage
from apps.core.tests.test_storage import FakeClient, install_build, remote  # noqa: F401


@pytest.fixture
def private_build(tmp_path):
    out = tmp_path / "private-build"
    out.mkdir()
    (out / "index.html").write_bytes(b"<h1>public</h1>")
    (out / "_meta.json").write_text('{"last_run":"2026-10-08T00:00:00"}')
    (out / "_live_queries.json").write_text('{"queries":{"q":{"sql":"SELECT 1"}}}')
    return out


@pytest.mark.django_db
def test_private_manifest_precedes_pointer_and_internal_read_works(studio, remote, private_build):
    storage.publish_build(studio, "sales", str(private_build), 1)
    build = storage.current_build(studio, "sales")
    private = f"_private/demo/casino/sales/builds/{build}/_live_queries.json"
    assert private in remote.objects
    assert not any(key.startswith("demo/") and "_live_queries" in key for key in remote.objects)
    assert remote.put_kwargs[private]["CacheControl"] == "no-store"
    assert remote.calls.index(("put", private)) < remote.calls.index(("put", "demo/casino/sales/_current"))
    assert storage.read_live_queries(studio, "sales")["queries"]["q"]["sql"] == "SELECT 1"
    assert not (storage.output_root(studio, "sales") / "_live_queries.json").exists()
    assert storage.live_query_index(studio) == {"sales"}


@pytest.mark.django_db
def test_private_upload_failure_never_advances_pointer(studio, remote, private_build, monkeypatch):
    install_build(remote, build="old")
    original = FakeClient.put_object

    def fail_private(self, **kwargs):
        if kwargs["Key"].startswith("_private/"):
            raise OSError("synthetic private upload failure")
        return original(self, **kwargs)

    monkeypatch.setattr(FakeClient, "put_object", fail_private)
    with pytest.raises(OSError, match="private upload failure"):
        storage.publish_build(studio, "sales", str(private_build), 1)
    assert storage.current_build(studio, "sales") == "old"


@pytest.mark.django_db
def test_index_uses_current_build_and_supports_legacy(studio, remote):
    prefix = install_build(remote, build="old", files={"index.html": b"x", "_live_queries.json": b'{"queries":{"legacy":{"sql":"SELECT 1"}}}'})
    assert storage.live_query_index(studio) == {"sales"}
    assert storage.read_live_queries(studio, "sales")["queries"]["legacy"]["sql"] == "SELECT 1"
    install_build(remote, build="new", files={"index.html": b"x"})
    remote.objects[f"_private/{prefix}/builds/uploading/_live_queries.json"] = b"{}"
    assert storage.live_query_index(studio) == set()
    assert storage.read_live_queries(studio, "sales") == {}


@pytest.mark.django_db
def test_static_publish_clears_the_live_flag(studio, remote, private_build):
    storage.publish_build(studio, "sales", str(private_build), 1)
    assert storage.live_query_index(studio) == {"sales"}
    (private_build / "_live_queries.json").unlink()
    storage.publish_build(studio, "sales", str(private_build), 2)
    assert storage.live_query_index(studio) == set()
    assert storage.read_live_queries(studio, "sales") == {}


@pytest.mark.django_db
def test_prune_removes_private_builds_including_failed_private_only_uploads(studio, remote):
    prefix = install_build(remote, build="03")
    for build in ("00-failed", "01", "02", "03"):
        remote.objects[f"_private/{prefix}/builds/{build}/_live_queries.json"] = b"{}"
    for build in ("01", "02"):
        remote.objects[f"{prefix}/builds/{build}/index.html"] = b"x"
    storage._prune_remote_builds(prefix, keep="03")
    assert not any("/builds/00-failed/" in k or "/builds/01/" in k for k in remote.objects)
    assert f"_private/{prefix}/builds/02/_live_queries.json" in remote.objects
    assert f"_private/{prefix}/builds/03/_live_queries.json" in remote.objects


@pytest.mark.django_db
@pytest.mark.parametrize("slug", ["sales", None])
def test_delete_output_counts_and_removes_private_namespace(studio, remote, slug):
    prefix = install_build(remote)
    private = f"_private/{prefix}/builds/b1/_live_queries.json"
    remote.objects[private] = b"{\"private\":true}"
    remote.objects["_private/demo/other/sales/builds/b1/_live_queries.json"] = b"unrelated"
    measured = storage.output_bytes(studio, slug)
    assert storage.delete_output(studio, slug) == measured
    assert private not in remote.objects
    assert not any(k.startswith(prefix + "/") for k in remote.objects)
    assert "_private/demo/other/sales/builds/b1/_live_queries.json" in remote.objects


@pytest.mark.django_db
@pytest.mark.parametrize("scope", ["studio", "org"])
def test_orphan_retention_removes_deleted_owners_private_blobs(studio_tree, remote, settings, scope):
    from apps.core import retention
    from apps.core.tests.test_retention import _age_tree

    settings.RETENTION = {**settings.RETENTION, "abandoned_upload_days": 90}
    out = studio_tree.output_dir / "sales"
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_bytes(b"old")
    _age_tree(studio_tree.output_dir, days=100)
    prefix = install_build(remote)
    private = f"_private/{prefix}/builds/b1/_live_queries.json"
    remote.objects[private] = b"private SQL"
    (studio_tree if scope == "studio" else studio_tree.org).delete()
    assert retention.purge_orphaned_data()[1] == 1
    assert private not in remote.objects
    assert not any(k.startswith(prefix + "/") for k in remote.objects)


def test_s3_publisher_filters_reserved_aliases(tmp_path, monkeypatch):
    from apps.core.s3 import S3Backend

    names = ["_live_queries.json", "_LIVE_QUERIES.JSON", "_live_queries.json.gz", "_live_queries.json.", "data.json", "rawdata.csv"]
    for name in names:
        (tmp_path / name).write_bytes(b"{}")
    uploaded = []

    class Client:
        def upload_file(self, path, bucket, key, ExtraArgs):
            uploaded.append(key)

    backend = S3Backend(bucket="test-bucket")
    monkeypatch.setattr(backend, "_client", Client)
    backend.publish(str(tmp_path), "demo/casino/sales/builds/b1")
    assert {k.rsplit("/", 1)[1] for k in uploaded} == {"data.json", "rawdata.csv"}


def test_actual_edge_handler_denies_private_artifacts():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is needed to run the edge handler")
    script = Path(__file__).resolve().parents[3] / "edge" / "report-access-worker.test.mjs"
    result = subprocess.run([node, str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr
