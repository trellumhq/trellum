"""Where the web process reads built report output from.

A build writes its output through the framework's output backend; the web
process then has to *read* that output back to serve it. When both halves share
a filesystem those are the same directory and there is nothing to do. When the
runner publishes to object storage they are not, and the gap between them is
what this module closes.

Two backends, chosen by ``TRELLUM_STORAGE_BACKEND``:

    local   runner and web share a filesystem. ``output_root`` is the studio's
            own output directory, nothing is copied, and this module is a thin
            pass-through. The default, and the only supported single-VM shape.

    s3      the runner publishes to a bucket. The web process pulls what it
            needs into a local read cache under ``<DATA_DIR>/cache/output/``
            and serves from there.

The cache is what keeps the rest of the portal path-based. Report serving does
path-traversal checks, sniffs the HTML entry point, rewrites markup at serve
time and hands a file to ``FileResponse`` — all of it filesystem work that would
have to be rewritten against a byte-stream API for no benefit. Materialising a
report once per build and letting that code run unchanged is both a smaller
change and a faster read path.

Bucket layout — builds are immutable, currency is a pointer::

    {org}/{studio}/{slug}/_meta.json           status; updated after EVERY run
    {org}/{studio}/{slug}/_current             {"build": <id>}; flipped only
                                               after a complete upload
    {org}/{studio}/{slug}/builds/{build}/...   one build's full output;
                                               written once, never mutated

Why a pointer and not a timestamp: uploads are many PUTs and take time, so any
scheme that overwrites a live prefix has a window where the store holds half of
one build and half of another — and a reader who pulls inside that window
caches the blend. Here every build lands in its own fresh prefix and a single
atomic PUT of ``_current`` is what makes it live, so a reader sees the old
complete build or the new complete build, never a mixture. The pointer also
decouples serving from ``_meta.json``'s ``last_run``, which failed runs bump
too (``trellum.meta.write_error``) — status travels on every run, but the
pointer only ever names a build that finished.

**The cache is a transfer optimisation, never a source of truth.** Every read
asks the store which build is current and only then looks for that exact build
on disk, so a cache hit is a verified copy rather than a guess. (The pointer
read is memoised for a few seconds — one small GET per report per window
instead of one per asset request — which bounds staleness at the memo TTL, no
worse than a CDN would do.) Nothing here falls back to a cached build when the
store cannot be reached: choosing object storage makes the bucket
authoritative, and answering from whichever version this node last happened to
download would serve a question nobody asked. An unreachable store is an
outage and reads as one.

What this module deliberately does NOT cover: uploaded data-source files, which
travel the other way (the web process writes them, a build reads them) and are
the other half of making a node stateless. They still require shared storage.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import threading
import time
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

#: Guards cache materialisation so two concurrent requests in the same process
#: do not both pull a build. Keyed by cache directory; entries are dropped once
#: the directory exists, so the dict never outgrows the builds in flight.
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()

#: Memoises ``_current`` pointer reads: {prefix: (monotonic fetched_at, build)}.
#: Serving a report page fetches dozens of assets in a burst; without this each
#: asset would be its own round trip to the store for an answer that changes
#: at most once per build.
_POINTER_TTL_SECONDS = 5.0
_pointer_memo: dict[str, tuple[float, str]] = {}
_pointer_memo_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())


def backend_name() -> str:
    """What the operator asked for: ``local`` or ``s3``. Unset means local."""
    return (getattr(settings, "TRELLUM_STORAGE_BACKEND", "") or "local").strip().lower()


def is_remote() -> bool:
    """Whether the operator selected object storage."""
    return backend_name() == "s3"


def _bucket() -> str:
    return getattr(settings, "TRELLUM_REPORTS_BUCKET", "") or ""


def _s3():
    """The portal's S3 backend — whole-prefix publish and pull.

    Imported lazily so a local deployment never loads boto3 at all.
    """
    from apps.core.s3 import S3Backend

    return S3Backend(bucket=_bucket() or None)


def _client():
    """A raw S3 client, for the single-object and listing calls.

    ``S3Backend`` covers publishing and pulling a whole report. Reading or
    writing one small object, or listing a studio, is not worth a directory
    round trip.
    """
    from apps.core.s3 import s3_client

    return s3_client()


def _remote_prefix(studio, slug: str | None = None) -> str:
    """Key prefix a studio's report occupies in the bucket.

    One bucket serves every tenant, so the prefix carries the org and studio.
    This must match what the runner publishes under — see
    ``apps.runner.executor.child_env``. ``slug=None`` gives the studio's whole
    prefix, which is what retention addresses when a studio itself is gone.
    """
    base = f"{studio.org.slug}/{studio.slug}"
    return base if slug is None else f"{base}/{slug}"


def _cache_root(studio, slug: str | None = None) -> Path:
    root = (
        Path(settings.DATA_DIR) / "cache" / "output" / studio.org.slug / studio.slug
    )
    return root if slug is None else root / slug


def _safe_component(value: str) -> str:
    # Build ids embed an ISO timestamp; ':' and '+' are not portable in path
    # names (and are awkward in S3 keys). Same-length substitution keeps the
    # sanitised form sorting the way the timestamp does.
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in value) or "none"


def _build_dir(studio, slug: str, build: str) -> Path:
    return _cache_root(studio, slug) / _safe_component(build)


def _pointer_key(prefix: str) -> str:
    return f"{prefix}/_current"


def _build_prefix(prefix: str, build: str) -> str:
    return f"{prefix}/builds/{build}"


def read_meta(studio, slug: str) -> dict:
    """``_meta.json`` for one report, without materialising its whole output.

    The registry renders every report in a studio, so this is on a hot path and
    must stay a single small read per report rather than a directory pull.
    """
    if not is_remote():
        from trellum.meta import read_meta as _read

        return _read(str(studio.output_dir / slug))

    key = f"{_remote_prefix(studio, slug)}/_meta.json"
    try:
        raw = _get_object_bytes(key)
    except FileNotFoundError:
        return {}
    if not raw:
        return {}
    try:
        return json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}


#: Live-query manifest a build may write beside ``_meta.json`` — see
#: apps/reports/livequery.py for the shape and the endpoint that reads it.
_LIVE_QUERIES_NAME = "_live_queries.json"


def live_queries_version(studio, slug: str) -> str:
    """Cheap identity of the manifest :func:`read_live_queries` would return.

    The current build id on the remote backend (a memoised pointer read, no
    content fetched), the manifest file's mtime locally, and ``""`` when
    there is nothing to read. Lets the endpoint cache the parsed manifest
    per report+build without a content read per request.
    """
    if is_remote():
        return _current_build(studio, slug)
    try:
        return str((studio.output_dir / slug / _LIVE_QUERIES_NAME).stat().st_mtime_ns)
    except OSError:
        return ""


def read_live_queries(studio, slug: str) -> dict:
    """``_live_queries.json`` for one report's current build, or ``{}``.

    Mirrors :func:`read_meta`: a single small read, never a directory pull.
    Unlike ``_meta.json`` the manifest is part of one build's immutable
    output, so on the remote backend it lives under ``builds/{build}/`` and
    the read is pointer-gated exactly like serving is.
    """
    if not is_remote():
        try:
            raw = (studio.output_dir / slug / _LIVE_QUERIES_NAME).read_bytes()
        except OSError:
            return {}
    else:
        build = _current_build(studio, slug)
        if not build:
            return {}
        key = f"{_build_prefix(_remote_prefix(studio, slug), build)}/{_LIVE_QUERIES_NAME}"
        try:
            raw = _get_object_bytes(key)
        except FileNotFoundError:
            return {}
    if not raw:
        return {}
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _get_object_bytes(key: str) -> bytes:
    """Fetch one object, transparently gunzipping if it was stored compressed.

    ``S3Backend.publish`` uploads .json pre-gzipped with ``Content-Encoding:
    gzip`` so a CDN can serve it as-is. A raw ``get_object`` therefore
    returns gzip bytes, and parsing them as JSON would fail.
    """
    import gzip

    try:
        response = _client().get_object(Bucket=_bucket(), Key=key)
    except Exception as exc:  # noqa: BLE001 - the driver raises per-operation classes
        if _is_missing(exc):
            raise FileNotFoundError(key) from exc
        raise
    body = response["Body"].read()
    if response.get("ContentEncoding") == "gzip" or body[:2] == b"\x1f\x8b":
        body = gzip.decompress(body)
    return body


def _is_missing(exc: Exception) -> bool:
    code = getattr(exc, "response", {}).get("Error", {}).get("Code", "")
    return code in ("NoSuchKey", "404", "NotFound")


def _current_build(studio, slug: str) -> str:
    """Which build ``_current`` names, or "" if the report never published.

    Memoised for a few seconds per report. Only answers are memoised — a store
    error propagates and is never cached, so an outage cannot linger in the
    memo after the store recovers (nor masquerade as "never built").
    """
    prefix = _remote_prefix(studio, slug)
    now = time.monotonic()
    with _pointer_memo_guard:
        entry = _pointer_memo.get(prefix)
        if entry is not None and now - entry[0] < _POINTER_TTL_SECONDS:
            return entry[1]

    try:
        raw = _get_object_bytes(_pointer_key(prefix))
    except FileNotFoundError:
        build = ""
    else:
        try:
            build = str(json.loads(raw.decode("utf-8")).get("build") or "")
        except (ValueError, UnicodeDecodeError):
            build = ""

    with _pointer_memo_guard:
        _pointer_memo[prefix] = (now, build)
    return build


def _forget_pointer(prefix: str) -> None:
    with _pointer_memo_guard:
        _pointer_memo.pop(prefix, None)


def current_build(studio, slug: str) -> str:
    """Which build is live for one report, or "" when none is.

    The public form of the pointer read, for callers that need the build id
    itself rather than materialised output — the CDN redirect path, which
    must know the current build to name it in an immutable URL without
    pulling a single content byte through this process. Memoised like every
    pointer read; raises when the store is unreachable. Always "" on the
    local backend, where builds have no ids.
    """
    if not is_remote():
        return ""
    return _current_build(studio, slug)


def output_root(studio, slug: str) -> Path:
    """A local directory holding this report's built output.

    Local backend: the studio's own output directory, untouched. Remote: a
    cache directory, materialised on first use after each build.

    The returned path may not exist — a report that has never built has no
    output, and callers already handle that (it is how ``report_unbuilt`` is
    reached). Never raises for a missing report.
    """
    if not is_remote():
        return studio.output_dir / slug

    # No fallback if this raises. Choosing object storage makes the bucket the
    # source of truth, and a node's cache is not a second opinion about what a
    # report currently is — it is a copy of one specific build, valid only for
    # as long as the store agrees that build is the current one. Serving it
    # while the store is unreachable would answer a question nobody asked: the
    # reader wants this report, not whichever version this node last happened
    # to download. An unreachable store is an outage, and it should read as one.
    build = _current_build(studio, slug)

    if not build:
        # Never published, or built by a portal old enough not to write the
        # pointer. Either way there is nothing to serve; hand back a path that
        # does not exist rather than pulling a prefix to discover that.
        return _build_dir(studio, slug, "none")

    target = _build_dir(studio, slug, build)
    if target.is_dir():
        # Not a stale read: the build id came from the store a moment ago, and
        # builds are immutable — this directory is a verified copy of exactly
        # the build the store says is current. That is what makes the cache a
        # transfer optimisation rather than a source of truth.
        return target

    with _lock_for(str(target)):
        # Re-check: another thread may have materialised it while we waited.
        if not target.is_dir():
            _materialise(studio, slug, build, target)
    with _locks_guard:
        # The directory now exists, so the fast path above answers every later
        # call and this lock is dead weight. Dropping it keeps the dict from
        # growing by one entry per build forever.
        _locks.pop(str(target), None)
    return target


def _materialise(studio, slug: str, build: str, target: Path) -> None:
    """Pull one build's output into *target*, atomically.

    Pulls into a uniquely-named staging directory and renames, so a reader
    never sees a partially-downloaded report. The staging name is unique per
    attempt (not a shared ``.part``): gunicorn runs several worker processes,
    and a per-process lock cannot stop two of them pulling the same build at
    once — with a shared name they would delete and rename each other's
    half-finished work. With unique names each attempt is self-contained, and
    losing the rename race is fine: the other attempt installed an identical
    copy of the same immutable build.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(
        tempfile.mkdtemp(prefix=target.name + ".part-", dir=str(target.parent))
    )
    try:
        _s3().pull(_build_prefix(_remote_prefix(studio, slug), build), str(staging))
        if not target.is_dir():
            try:
                os.replace(staging, target)
            except OSError:
                # Lost the race to another process after our check (os.replace
                # cannot replace an existing directory on Windows). Their copy
                # of this immutable build is identical; keep it, drop ours.
                if not target.is_dir():
                    raise
    finally:
        shutil.rmtree(staging, ignore_errors=True)
    _prune_old_versions(studio, slug, keep=target.name)


