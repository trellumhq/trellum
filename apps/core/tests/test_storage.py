"""Reading built report output, on a shared filesystem and from object storage.

The remote tests drive a fake backend rather than a real driver. What matters
here is the portal's contract with the store — which prefix it addresses, when
it pulls and when it does not, and above all the publish protocol: builds land
in immutable ``builds/{build}/`` prefixes and one atomic ``_current`` pointer
is what makes a build live — not that the driver itself works.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from apps.core import storage


@pytest.fixture
def local_output(studio, settings, tmp_path):
    """A studio whose output directory holds two built reports."""
    settings.DATA_DIR = tmp_path
    settings.TRELLUM_STORAGE_BACKEND = "local"
    root = studio.output_dir
    for slug, files in (
        ("sales", {"index.html": "<h1>sales</h1>", "_meta.json": '{"last_run": "2026-01-01T00:00:00Z"}'}),
        ("churn", {"churn.html": "<h1>churn</h1>"}),
    ):
        d = root / slug
        d.mkdir(parents=True)
        for name, body in files.items():
            (d / name).write_text(body, encoding="utf-8")
    return root


class FakeS3:
    """Stands in for the portal's S3Backend, recording what it was asked to do.

    ``publish`` actually installs the directory's files into ``objects`` under
    the given prefix (top-level files only, skipping ``.gz`` — the same rules
    as the real backend), so publish tests assert on resulting bucket state,
    not just on recorded calls.
    """

    def __init__(self, objects: dict[str, bytes] | None = None):
        self.bucket = "test-bucket"
        self.objects = objects or {}
        self.pulls: list[tuple[str, str]] = []
        self.publishes: list[tuple[str, str]] = []
        #: Every store interaction in order: ("publish", prefix),
        #: ("get"/"put"/"delete", key). The publish-protocol tests are
        #: order-sensitive, and this is what they read.
        self.calls: list[tuple[str, str]] = []
        self.put_kwargs: dict[str, dict] = {}

    def pull(self, prefix: str, local_dir: str) -> None:
        self.pulls.append((prefix, local_dir))
        for key, body in self.objects.items():
            if not key.startswith(prefix + "/"):
                continue
            target = Path(local_dir) / key[len(prefix) + 1:]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)

    def publish(self, output_dir: str, prefix: str) -> None:
        self.publishes.append((output_dir, prefix))
        self.calls.append(("publish", prefix))
        for name in sorted(os.listdir(output_dir)):
            path = Path(output_dir) / name
            if path.is_file() and not name.endswith(".gz"):
                self.objects[f"{prefix}/{name}"] = path.read_bytes()


class MissingKey(Exception):
    """Shaped like the driver's ClientError for a 404."""

    response = {"Error": {"Code": "NoSuchKey"}}


class FakeClient:
    """The single-object and listing half of the S3 API."""

    def __init__(self, backend: FakeS3):
        self.backend = backend

    def get_object(self, Bucket, Key):  # noqa: N803 - the S3 API's casing
        self.backend.calls.append(("get", Key))
        if Key not in self.backend.objects:
            raise MissingKey
        return {"Body": _Body(self.backend.objects[Key])}

    def put_object(self, Bucket, Key, Body, **kwargs):  # noqa: N803
        self.backend.calls.append(("put", Key))
        self.backend.objects[Key] = Body
        self.backend.put_kwargs[Key] = kwargs
        return {}

    def delete_objects(self, Bucket, Delete):  # noqa: N803
        for obj in Delete["Objects"]:
            self.backend.calls.append(("delete", obj["Key"]))
            self.backend.objects.pop(obj["Key"], None)
        return {}

    def get_paginator(self, _name):
        return self

    def paginate(self, Bucket, Prefix, Delimiter=None):  # noqa: N803
        if Delimiter:
            prefixes = set()
            for key in self.backend.objects:
                if not key.startswith(Prefix):
                    continue
                head, sep, _rest = key[len(Prefix):].partition(Delimiter)
                if sep:
                    prefixes.add(Prefix + head + Delimiter)
            return [{"CommonPrefixes": [{"Prefix": p} for p in sorted(prefixes)]}]
        contents = [
            {"Key": k, "Size": len(v)}
            for k, v in sorted(self.backend.objects.items())
            if k.startswith(Prefix)
        ]
        return [{"Contents": contents}]


