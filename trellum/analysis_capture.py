"""Portable PNG evidence imports and their provenance contract."""

from __future__ import annotations

import base64
import binascii
import io
import json
import re
import warnings
from datetime import datetime
from pathlib import Path
from urllib.parse import unquote, urlsplit

from PIL import Image

from trellum.report import _validate_output_filename

MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_CAPTURE_BYTES = ((MAX_IMAGE_BYTES + 2) // 3) * 4 + 1024 * 1024
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
SOURCE_FIELDS = (
    "report_slug", "report_name", "url", "component_id", "component_title",
    "captured_at", "source_built_at", "filters",
)


def read_bounded(path: Path, limit: int) -> bytes:
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError(f"{path.name} exceeds the size limit")
    return raw


def safe_name(value: str) -> str:
    """One portable slug/asset stem, including Windows device protections."""
    _validate_output_filename(value)
    if not re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,99}", value):
        raise ValueError("Use a lowercase name with letters, digits, hyphens or underscores")
    return value


def safe_source_url(value: str) -> str:
    """Source links cannot carry credentials, filters or share tokens."""
    parts = urlsplit(value)
    decoded_path = unquote(parts.path).lower()
    if (parts.scheme not in ("http", "https") or not parts.hostname
            or parts.username is not None or parts.password is not None
            or parts.query or "?" in value or parts.fragment or "#" in value
            or "\\" in value or any(ord(c) <= 32 or ord(c) == 127 for c in value)
            or any(segment in ("share", "shared", "token")
                   for segment in decoded_path.split("/"))):
        raise ValueError("Source URL must be http(s), without credentials, query or share token")
    # Accessing port also validates a malformed port.
    parts.port
    return value


def validate_source(source: object) -> dict:
    if not isinstance(source, dict) or set(source) != set(SOURCE_FIELDS):
        raise ValueError("Capture source must contain the documented provenance fields only")
    for key in SOURCE_FIELDS[:-1]:
        if source[key] is not None and not isinstance(source[key], str):
            raise ValueError(f"Source {key} must be a string or null")
    captured_at = source["captured_at"]
    if not captured_at or "T" not in captured_at:
        raise ValueError("Source captured_at must be an ISO timestamp")
    for key in ("captured_at", "source_built_at"):
        if source[key]:
            try:
                parsed = datetime.fromisoformat(source[key].replace("Z", "+00:00"))
            except ValueError as exc:
                raise ValueError(f"Source {key} must be an ISO timestamp") from exc
            if parsed.tzinfo is None:
                raise ValueError(f"Source {key} must include a timezone")
    if not isinstance(source["filters"], dict):
        raise ValueError("Source filters must be a JSON object")
    # Strict JSON also rejects NaN and Infinity accepted by json.loads.
    json.dumps(source, allow_nan=False)
    if source["url"]:
        safe_source_url(source["url"])
    return source


def validate_png(raw: bytes) -> None:
    if len(raw) > MAX_IMAGE_BYTES or not raw.startswith(PNG_SIGNATURE):
        raise ValueError("Evidence must be a PNG no larger than 20 MiB")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(raw)) as image:
                if image.format != "PNG":
                    raise ValueError("Evidence must be PNG")
                image.verify()
            with Image.open(io.BytesIO(raw)) as image:
                image.load()
    except Exception as exc:
        raise ValueError("Evidence PNG cannot be decoded safely") from exc


def confined_file(root: Path, relative: str) -> Path:
    """Resolve an authored asset without traversal, device names or symlink escape."""
    if (not relative or relative.startswith(("/", "\\"))
            or "\\" in relative or any(c in relative for c in (":", "?", "#"))):
        raise ValueError(f"Unsafe article asset path: {relative!r}")
    path = Path(relative)
    for part in path.parts:
        _validate_output_filename(part)
    root = root.resolve()
    resolved = (root / path).resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        raise ValueError(f"Article asset is missing or outside its directory: {relative!r}")
    return resolved


def import_capture(report_dir: str, capture_file: str, name: str | None = None) -> str:
    from trellum.report import BaseReport

    root = Path(report_dir).resolve()
    if BaseReport.load_config(str(root)).get("kind", "report") != "analysis":
        raise ValueError("Import evidence into a kind: analysis directory")
    capture_path = Path(capture_file)
    raw_capture = read_bounded(capture_path, MAX_CAPTURE_BYTES)
    capture = json.loads(raw_capture)
    if (not isinstance(capture, dict)
            or capture.get("format") != "trellum-analysis-capture"
            or type(capture.get("version")) is not int or capture["version"] != 1):
        raise ValueError("Unsupported analysis capture format/version")
    source = validate_source(capture.get("source"))
    image = capture.get("image")
    if (not isinstance(image, dict) or image.get("mime_type") != "image/png"
            or not isinstance(image.get("data_base64"), str)):
        raise ValueError("Capture image must contain PNG base64 data")
    try:
        raw = base64.b64decode(image["data_base64"], validate=True)
    except (ValueError, binascii.Error) as exc:
        raise ValueError("Capture image is not valid base64") from exc
    validate_png(raw)
    default_name = (capture_path.name.removesuffix(".trellum-capture.json")
                    if capture_path.name.endswith(".trellum-capture.json") else capture_path.stem)
    stem = safe_name(name if name is not None else default_name)
    evidence = root / "evidence"
    if evidence.is_symlink() or not evidence.resolve().is_relative_to(root):
        raise ValueError("Evidence directory must stay inside the analysis directory")
    evidence.mkdir(exist_ok=True)
    png_path, json_path = evidence / f"{stem}.png", evidence / f"{stem}.json"
    if png_path.exists() or png_path.is_symlink() or json_path.exists() or json_path.is_symlink():
        raise FileExistsError(f"Evidence {stem!r} already exists")
    # Exclusive creation handles races as well as accidental overwrite.
    with png_path.open("xb") as stream:
        stream.write(raw)
    try:
        with json_path.open("x", encoding="utf-8") as stream:
            json.dump(source, stream, ensure_ascii=False, indent=2, allow_nan=False)
    except Exception:
        png_path.unlink()
        raise
    return f"![Evidence](evidence/{stem}.png)"