def _prune_old_versions(studio, slug: str, *, keep: str) -> None:
    """Drop cache directories for superseded builds.

    Best-effort: a request currently serving a file out of an older directory
    keeps its open handle on POSIX, and on Windows the removal simply fails and
    is retried after the next build. Staging directories (``.part-``) belong to
    other in-flight attempts and are never touched — each attempt cleans up its
    own.
    """
    root = _cache_root(studio, slug)
    try:
        entries = list(root.iterdir())
    except OSError:
        return
    for entry in entries:
        if entry.name == keep or ".part-" in entry.name or not entry.is_dir():
            continue
        shutil.rmtree(entry, ignore_errors=True)


def html_index(studio) -> dict[str, list[str]]:
    """``{slug: [html filenames]}`` for every built report in a studio.

    The registry needs two things per report — whether it has any HTML output,
    and which file is the entry point. Both are answerable from names alone, so
    this never reads file contents and never materialises a report.

    On the remote backend it is a single paginated listing of the studio's
    prefix, not one call per report: the registry renders a whole studio at
    once and per-report round trips would dominate it. The listing sees every
    ``builds/{build}/`` prefix; the newest build per report wins (ids embed the
    run timestamp, so lexicographic order is chronological). That can surface a
    build still uploading a few seconds before its pointer flips — but only its
    *filenames*, which is all this answers; serving itself is strictly
    pointer-gated.
    """
    if not is_remote():
        index: dict[str, list[str]] = {}
        root = studio.output_dir
        try:
            entries = sorted(p for p in root.iterdir() if p.is_dir())
        except OSError:
            return {}
        for report_dir in entries:
            names = sorted(
                f.name for f in report_dir.iterdir()
                if f.is_file() and f.name.endswith(".html")
            )
            if names:
                index[report_dir.name] = names
        return index

    prefix = f"{studio.org.slug}/{studio.slug}/"
    per_slug: dict[str, dict[str, list[str]]] = {}
    paginator = _client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=_bucket(), Prefix=prefix):
        for obj in page.get("Contents", []):
            rest = obj["Key"][len(prefix):]
            parts = rest.split("/")
            # Only {slug}/builds/{build}/{file} at the build's own top level
            # holds an entry point; _meta.json, _current and nested assets
            # all fall out of the length check.
            if len(parts) != 4:
                continue
            slug, marker, build, filename = parts
            if marker != "builds" or not filename.endswith(".html"):
                continue
            per_slug.setdefault(slug, {}).setdefault(build, []).append(filename)
    return {
        slug: sorted(builds[max(builds)])
        for slug, builds in per_slug.items()
    }