class _Body:
    def __init__(self, raw: bytes):
        self.raw = raw

    def read(self) -> bytes:
        return self.raw

    def close(self) -> None:
        pass


@pytest.fixture
def remote(studio, settings, tmp_path, monkeypatch):
    settings.DATA_DIR = tmp_path
    settings.TRELLUM_STORAGE_BACKEND = "s3"
    settings.TRELLUM_REPORTS_BUCKET = "test-bucket"
    fake = FakeS3()
    monkeypatch.setattr(storage, "_s3", lambda: fake)
    monkeypatch.setattr(storage, "_client", lambda: FakeClient(fake))
    # The memo trades a few seconds of currency staleness for round trips. In
    # tests it would hide exactly the store consultations the assertions are
    # about, so it is off by default; TestPointerMemo turns it back on.
    monkeypatch.setattr(storage, "_POINTER_TTL_SECONDS", 0.0)
    storage._pointer_memo.clear()
    storage._locks.clear()
    return fake


def meta_bytes(last_run: str, status: str = "success") -> bytes:
    return json.dumps({"last_run": last_run, "last_status": status}).encode()


def install_build(
    fake: FakeS3,
    slug: str = "sales",
    build: str = "b1",
    files: dict[str, bytes] | None = None,
    last_run: str = "r1",
) -> str:
    """Put a complete published build into the fake bucket, pointer and all."""
    prefix = f"demo/casino/{slug}"
    fake.objects[f"{prefix}/_current"] = json.dumps({"build": build}).encode()
    fake.objects[f"{prefix}/_meta.json"] = meta_bytes(last_run)
    for name, body in (files or {"index.html": b"<h1>sales</h1>"}).items():
        fake.objects[f"{prefix}/builds/{build}/{name}"] = body
    return prefix


# ── The S3 client the portal now owns ───────────────────────────────────────

class TestS3ClientConfiguration:
    """The endpoint override is the setting that decides whether the portal
    talks to the operator's MinIO or to real AWS. If it stops being read,
    nothing raises — boto3 falls back to its default chain and the objects go
    somewhere the operator never configured."""

    def test_endpoint_and_credentials_come_from_settings(self, settings):
        from apps.core import s3 as s3mod

        settings.TRELLUM_STORAGE_ENDPOINT_URL = "http://minio:9000"
        settings.TRELLUM_STORAGE_ACCESS_KEY = "key"
        settings.TRELLUM_STORAGE_SECRET_KEY = "secret"
        settings.TRELLUM_STORAGE_REGION = "eu-west-1"

        captured = {}

        def fake_client(service, **kwargs):
            captured["service"] = service
            captured.update(kwargs)
            return object()

        import boto3

        real = boto3.client
        boto3.client = fake_client
        try:
            s3mod.s3_client()
        finally:
            boto3.client = real

        assert captured["service"] == "s3"
        assert captured["endpoint_url"] == "http://minio:9000"
        assert captured["aws_access_key_id"] == "key"
        assert captured["aws_secret_access_key"] == "secret"
        assert captured["region_name"] == "eu-west-1"
        # Non-AWS stores serve bucket-in-path; virtual-host style needs
        # wildcard DNS they do not have.
        assert captured["config"].s3["addressing_style"] == "path"

    def test_without_an_endpoint_the_default_credential_chain_is_left_alone(
        self, settings
    ):
        """An AWS deployment on an instance role configures none of this, and
        passing empty strings as keys would break it rather than defer to it."""
        from apps.core import s3 as s3mod

        settings.TRELLUM_STORAGE_ENDPOINT_URL = ""
        settings.TRELLUM_STORAGE_ACCESS_KEY = ""
        settings.TRELLUM_STORAGE_SECRET_KEY = ""
        settings.TRELLUM_STORAGE_REGION = ""

        captured = {}

        def fake_client(service, **kwargs):
            captured.update(kwargs)
            return object()

        import boto3

        real = boto3.client
        boto3.client = fake_client
        try:
            s3mod.s3_client()
        finally:
            boto3.client = real

        assert "endpoint_url" not in captured
        assert "aws_access_key_id" not in captured
        assert "region_name" not in captured
        assert captured["config"].s3["addressing_style"] == "auto"

    def test_bucket_defaults_to_the_setting(self, settings):
        from apps.core.s3 import S3Backend

        settings.TRELLUM_REPORTS_BUCKET = "reports-prod"
        assert S3Backend().bucket == "reports-prod"
        assert S3Backend(bucket="explicit").bucket == "explicit"


