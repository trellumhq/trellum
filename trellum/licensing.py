"""Licence and exact-source information carried by built reports."""

from __future__ import annotations

import html
import os
from pathlib import Path
from urllib.parse import urlsplit

from trellum import __version__

LICENSE_ID = "AGPL-3.0-only"
OFFICIAL_REPOSITORY = "https://github.com/trellumhq/trellum"
OUTPUT_LICENSE = "TRELLUM-LICENSE.txt"


def source_url(version: str | None = None, override: str | None = None) -> str:
    """Return the exact source URL, accepting a safe fork/deployment override."""
    candidate = (override if override is not None else os.getenv("TRELLUM_SOURCE_URL", "")).strip()
    if candidate:
        if any(ord(char) < 32 or ord(char) == 127 for char in candidate):
            raise ValueError("TRELLUM_SOURCE_URL must be an HTTP(S) URL without credentials")
        parsed = urlsplit(candidate)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise ValueError("TRELLUM_SOURCE_URL must be an HTTP(S) URL without credentials")
        return candidate.rstrip("/")

    release = (version or __version__).removeprefix("v")
    return f"{OFFICIAL_REPOSITORY}/tree/v{release}"


def source_notice_html(version: str | None = None, override: str | None = None) -> str:
    url = html.escape(source_url(version, override), quote=True)
    release = html.escape((version or __version__).removeprefix("v"))
    return (
        '<footer class="trellum-source" style="margin:2rem 1rem 1rem;'
        'font-size:.75rem;text-align:center">'
        f'Trellum runtime {release} is {LICENSE_ID} · '
        f'<a href="{url}" rel="noopener">Source</a> · '
        f'<a href="{OUTPUT_LICENSE}">License</a>'
        "</footer>"
    )


def inject_source_notice(document: str) -> str:
    notice = source_notice_html()
    marker = "</body>"
    index = document.lower().rfind(marker)
    return document[:index] + notice + document[index:] if index >= 0 else document + notice


def write_output_license(output_dir: str | Path) -> Path:
    target = Path(output_dir) / OUTPUT_LICENSE
    target.write_bytes(Path(__file__).with_name("LICENSE").read_bytes())
    return target