def live_query_index(studio) -> set[str]:
    """Slugs whose current build published ``_live_queries.json`` — reports
    that run queries at view time rather than serving a static snapshot.

    One bulk listing per studio, the same shape as :func:`html_index`, so the
    registry can flag live reports without a per-report round trip. The file's
    presence is the whole signal; its contents are never read here.
    """
    if not is_remote():
        out: set[str] = set()
        root = studio.output_dir
        try:
            entries = [p for p in root.iterdir() if p.is_dir()]
        except OSError:
            return out
        for report_dir in entries:
            if (report_dir / "_live_queries.json").is_file():
                out.add(report_dir.name)
        return out

    # Remote: per slug, which builds exist and whether each carries the
    # manifest; the newest build wins (build ids embed the run timestamp, so
    # lexicographic max is chronological — the same rule html_index uses).
    prefix = f"{studio.org.slug}/{studio.slug}/"
    per_slug: dict[str, dict[str, bool]] = {}
    paginator = _client().get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=_bucket(), Prefix=prefix):
        for obj in page.get("Contents", []):
            rest = obj["Key"][len(prefix):]
            parts = rest.split("/")
            if len(parts) != 4:
                continue
            slug, marker, build, filename = parts
            if marker != "builds":
                continue
            builds = per_slug.setdefault(slug, {})
            builds.setdefault(build, False)
            if filename == "_live_queries.json":
                builds[build] = True
    return {slug for slug, builds in per_slug.items() if builds[max(builds)]}