# ── Local backend ───────────────────────────────────────────────────────────

class TestLocalBackend:
    def test_output_root_is_the_studios_own_directory(self, studio, local_output):
        assert storage.output_root(studio, "sales") == local_output / "sales"

    def test_nothing_is_copied(self, studio, local_output, settings):
        storage.output_root(studio, "sales")
        assert not (settings.DATA_DIR / "cache").exists()

    def test_read_meta(self, studio, local_output):
        assert storage.read_meta(studio, "sales")["last_run"] == "2026-01-01T00:00:00Z"

    def test_read_meta_of_unbuilt_report_is_empty(self, studio, local_output):
        assert storage.read_meta(studio, "never-built") == {}

    def test_html_index_lists_built_reports_only(self, studio, local_output):
        assert storage.html_index(studio) == {
            "sales": ["index.html"],
            "churn": ["churn.html"],
        }

    def test_html_index_of_a_studio_with_no_output(self, studio, settings, tmp_path):
        settings.DATA_DIR = tmp_path
        settings.TRELLUM_STORAGE_BACKEND = "local"
        assert storage.html_index(studio) == {}

    def test_publish_is_a_no_op(self, studio, local_output):
        # Must not require a bucket, credentials, or an S3 driver to be installed.
        storage.publish_build(studio, "sales", str(local_output / "sales"), 1)
        storage.publish_meta(studio, "sales", str(local_output / "sales"))


class TestEntryFor:
    def test_prefers_index_html(self):
        assert storage.entry_for(["a.html", "index.html"]) == "index.html"

    def test_falls_back_to_the_first_file(self):
        assert storage.entry_for(["churn.html", "extra.html"]) == "churn.html"

    def test_no_output_still_names_something(self):
        assert storage.entry_for([]) == "index.html"


# ── Backend selection ────────────────────────────────────────────────────────

class TestObjectStorageSelection:
    @pytest.fixture(autouse=True)
    def _base(self, settings, tmp_path):
        settings.DATA_DIR = tmp_path
        settings.TRELLUM_REPORTS_BUCKET = "test-bucket"

    def test_s3_is_selected_directly(self, settings):
        settings.TRELLUM_STORAGE_BACKEND = "s3"
        settings.TRELLUM_FEATURES = {"object_storage": False}
        assert storage.is_remote() is True

    def test_local_remains_local(self, settings):
        settings.TRELLUM_STORAGE_BACKEND = "local"
        assert storage.is_remote() is False

    def test_missing_bucket_is_reported(self, settings):
        settings.TRELLUM_STORAGE_BACKEND = "s3"
        settings.TRELLUM_REPORTS_BUCKET = ""
        with pytest.raises(RuntimeError, match="TRELLUM_REPORTS_BUCKET"):
            storage.probe()


# ── Remote backend: reading ─────────────────────────────────────────────────

