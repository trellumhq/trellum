"""File-based and HTTP-based data sources.

No containers, no engine selection: these run unconditionally whenever
this file is collected. csv/xlsx/parquet round-trip through
``trellum.data.query.read_source`` against files written into
``tmp_path``; ``read_api`` is exercised against a real HTTP server -- a
stdlib ``http.server`` started on an ephemeral port in a daemon thread --
rather than mocked, so this proves the actual request/response wiring
(query params, JSON body, ``json_path`` extraction) end to end.
"""

from __future__ import annotations

import json
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

import pandas as pd
import pytest


def test_read_source_csv(tmp_path):
    from trellum.data.query import read_source

    path = tmp_path / "data.csv"
    pd.DataFrame({
        "id": [1, 2, 3],
        "label": ["a", "héllo — ünïcode", "O'Brien"],
    }).to_csv(path, index=False)

    df = read_source("csv", str(path))
    assert list(df.columns) == ["id", "label"]
    assert len(df) == 3
    assert df["label"].tolist() == ["a", "héllo — ünïcode", "O'Brien"]


def test_read_source_xlsx(tmp_path):
    from trellum.data.query import read_source

    path = tmp_path / "data.xlsx"
    pd.DataFrame({
        "id": [1, 2],
        "label": ["héllo — ünïcode", "O'Brien"],
        "value": [10.5, 20.25],
    }).to_excel(path, index=False)

    df = read_source("excel", str(path))
    assert list(df.columns) == ["id", "label", "value"]
    assert len(df) == 2
    assert df["value"].tolist() == [10.5, 20.25]
    assert df["label"].tolist() == ["héllo — ünïcode", "O'Brien"]


def test_read_source_parquet(tmp_path):
    from trellum.data.query import read_source

    path = tmp_path / "data.parquet"
    expected = pd.DataFrame({
        "id": [1, 2, 3],
        "label": ["héllo — ünïcode", "O'Brien", "plain"],
        "amount": [1.1, 2.2, 3.3],
    })
    expected.to_parquet(path, index=False)

    df = read_source("parquet", str(path))
    pd.testing.assert_frame_equal(df, expected)


# ── read_api against a real HTTP server ─────────────────────────────────


class _EchoHandler(BaseHTTPRequestHandler):
    """Minimal REST stub: GET echoes query params, POST echoes the JSON body."""

    def _send_json(self, payload) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 -- BaseHTTPRequestHandler naming convention
        parsed = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        self._send_json({
            "meta": {"ok": True},
            "data": {"results": [
                {"echo": query, "row": 1},
                {"echo": query, "row": 2},
            ]},
        })

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        body = json.loads(self.rfile.read(length)) if length else {}
        self._send_json([{"received": body}])

    def log_message(self, format, *args):  # noqa: A002 -- fixed base-class signature
        pass  # keep pytest output quiet -- this isn't a request-logging test


@pytest.fixture(scope="module")
def http_server():
    server = HTTPServer(("127.0.0.1", 0), _EchoHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_read_api_get_with_params(http_server):
    from trellum.data.query import read_api

    host, port = http_server.server_address
    df = read_api(
        f"http://{host}:{port}/items",
        params={"category": "widgets"},
        json_path="data.results",
    )

    assert len(df) == 2
    assert df["row"].tolist() == [1, 2]
    assert df["echo_category"].iloc[0] == "widgets"


def test_read_api_post_json_body(http_server):
    from trellum.data.query import read_api

    host, port = http_server.server_address
    df = read_api(
        f"http://{host}:{port}/submit",
        method="POST",
        json_body={"sql": "SELECT 1", "note": "O'Brien"},
    )

    assert len(df) == 1
    assert df["received_sql"].iloc[0] == "SELECT 1"
    assert df["received_note"].iloc[0] == "O'Brien"


# ── s3:// file source against MinIO (optional "s3" profile) ────────────
#
# Only collected when "s3" is passed to --engines (see conftest.py's
# _CONDITIONAL_TESTS / pytest_collection_modifyitems -- when it isn't, this
# test is deselected, never skipped). Proves read_source("csv", "s3://...")
# with zero framework changes: read_source forwards **kwargs (including
# storage_options) straight to pandas, which hands them to s3fs.


def _minio_storage_options() -> dict:
    return {
        "key": os.environ.get("ITEST_MINIO_USER", "itest"),
        "secret": os.environ.get("ITEST_MINIO_PASS", "itest_pw123"),
        "client_kwargs": {
            "endpoint_url": os.environ.get(
                "ITEST_MINIO_ENDPOINT", "http://localhost:59000"
            ),
        },
    }


def test_read_source_csv_s3():
    import boto3

    from trellum.data.query import read_source

    opts = _minio_storage_options()
    bucket = "itest-bucket"
    key = "data.csv"
    csv_body = (
        "id,label\n"
        "1,a\n"
        "2,héllo — ünïcode\n"
        "3,O'Brien\n"
    ).encode("utf-8")

    client = boto3.client(
        "s3",
        endpoint_url=opts["client_kwargs"]["endpoint_url"],
        aws_access_key_id=opts["key"],
        aws_secret_access_key=opts["secret"],
    )
    try:
        client.create_bucket(Bucket=bucket)
    except client.exceptions.BucketAlreadyOwnedByYou:
        pass
    client.put_object(Bucket=bucket, Key=key, Body=csv_body)

    df = read_source(
        "csv", f"s3://{bucket}/{key}", storage_options=opts,
    )
    assert list(df.columns) == ["id", "label"]
    assert len(df) == 3
    assert df["label"].tolist() == ["a", "héllo — ünïcode", "O'Brien"]