def entry_for(names: list[str]) -> str:
    """Pick a report's HTML entry point from its filenames."""
    if "index.html" in names:
        return "index.html"
    return names[0] if names else "index.html"


def publish_build(studio, slug: str, output_dir: str, run_id) -> None:
    """Push a finished build's output to the remote store and make it live.

    No-op on the local backend, where the build already wrote where the web
    process reads. Raises on failure — the caller decides what a build whose
    output nobody can reach should be recorded as, and silently swallowing it
    would leave a "successful" report that 404s.

    Order matters and is the whole point:

    1. the full output goes to a fresh, immutable ``builds/{build}/`` prefix —
       nothing readers currently use is touched;
    2. ``_meta.json`` lands at the report's top level, so the registry's
       status reflects this run;
    3. one atomic PUT flips ``_current`` — only now does the new build exist
       as far as serving is concerned, and it is complete by construction;
    4. superseded builds are pruned, keeping the one just replaced so a reader
       mid-pull of it never loses objects under its feet.
    """
    if not is_remote():
        return
    prefix = _remote_prefix(studio, slug)
    build = _build_id(output_dir, run_id)

    _s3().publish(output_dir, _build_prefix(prefix, build))
    _publish_meta_file(output_dir, prefix)
    _put_pointer(prefix, build)
    # This node should serve what it just made live, not a memoised "5 seconds
    # ago"; other nodes converge within the TTL.
    _forget_pointer(prefix)
    _prune_remote_builds(prefix, keep=build)