class TestRemoteBackend:
    def test_pull_addresses_the_current_builds_own_prefix(self, studio, remote):
        install_build(remote, build="b1")
        storage.output_root(studio, "sales")
        assert remote.pulls[0][0] == "demo/casino/sales/builds/b1"

    def test_output_is_materialised_and_readable(self, studio, remote):
        install_build(remote)
        root = storage.output_root(studio, "sales")
        assert (root / "index.html").read_bytes() == b"<h1>sales</h1>"

    def test_second_read_of_the_same_build_does_not_pull_again(self, studio, remote):
        install_build(remote)
        storage.output_root(studio, "sales")
        storage.output_root(studio, "sales")
        assert len(remote.pulls) == 1

    def test_a_new_build_is_pulled_and_replaces_the_old_one(self, studio, remote):
        install_build(remote, build="b1", files={"index.html": b"<h1>old</h1>"})
        first = storage.output_root(studio, "sales")
        assert (first / "index.html").read_bytes() == b"<h1>old</h1>"

        install_build(remote, build="b2", files={"index.html": b"<h1>new</h1>"})
        second = storage.output_root(studio, "sales")
        assert second != first
        assert (second / "index.html").read_bytes() == b"<h1>new</h1>"
        assert len(remote.pulls) == 2

    def test_superseded_builds_are_pruned_from_the_cache(self, studio, remote):
        install_build(remote, build="b1")
        first = storage.output_root(studio, "sales")
        install_build(remote, build="b2")
        storage.output_root(studio, "sales")
        assert not first.exists()

    def test_an_unbuilt_report_does_not_pull(self, studio, remote):
        root = storage.output_root(studio, "never-built")
        assert not root.exists()
        assert remote.pulls == []

    def test_serving_follows_the_pointer_not_the_meta_timestamp(self, studio, remote):
        """The regression that motivated the pointer: a failed run bumps
        ``last_run`` in ``_meta.json`` (framework ``write_error``), and serving
        keyed on that timestamp would repoint at a build that never completed.
        Status may say anything; only ``_current`` decides what serves."""
        prefix = install_build(remote, build="b1", files={"index.html": b"<h1>good</h1>"})
        storage.output_root(studio, "sales")

        # A failed run travels: meta now carries a NEWER timestamp and an
        # error — but the pointer still names b1.
        remote.objects[f"{prefix}/_meta.json"] = meta_bytes("r2-failed", status="error")
        root = storage.output_root(studio, "sales")
        assert (root / "index.html").read_bytes() == b"<h1>good</h1>"
        assert len(remote.pulls) == 1  # nothing new to fetch

    def test_a_failed_pull_leaves_no_half_written_directory(self, studio, remote):
        install_build(remote)

        def boom(prefix, local_dir):
            raise RuntimeError("connection reset")

        remote.pull = boom
        with pytest.raises(RuntimeError):
            storage.output_root(studio, "sales")
        cached = storage._cache_root(studio, "sales")
        assert not any(p.is_dir() for p in cached.iterdir())

    def test_the_materialise_lock_is_dropped_after_use(self, studio, remote):
        install_build(remote)
        storage.output_root(studio, "sales")
        assert storage._locks == {}

    def test_read_meta_gunzips_what_publish_compressed(self, studio, remote):
        import gzip

        remote.objects = {
            "demo/casino/sales/_meta.json": gzip.compress(meta_bytes("r1")),
        }
        assert storage.read_meta(studio, "sales")["last_run"] == "r1"

    def test_html_index_is_one_listing_for_the_whole_studio(self, studio, remote):
        install_build(remote, slug="sales", files={"index.html": b"x", "data.json": b"{}"})
        install_build(remote, slug="churn", files={"churn.html": b"x"})
        # A report that only ever failed has status objects but no build.
        remote.objects["demo/casino/queued/_meta.json"] = meta_bytes("r1", "error")
        assert storage.html_index(studio) == {
            "sales": ["index.html"],
            "churn": ["churn.html"],
        }

    def test_html_index_reads_the_newest_build_per_report(self, studio, remote):
        install_build(remote, build="2026-01-01-r1", files={"old.html": b"x"})
        install_build(remote, build="2026-01-02-r2", files={"new.html": b"x"})
        assert storage.html_index(studio) == {"sales": ["new.html"]}

    def test_html_index_ignores_another_studio(self, studio, studio2, remote):
        install_build(remote, slug="sales")
        remote.objects["demo/arcade/other/builds/b1/index.html"] = b"x"
        assert set(storage.html_index(studio)) == {"sales"}
        assert set(storage.html_index(studio2)) == {"other"}


# ── Remote backend: the publish protocol ────────────────────────────────────

