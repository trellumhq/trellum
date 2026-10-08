"""The portal's S3 client and publish/pull backend.

This used to live in the framework. It moved here when the framework cut its
ties to any cloud: a library for writing reports has no business carrying an
AWS SDK, and the portal is the thing that actually owns distribution — the
tenant-scoped key layout, the read cache, the grant cookie and the edge
worker are all already here. The client was the last piece still on the wrong
side of that line.

What the framework wrote to a bucket, the portal now writes. The object layout
is unchanged, so a bucket populated by the old code is read correctly by this
one; that mattered more than a tidier design.

Configuration comes from Django settings, not the environment. The framework
read ``BI_STORAGE_*`` at call time because it had no settings layer to read
from; the portal does, and an operator who sets ``TRELLUM_STORAGE_ENDPOINT_URL``
should not have to know that some other prefix is what actually gets read.
"""
from __future__ import annotations

import gzip
import logging
import os
import tempfile

from django.conf import settings

logger = logging.getLogger("trellum.storage")

_COMPRESSIBLE = {".json", ".html", ".js", ".css"}

_CONTENT_TYPES = {
    ".html": "text/html",
    ".json": "application/json",
    ".css": "text/css",
    ".js": "application/javascript",
}

#: The .json extension would otherwise label these application/json, which
#: breaks PWA install on some browsers.
_FILENAME_CONTENT_TYPES = {
    "manifest.json": "application/manifest+json",
}

#: Cache-Control per file, set as object metadata so browsers and the CDN keep
#: each thing for the right length of time. data.json is fetched with a
#: ?v=<last_run> query string, so every version is a distinct URL and can be
#: treated as immutable.
_CACHE_CONTROL = {
    "data.json": "public, max-age=86400, immutable",
    "index.html": "public, max-age=60, must-revalidate",
    "_meta.json": "public, max-age=30",
    "_validation.json": "public, max-age=60",
    # Referenced by index.html, read once on add-to-home-screen, and only
    # changes when the report name does.
    "manifest.json": "public, max-age=3600",
}


def _cache_control_for(filename: str) -> str | None:
    if filename in _CACHE_CONTROL:
        return _CACHE_CONTROL[filename]
    # Scope data files (data_<scope>.json) version and cache like data.json.
    if filename.startswith("data_") and filename.endswith(".json"):
        return _CACHE_CONTROL["data.json"]
    return None


def s3_client(read_timeout: int = 15, region: str | None = None):
    """An S3 client with conservative timeouts.

    The timeouts are the point: a misconfigured VPC, a missing endpoint or an
    IAM problem otherwise hangs the call forever, and some of these run on the
    health-check path where hanging is worse than failing.

    Set ``TRELLUM_STORAGE_ENDPOINT_URL`` to talk to any S3-compatible store
    (MinIO, Ceph, Wasabi); credentials then come from
    ``TRELLUM_STORAGE_ACCESS_KEY`` / ``TRELLUM_STORAGE_SECRET_KEY``. With no
    endpoint override the ordinary boto3 credential chain applies, so an AWS
    deployment on an instance role configures nothing.
    """
    import boto3
    from botocore.config import Config

    endpoint = (getattr(settings, "TRELLUM_STORAGE_ENDPOINT_URL", "") or "").strip()
    # MinIO and most non-AWS stores serve bucket-in-path rather than
    # bucket-as-subdomain, which would need wildcard DNS they do not have.
    addressing = "path" if endpoint else "auto"

    client_kwargs: dict = {
        "config": Config(
            connect_timeout=5,
            read_timeout=read_timeout,
            retries={"max_attempts": 2, "mode": "standard"},
            s3={"addressing_style": addressing},
        ),
    }

    region = region or (getattr(settings, "TRELLUM_STORAGE_REGION", "") or "").strip() or None
    if region:
        client_kwargs["region_name"] = region

    if endpoint:
        client_kwargs["endpoint_url"] = endpoint
        access_key = (getattr(settings, "TRELLUM_STORAGE_ACCESS_KEY", "") or "").strip()
        secret_key = (getattr(settings, "TRELLUM_STORAGE_SECRET_KEY", "") or "").strip()
        if access_key and secret_key:
            client_kwargs["aws_access_key_id"] = access_key
            client_kwargs["aws_secret_access_key"] = secret_key

    return boto3.client("s3", **client_kwargs)