def publish_meta(studio, slug: str, output_dir: str) -> None:
    """Push only ``_meta.json`` to the remote store — the failure path.

    A failed run must still travel: the registry reads status from the bucket,
    and a failure that stays on the runner's disk leaves every web node showing
    the previous outcome forever. But *only* the status travels — the output
    directory of a failed run holds whatever mix of old and half-written files
    the build died with, and ``_current`` never moves, so the last good build
    keeps serving untouched.
    """
    if not is_remote():
        return
    _publish_meta_file(output_dir, _remote_prefix(studio, slug))


def _build_id(output_dir: str, run_id) -> str:
    """A unique, chronologically-sortable id for this build.

    Prefixed with the framework's ``last_run`` stamp so ids sort in build
    order; suffixed with the run's pk so two builds finishing within the same
    second can never collide on a prefix.
    """
    from trellum.meta import read_meta as _read_local

    last_run = str(_read_local(output_dir).get("last_run") or "")
    if not last_run:
        from datetime import datetime, timezone

        last_run = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%S")
    return f"{_safe_component(last_run)}-r{run_id}"


def _publish_meta_file(output_dir: str, prefix: str) -> None:
    meta_path = Path(output_dir) / "_meta.json"
    if not meta_path.is_file():
        return
    # Through the backend publish rather than a raw PUT so the object gets
    # the same gzip/content-type/cache-control treatment as every other
    # _meta.json it uploads — a directory of one file is the price of one
    # convention instead of two.
    with tempfile.TemporaryDirectory() as tmp:
        shutil.copy2(meta_path, Path(tmp) / "_meta.json")
        _s3().publish(tmp, prefix)