class TestPublishProtocol:
    """Build files first, status second, pointer last — the order is the
    correctness property. Until the pointer flips, readers still resolve the
    previous complete build; the moment it flips, the new one is complete by
    construction."""

    @pytest.fixture
    def out_dir(self, tmp_path):
        out = tmp_path / "out" / "sales"
        out.mkdir(parents=True)
        (out / "index.html").write_bytes(b"<h1>new</h1>")
        (out / "_meta.json").write_bytes(meta_bytes("2026-02-01T00:00:00"))
        return out

    def test_pointer_flips_only_after_everything_is_uploaded(
        self, studio, remote, out_dir
    ):
        storage.publish_build(studio, "sales", str(out_dir), 7)
        kinds = [
            (op, key) for op, key in remote.calls if op in ("publish", "put")
        ]
        assert kinds == [
            ("publish", "demo/casino/sales/builds/2026-02-01T00_00_00-r7"),
            ("publish", "demo/casino/sales"),  # _meta.json at the top level
            ("put", "demo/casino/sales/_current"),
        ]

    def test_the_pointer_names_the_published_build(self, studio, remote, out_dir):
        storage.publish_build(studio, "sales", str(out_dir), 7)
        pointer = json.loads(remote.objects["demo/casino/sales/_current"])
        assert pointer["build"] == "2026-02-01T00_00_00-r7"
        assert (
            remote.objects[
                "demo/casino/sales/builds/2026-02-01T00_00_00-r7/index.html"
            ]
            == b"<h1>new</h1>"
        )

    def test_the_pointer_is_never_cacheable(self, studio, remote, out_dir):
        """A CDN or proxy that cached currency would defeat the whole design."""
        storage.publish_build(studio, "sales", str(out_dir), 7)
        kwargs = remote.put_kwargs["demo/casino/sales/_current"]
        assert kwargs.get("CacheControl") == "no-cache"

    def test_web_readers_see_the_new_build_immediately_after_publish(
        self, studio, remote, out_dir
    ):
        install_build(remote, build="b1", files={"index.html": b"<h1>old</h1>"})
        storage.output_root(studio, "sales")
        storage.publish_build(studio, "sales", str(out_dir), 7)
        root = storage.output_root(studio, "sales")
        assert (root / "index.html").read_bytes() == b"<h1>new</h1>"

    def test_publish_keeps_the_previous_build_and_prunes_older_ones(
        self, studio, remote, out_dir
    ):
        """A reader that resolved the pointer just before the flip may still be
        pulling the previous build; only builds older than that are deleted."""
        install_build(remote, build="a1", files={"index.html": b"1"})
        remote.objects["demo/casino/sales/builds/a2/index.html"] = b"2"
        storage.publish_build(studio, "sales", str(out_dir), 7)
        keys = set(remote.objects)
        assert "demo/casino/sales/builds/a1/index.html" not in keys
        assert "demo/casino/sales/builds/a2/index.html" in keys
        assert (
            "demo/casino/sales/builds/2026-02-01T00_00_00-r7/index.html" in keys
        )

    def test_publish_meta_moves_status_and_nothing_else(self, studio, remote, out_dir):
        """The failure path: the error must travel, the pointer must not move,
        and not one output file may be overwritten."""
        install_build(remote, build="b1", files={"index.html": b"<h1>good</h1>"})
        (out_dir / "_meta.json").write_bytes(meta_bytes("r2-failed", "error"))
        before = dict(remote.objects)

        storage.publish_meta(studio, "sales", str(out_dir))

        changed = {
            k for k in set(before) | set(remote.objects)
            if before.get(k) != remote.objects.get(k)
        }
        assert changed == {"demo/casino/sales/_meta.json"}
        assert storage.read_meta(studio, "sales")["last_status"] == "error"
        # And the last good build still serves.
        root = storage.output_root(studio, "sales")
        assert (root / "index.html").read_bytes() == b"<h1>good</h1>"

    def test_publish_meta_without_a_meta_file_is_a_no_op(self, studio, remote, tmp_path):
        empty = tmp_path / "empty"
        empty.mkdir()
        storage.publish_meta(studio, "sales", str(empty))
        assert remote.calls == []


# ── Concurrency across processes ────────────────────────────────────────────