class S3Backend:
    """Publishes and retrieves a report directory under a key prefix."""

    def __init__(self, bucket: str | None = None, region: str | None = None):
        self.bucket = bucket or getattr(settings, "TRELLUM_REPORTS_BUCKET", "") or ""
        self.region = region

    def _client(self):
        return s3_client(read_timeout=15, region=self.region)

    def publish(self, output_dir: str, prefix: str) -> None:
        """Upload browser assets in *output_dir* to ``s3://{bucket}/{prefix}/``.

        Compressible files go up gzipped with ``ContentEncoding: gzip`` so the
        CDN can serve them pre-compressed.
        """
        from botocore.exceptions import ClientError, NoCredentialsError
        from trellum.artifacts import is_private_artifact

        s3 = self._client()

        files = [
            f
            for f in os.listdir(output_dir)
            if os.path.isfile(os.path.join(output_dir, f))
            and not f.endswith(".gz") and not is_private_artifact(f)
        ]
        logger.info("publishing %d file(s) to s3://%s/%s/", len(files), self.bucket, prefix)

        def _upload(local_path: str, s3_key: str, extra_args: dict) -> None:
            # Every failure here is a configuration mistake someone has to fix,
            # so each one says which mistake it looks like. A bare ClientError
            # sends the reader to the AWS docs instead of to their own settings.
            try:
                s3.upload_file(local_path, self.bucket, s3_key, ExtraArgs=extra_args)
            except NoCredentialsError as exc:
                raise RuntimeError(
                    f"S3 upload failed: no credentials found while uploading "
                    f"s3://{self.bucket}/{s3_key}. Check the instance role, or set "
                    f"TRELLUM_STORAGE_ACCESS_KEY / TRELLUM_STORAGE_SECRET_KEY."
                ) from exc
            except ClientError as exc:
                err = exc.response.get("Error", {})
                code = err.get("Code", "Unknown")
                msg = err.get("Message", str(exc))
                hint = ""
                if code == "NoSuchBucket":
                    hint = (
                        f" -- bucket '{self.bucket}' does not exist (check "
                        f"TRELLUM_REPORTS_BUCKET and region={self.region or 'default'})"
                    )
                elif code in ("AccessDenied", "403", "Forbidden"):
                    hint = (
                        f" -- the credentials lack s3:PutObject on "
                        f"s3://{self.bucket}/{s3_key}"
                    )
                elif code in ("InvalidAccessKeyId", "SignatureDoesNotMatch"):
                    hint = " -- the credentials are invalid or misconfigured"
                raise RuntimeError(
                    f"S3 upload failed for s3://{self.bucket}/{s3_key}: "
                    f"{code} ({msg}){hint}"
                ) from exc

        uploaded = 0
        for filename in files:
            filepath = os.path.join(output_dir, filename)
            ext = os.path.splitext(filename)[1]
            content_type = _FILENAME_CONTENT_TYPES.get(
                filename, _CONTENT_TYPES.get(ext, "application/octet-stream"),
            )
            s3_key = f"{prefix}/{filename}"

            base_args: dict = {"ContentType": content_type}
            cache_control = _cache_control_for(filename)
            if cache_control:
                base_args["CacheControl"] = cache_control

            if ext in _COMPRESSIBLE:
                gz_args = dict(base_args, ContentEncoding="gzip")
                gz_path = filepath + ".gz"
                if os.path.isfile(gz_path):
                    _upload(gz_path, s3_key, gz_args)
                else:
                    with open(filepath, "rb") as f:
                        raw = f.read()
                    compressed = gzip.compress(raw, compresslevel=6)
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".gz") as tmp:
                        tmp.write(compressed)
                        tmp_path = tmp.name
                    try:
                        _upload(tmp_path, s3_key, gz_args)
                    finally:
                        os.unlink(tmp_path)
            else:
                _upload(filepath, s3_key, base_args)
            uploaded += 1

        logger.info("uploaded %d file(s) to s3://%s/%s/", uploaded, self.bucket, prefix)

    def list_prefixes(self) -> list[str]:
        """Top-level key prefixes in the bucket.

        Used as a reachability probe by the storage health check rather than
        for its result: in the portal's layout a top-level prefix is an
        organization slug, not a report.
        """
        s3 = self._client()

        prefixes: list[str] = []
        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Delimiter="/"):
            for prefix_obj in page.get("CommonPrefixes", []):
                name = prefix_obj["Prefix"].rstrip("/")
                if name and not name.startswith("_"):
                    prefixes.append(name)
        return prefixes

    def pull(self, prefix: str, local_dir: str) -> None:
        """Download everything under ``{prefix}/`` into *local_dir*.

        get_object rather than download_file, so ``Content-Encoding: gzip`` can
        be detected and undone on write. publish() uploads text files
        pre-gzipped for the CDN; writing those bytes to disk as-is would leave
        a local file full of gzip that the next ``json.load`` or template read
        fails on, with an error that points nowhere near here.

        The skip-if-unchanged shortcut only applies to objects that are not
        encoded, because a compressed object's size does not match the size of
        the decompressed file on disk.
        """
        s3 = self._client()
        key_prefix = f"{prefix}/"

        os.makedirs(local_dir, exist_ok=True)

        paginator = s3.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=key_prefix):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                filename = key[len(key_prefix):]
                if not filename:
                    continue

                local_path = os.path.join(local_dir, filename)
                local_subdir = os.path.dirname(local_path)
                if local_subdir:
                    os.makedirs(local_subdir, exist_ok=True)

                response = s3.get_object(Bucket=self.bucket, Key=key)
                encoding = response.get("ContentEncoding", "")
                if not encoding and os.path.isfile(local_path):
                    try:
                        if os.path.getsize(local_path) == obj["Size"]:
                            response["Body"].close()
                            continue
                    except OSError:
                        pass

                body = response["Body"].read()
                if encoding == "gzip":
                    body = gzip.decompress(body)
                with open(local_path, "wb") as f:
                    f.write(body)