def _put_pointer(prefix: str, build: str) -> None:
    _client().put_object(
        Bucket=_bucket(),
        Key=_pointer_key(prefix),
        Body=json.dumps({"build": build}).encode("utf-8"),
        ContentType="application/json",
        # Currency must never be cached by anything between the store and a
        # reader — the pointer is tiny and the whole design leans on it being
        # fresh.
        CacheControl="no-cache",
    )


def _prune_remote_builds(prefix: str, keep: str) -> None:
    """Delete superseded ``builds/`` prefixes, best-effort.

    Keeps *keep* (the build just made live) and the newest superseded build —
    a reader that resolved the pointer just before the flip may still be
    pulling it. Everything older is unreachable by construction and only
    costs storage. Failure here is logged, never raised: the publish already
    succeeded and a leftover prefix is not worth failing the run over.
    """
    try:
        client = _client()
        base = f"{prefix}/builds/"
        paginator = client.get_paginator("list_objects_v2")
        builds: set[str] = set()
        for page in paginator.paginate(Bucket=_bucket(), Prefix=base, Delimiter="/"):
            for cp in page.get("CommonPrefixes", []):
                name = cp["Prefix"][len(base):].rstrip("/")
                if name:
                    builds.add(name)
        stale = sorted(b for b in builds if b != keep)[:-1]
        for build in stale:
            keys: list[str] = []
            for page in paginator.paginate(Bucket=_bucket(), Prefix=f"{base}{build}/"):
                keys.extend(o["Key"] for o in page.get("Contents", []))
            for i in range(0, len(keys), 1000):
                client.delete_objects(
                    Bucket=_bucket(),
                    Delete={
                        "Objects": [{"Key": k} for k in keys[i:i + 1000]],
                        "Quiet": True,
                    },
                )
    except Exception as exc:  # noqa: BLE001 - cleanup must not fail a publish
        logger.warning(
            f"pruning superseded builds under {prefix}: {type(exc).__name__}: {exc}"
        )


def probe() -> str:
    """Prove the configured store is usable. Returns a detail line, or raises.

    Goes through the S3 backend rather than a client of its own: a
    listing that succeeds proves the bucket name, the endpoint and the
    credentials all work, which is exactly what the health check needs.
    """
    if not is_remote():
        return "local (runner and web share the data volume)"

    bucket = _bucket()
    if not bucket:
        raise RuntimeError(
            "TRELLUM_STORAGE_BACKEND=s3 but TRELLUM_REPORTS_BUCKET is empty — "
            "builds would have nowhere to publish to."
        )
    try:
        _s3().list_prefixes()
    except Exception as exc:  # noqa: BLE001 - the driver raises per-operation classes
        raise RuntimeError(
            f"cannot reach s3://{bucket}: {type(exc).__name__}: {exc}. "
            f"Check the bucket name, the endpoint override, and that the "
            f"credentials this process runs with may read and write it."
        ) from exc
    return f"s3://{bucket}"