class TestConcurrentMaterialisation:
    """The in-process lock cannot cover gunicorn's separate worker processes,
    so the staging discipline itself has to be collision-free: unique staging
    names per attempt, and losing the rename race is a success."""

    def test_two_racing_attempts_both_succeed(self, studio, remote):
        install_build(remote)
        target = storage._build_dir(studio, "sales", "b1")
        barrier = threading.Barrier(2, timeout=5)
        real_pull = remote.pull

        def slow_pull(prefix, local_dir):
            barrier.wait()  # both attempts are mid-pull at the same time
            real_pull(prefix, local_dir)

        remote.pull = slow_pull
        errors: list[Exception] = []

        def attempt():
            try:
                storage._materialise(studio, "sales", "b1", target)
            except Exception as exc:  # noqa: BLE001 - collected for the assert
                errors.append(exc)

        threads = [threading.Thread(target=attempt) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        assert errors == []
        assert (target / "index.html").read_bytes() == b"<h1>sales</h1>"
        # Neither attempt's staging survives.
        leftovers = [p for p in target.parent.iterdir() if ".part-" in p.name]
        assert leftovers == []

    def test_pruning_never_touches_another_attempts_staging(self, studio, remote):
        root = storage._cache_root(studio, "sales")
        (root / "b2").mkdir(parents=True)
        (root / "b1").mkdir()
        (root / "b2.part-abc123").mkdir()  # someone else, mid-pull
        storage._prune_old_versions(studio, "sales", keep="b2")
        assert not (root / "b1").exists()
        assert (root / "b2").exists()
        assert (root / "b2.part-abc123").exists()


# ── The pointer memo ────────────────────────────────────────────────────────

class TestPointerMemo:
    """A report page fetches dozens of assets in a burst; each one asks which
    build is current. The memo answers from memory for a few seconds so that
    is one small GET per report per window, not one per asset."""

    @pytest.fixture
    def memo_on(self, remote, monkeypatch):
        monkeypatch.setattr(storage, "_POINTER_TTL_SECONDS", 30.0)
        return remote

    def _pointer_gets(self, fake):
        return [
            key for op, key in fake.calls
            if op == "get" and key.endswith("/_current")
        ]

    def test_a_burst_of_reads_asks_the_store_once(self, studio, memo_on):
        install_build(memo_on)
        for _ in range(5):
            storage.output_root(studio, "sales")
        assert len(self._pointer_gets(memo_on)) == 1

    def test_publishing_refreshes_this_nodes_answer_immediately(
        self, studio, memo_on, tmp_path
    ):
        install_build(memo_on, build="b1", files={"index.html": b"<h1>old</h1>"})
        storage.output_root(studio, "sales")

        out = tmp_path / "out"
        out.mkdir()
        (out / "index.html").write_bytes(b"<h1>new</h1>")
        (out / "_meta.json").write_bytes(meta_bytes("r2"))
        storage.publish_build(studio, "sales", str(out), 9)

        root = storage.output_root(studio, "sales")
        assert (root / "index.html").read_bytes() == b"<h1>new</h1>"

    def test_clear_cache_also_forgets_the_memo(self, studio, memo_on):
        install_build(memo_on)
        storage.output_root(studio, "sales")
        storage.clear_cache(studio)
        storage.output_root(studio, "sales")
        assert len(self._pointer_gets(memo_on)) == 2


# ── The store going down ────────────────────────────────────────────────────

class TestTheStoreGoingDown:
    """An unreachable store is an outage, and must read as one.

    Choosing object storage makes the bucket authoritative. A node's cache is a
    copy of one specific build, valid only while the store agrees that build is
    current — so it is a transfer optimisation, never a second opinion. Serving
    from it during an outage would answer with whichever version this node last
    happened to download, which is not what the reader asked for.
    """

    def _cache_a_build(self, studio, remote):
        install_build(remote, files={"index.html": b"<h1>cached</h1>"})
        return storage.output_root(studio, "sales")

    def _unplug(self, monkeypatch):
        def down(*_a, **_kw):
            raise OSError("connection refused")

        monkeypatch.setattr(storage, "_client", down)
        monkeypatch.setattr(storage, "_s3", down)

    def test_a_cached_report_is_not_served_from_disk(self, studio, remote, monkeypatch):
        """Even with a complete, valid copy on disk, an unreachable store fails."""
        cached = self._cache_a_build(studio, remote)
        assert (cached / "index.html").is_file()  # it really is there
        self._unplug(monkeypatch)
        with pytest.raises(OSError):
            storage.output_root(studio, "sales")

    def test_an_outage_is_never_memoised(self, studio, remote, monkeypatch):
        """The memo holds answers, not errors: once the store recovers, the
        next read gets the real pointer rather than a remembered failure —
        and during the outage no reader gets a remembered success."""
        monkeypatch.setattr(storage, "_POINTER_TTL_SECONDS", 30.0)
        install_build(remote)
        real_client = storage._client

        def down(*_a, **_kw):
            raise OSError("connection refused")

        monkeypatch.setattr(storage, "_client", down)
        with pytest.raises(OSError):
            storage.output_root(studio, "sales")
        monkeypatch.setattr(storage, "_client", real_client)
        assert storage.output_root(studio, "sales").is_dir()

    def test_a_report_never_cached_here_raises_too(self, studio, remote, monkeypatch):
        self._unplug(monkeypatch)
        with pytest.raises(OSError):
            storage.output_root(studio, "never-seen")

    def test_a_failed_pull_raises_rather_than_serving_an_older_build(
        self, studio, remote
    ):
        self._cache_a_build(studio, remote)
        # A newer build exists, but the store dies mid-pull.
        install_build(remote, build="b2")

        def boom(*_a, **_kw):
            raise OSError("reset by peer")

        remote.pull = boom
        with pytest.raises(OSError):
            storage.output_root(studio, "sales")

    def test_the_dashboard_listing_does_not_invent_an_answer(
        self, studio, remote, monkeypatch
    ):
        self._cache_a_build(studio, remote)
        self._unplug(monkeypatch)
        with pytest.raises(OSError):
            storage.html_index(studio)

    def test_read_meta_raises(self, studio, remote, monkeypatch):
        self._unplug(monkeypatch)
        with pytest.raises(OSError):
            storage.read_meta(studio, "sales")

    def test_a_missing_report_is_still_not_an_outage(self, studio, remote):
        """The distinction that has to survive: absent is not unreachable."""
        remote.objects = {}
        assert storage.read_meta(studio, "sales") == {}
        assert not storage.output_root(studio, "sales").exists()

    def test_a_cache_hit_while_the_store_is_up_is_a_verified_copy(
        self, studio, remote
    ):
        """The cache is still read on the happy path — but only after the store
        has confirmed which build is current, which is what keeps it an
        optimisation rather than a source of truth."""
        first = self._cache_a_build(studio, remote)
        second = storage.output_root(studio, "sales")
        assert second == first
        assert len(remote.pulls) == 1  # served from disk, build checked first



class TestDeletingOutput:
    """Retention's half of this module. The one thing that must not happen is
    a report going from one of the three places output lives while another
    keeps serving it, so every test here asserts on all three."""

    def test_local_output_and_cache_both_go(self, studio, local_output, tmp_path):
        cached = tmp_path / "cache" / "output" / "demo" / "casino" / "sales"
        cached.mkdir(parents=True)
        (cached / "index.html").write_text("<h1>stale</h1>", encoding="utf-8")

        freed = storage.delete_output(studio, "sales")

        assert not (local_output / "sales").exists()
        assert not cached.exists()
        assert freed >= len("<h1>sales</h1>") + len("<h1>stale</h1>")
        # A sibling report is untouched.
        assert (local_output / "churn" / "churn.html").is_file()

    def test_the_whole_remote_prefix_goes_pointer_included(self, studio, remote):
        install_build(remote, slug="sales", build="b1")
        install_build(remote, slug="sales-eu", build="b1")

        storage.delete_output(studio, "sales")

        assert not [k for k in remote.objects if k.startswith("demo/casino/sales/")]
        # The trailing slash matters: the neighbour whose slug merely starts
        # the same way must survive.
        assert any(k.startswith("demo/casino/sales-eu/") for k in remote.objects)

    def test_it_reports_the_bytes_it_removed(self, studio, remote):
        install_build(remote, slug="sales", files={"index.html": b"x" * 500})
        assert storage.delete_output(studio, "sales") >= 500

    def test_output_bytes_measures_what_delete_would_free(self, studio, remote):
        install_build(remote, slug="sales", files={"index.html": b"x" * 500})
        measured = storage.output_bytes(studio, "sales")
        assert measured >= 500
        assert storage.delete_output(studio, "sales") == measured

    def test_output_bytes_removes_nothing(self, studio, local_output):
        assert storage.output_bytes(studio, "sales") > 0
        assert (local_output / "sales" / "index.html").is_file()

    def test_a_report_that_never_built_is_not_an_error(self, studio, local_output):
        assert storage.delete_output(studio, "never-built") == 0

    def test_a_whole_studio_goes_when_the_slug_is_omitted(self, studio, local_output):
        """What retention addresses when the Studio row itself is gone and the
        slugs it held are no longer knowable from the database."""
        freed = storage.delete_output(studio)
        assert freed > 0
        assert not (local_output / "sales").exists()
        assert not (local_output / "churn").exists()

    def test_a_deleted_report_stops_being_current(self, studio, remote):
        """The pointer goes with the build it names — a _current left behind
        would point every reader at a prefix that is no longer there."""
        install_build(remote, slug="sales", build="b1")
        assert storage.current_build(studio, "sales") == "b1"
        storage.delete_output(studio, "sales")
        assert storage.current_build(studio, "sales") == ""