def clear_cache(studio, slug: str | None = None) -> None:
    """Drop cached output for a studio, or one report of it.

    For operators and tests. The cache is derived state — removing it costs a
    re-pull and nothing else.
    """
    with _pointer_memo_guard:
        _pointer_memo.clear()
    shutil.rmtree(_cache_root(studio, slug), ignore_errors=True)


# ── Removing built output (retention) ──────────────────────────────────────
#
# Everything above this line puts output somewhere or reads it back. These two
# take it away again, for ``apps.core.retention``'s built-data window.


def _tree_bytes(path: Path) -> int:
    """Bytes a directory tree occupies. Unreadable entries count as nothing."""
    total = 0
    try:
        entries = list(path.rglob("*"))
    except OSError:
        return 0
    for entry in entries:
        try:
            if entry.is_file():
                total += entry.stat().st_size
        except OSError:
            continue
    return total


def _sweep_remote_prefix(prefix: str, *, delete: bool) -> int:
    """Bytes under one bucket prefix, removing them when asked.

    The trailing slash is load-bearing: without it ``sales`` would also match
    ``sales-eu``, and a retention sweep that took a neighbouring report with it
    is not a bug anyone gets to find out about twice. Deletes page by page so
    the key list stays bounded however large the prefix is.
    """
    client = _client()
    paginator = client.get_paginator("list_objects_v2")
    total = 0
    pending: list[str] = []

    def flush(force: bool) -> None:
        while pending and (force or len(pending) >= 1000):
            chunk, pending[:] = pending[:1000], pending[1000:]
            client.delete_objects(
                Bucket=_bucket(),
                Delete={"Objects": [{"Key": k} for k in chunk], "Quiet": True},
            )

    for page in paginator.paginate(Bucket=_bucket(), Prefix=f"{prefix}/"):
        for obj in page.get("Contents", []):
            total += obj.get("Size", 0)
            if delete:
                pending.append(obj["Key"])
        if delete:
            flush(force=False)
    if delete:
        flush(force=True)
        # The pointer for this prefix is one of the keys just deleted; a
        # memoised "build b7 is current" would outlive the build itself.
        _forget_pointer(prefix)
    return total


def output_bytes(studio, slug: str | None = None) -> int:
    """Bytes one report's built output occupies, everywhere it lives.

    ``slug=None`` measures the whole studio. The measuring half of
    :func:`delete_output` — same places, nothing removed — so a dry run and
    the health check can report what a sweep would free.
    """
    return _sweep_output(studio, slug, delete=False)


def delete_output(studio, slug: str | None = None) -> int:
    """Remove one report's built output everywhere it lives. Returns bytes freed.

    ``slug=None`` removes the studio's output wholesale, which is what a
    retention sweep addresses when the ``Studio`` row itself is gone and the
    slugs it used to hold are no longer knowable from the database.

    All three places, every time: the studio's output directory, the bucket
    prefix, and this node's read cache. Not "whichever the current backend
    uses" — an instance that has ever run the other one still has the other
    copy, and removing a report from one place while another keeps serving it
    is the precise failure this function exists to prevent.

    The ``Report`` row, its history, favourites and share links all survive.
    Only bytes go.
    """
    return _sweep_output(studio, slug, delete=True)


def _sweep_output(studio, slug: str | None, *, delete: bool) -> int:
    local = studio.output_dir if slug is None else studio.output_dir / slug
    total = _tree_bytes(local) + _tree_bytes(_cache_root(studio, slug))
    # Store first, disk second, and the order is the safe one: if the store is
    # unreachable this raises with the local copy still intact, and a retry
    # finishes the job. Deleting locally first would leave the bucket serving a
    # report the sweep believes it removed.
    if is_remote():
        total += _sweep_remote_prefix(_remote_prefix(studio, slug), delete=delete)
    if delete:
        shutil.rmtree(local, ignore_errors=True)
        clear_cache(studio, slug)
    return total
