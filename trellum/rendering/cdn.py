"""Vendor JavaScript/CSS library management.

All libraries are bundled in ``trellum/static/vendor/``.
No CDN fallback — if a file is missing, the report will fail visibly.

See ``trellum/static/vendor/MANIFEST.json`` for versions and licenses.
See ``trellum/static/vendor/UPDATING.md`` for how to update.
"""

from pathlib import Path

_VENDOR_DIR = Path(__file__).resolve().parent.parent / "static" / "vendor"

# The route a server answers on. This never changes: it is the contract between
# generated output and every host that serves it.
_VENDOR_PREFIX = "/_vendor/"

# The prefix written INTO generated HTML, which is a different question.
#
# The default is the absolute route above, which requires the host to serve
# /_vendor/ at the domain root. That is fine for the dev server and for a host
# that mounts reports at the root, and wrong for anything published into a
# subdirectory -- the requests resolve against the domain root and 404, so the
# page renders with no charts and no error. Portable builds set this to a
# relative prefix instead (see set_vendor_url_base).
_vendor_url_base = _VENDOR_PREFIX


def set_vendor_url_base(base: str) -> None:
    """Set the prefix used for vendor URLs in generated HTML.

    ``"/_vendor/"`` (the default) emits absolute paths and needs the host to
    serve that route. ``"../_vendor/"`` emits paths relative to a report
    directory, so the output tree can be published under any path -- which is
    what portable builds use, alongside a copy of the vendor files made by
    :func:`copy_vendor_tree`.

    A trailing slash is added if missing, because every call site concatenates
    a bare filename onto this.
    """
    global _vendor_url_base
    _vendor_url_base = base if base.endswith("/") else base + "/"


def get_vendor_url_base() -> str:
    """The prefix currently used for vendor URLs in generated HTML."""
    return _vendor_url_base


def copy_vendor_tree(dest_dir) -> Path:
    """Copy the bundled vendor directory to ``dest_dir/_vendor``.

    Report output references these files but has never contained them: they are
    served at runtime from inside the installed package. That works only while
    something is running. A published, static copy of the output has no such
    server, so the files have to be on disk beside it.

    Returns the directory written.
    """
    import shutil

    dest = Path(dest_dir) / "_vendor"
    shutil.copytree(_VENDOR_DIR, dest, dirs_exist_ok=True)
    return dest
_VENDOR_CONTENT_TYPES = {
    ".js": "application/javascript",
    ".css": "text/css",
    ".ttf": "font/ttf",
    ".woff2": "font/woff2",
    ".woff": "font/woff",
    ".json": "application/json",
    ".map": "application/json",
}


def serve_vendor_request(handler) -> bool:
    """Serve a ``/_vendor/*`` request from the bundled vendor directory.

    Reports reference vendor assets by absolute path and there is no CDN
    fallback, so every server that hosts report output must handle this route
    or the page renders without charts. Shared by the runner's dev server and the
    test harness rather than reimplemented in each.

    Returns ``True`` if the request was handled (including error responses),
    ``False`` if it is not a vendor request and the caller should continue.
    """
    if not handler.path.startswith(_VENDOR_PREFIX):
        return False

    filename = handler.path[len(_VENDOR_PREFIX):].split("?")[0]
    if ".." in filename or filename.startswith("/"):
        handler.send_error(400)
        return True

    filepath = _VENDOR_DIR / filename
    if not filepath.is_file():
        handler.send_error(404)
        return True

    content = filepath.read_bytes()
    ext = filepath.suffix.lower()
    handler.send_response(200)
    handler.send_header("Content-Type",
                        _VENDOR_CONTENT_TYPES.get(ext, "application/octet-stream"))
    handler.send_header("Content-Length", str(len(content)))
    handler.send_header("Cache-Control", "public, max-age=31536000, immutable")
    handler.end_headers()
    handler.wfile.write(content)
    return True

# Maps logical key → local filename in the vendor directory.
# Every file MUST exist — no silent CDN fallback.
_LIBRARIES: dict[str, str] = {
    # ── Always included in every report ──────────────────────
    #
    # chart.js (4.5.1) — Core charting library. Renders all charts
    # (line, bar, doughnut, scatter, etc.) on <canvas> elements.
    "chartjs": "chart.umd.min.js",
    #
    # hammerjs (2.0.8) — Touch gesture recognition. Required by
    # chartjs-plugin-zoom for pinch-to-zoom on mobile devices.
    "hammerjs": "hammer.min.js",
    #
    # chartjs-plugin-zoom (2.2.0) — Adds drag-to-zoom and pan
    # to any Chart.js chart. Used on time-series line charts.
    "chartjs_zoom": "chartjs-plugin-zoom.min.js",
    #
    # chartjs-plugin-annotation (3.1.0) — Draws event markers,
    # goal lines, and reference lines on charts. Used by the
    # events/annotations system (events.yaml).
    "chartjs_annotation": "chartjs-plugin-annotation.min.js",
    #
    # chartjs-plugin-datalabels (2.2.0) — Renders value labels
    # on bars, points, and segments. Disabled globally by default;
    # enabled per-chart with plugins: { datalabels: { display: true } }.
    "chartjs_datalabels": "chartjs-plugin-datalabels.min.js",
    #
    # html2canvas (1.4.1) — Captures a DOM element as a PNG image.
    # Used by the Export button to generate report screenshots.
    "html2canvas": "html2canvas.min.js",
    #
    # jsPDF (2.5.2) — Generates PDF files in the browser.
    # Used by the Export button for PDF export.
    "jspdf": "jspdf.umd.min.js",

    # ── Included when components request them (cdn_deps) ─────
    #
    # chartjs-chart-matrix (2.0.1) — Heatmap/matrix chart type.
    # Used by the HeatmapChart component.
    "chartjs_matrix": "chartjs-chart-matrix.min.js",
    #
    # chartjs-chart-funnel (4.2.5) — Funnel chart type.
    # Used by the FunnelChart component.
    "chartjs_funnel": "chartjs-chart-funnel.umd.min.js",
    #
    # chartjs-chart-treemap (2.3.1) — Treemap chart type.
    # Used by the TreemapChart component.
    "chartjs_treemap": "chartjs-chart-treemap.min.js",
    #
    # nouislider (15.8.1) — Range slider widget.
    # Used by the SliderFilter in FilterBar (numeric range/single filters;
    # DateRangeFilter uses native <input type="date"> and doesn't need it).
    "nouislider_js": "nouislider.min.js",
    "nouislider_css": "nouislider.min.css",
    #
    # slim-select (2.9.2) — Searchable dropdown/multiselect.
    # Used by FilterBar for categorical filters.
    "slim_select_js": "slimselect.min.js",
    "slim_select_css": "slimselect.css",

    # ── Included per-report via extra_cdn in report.yaml ─────
    #
    # chartjs-chart-venn (4.3.7) — Venn/Euler diagram chart type.
    # Worked example: player-overview, the behaviour-overlap section.
    "chartjs_venn": "chartjs-chart-venn.umd.min.js",
    #
    # chartjs-chart-sankey (0.12.1) — Sankey flow diagram chart type.
    # Worked example: conversion, the per-title flow section.
    "chartjs_sankey": "chartjs-chart-sankey.min.js",
    #
    # chartjs-chart-geo (4.3.3) — Choropleth/bubble map chart type.
    # Worked example: player-overview, the revenue map section.
    "chartjs_geo": "chartjs-chart-geo.umd.min.js",
    #
    # topojson-client (3.1.0) — Converts TopoJSON to GeoJSON for maps.
    # Required companion for chartjs-chart-geo.
    "topojson_client": "topojson-client.min.js",
    #
    # Plotly.js (2.27.0) — Full-featured scientific charting library.
    # Kept deliberately for the things Chart.js cannot do at all: 3D scatter,
    # surface plots, parallel coordinates. No demo report uses it yet, so there
    # is no worked example to copy — expect to read Plotly's own docs.
    # NOTE: 3.4MB, larger than every other vendor file combined. It only loads
    # for reports that name it in extra_cdn, so the cost is confined to those.
    "plotly_js": "plotly.min.js",

    # ── Font ─────────────────────────────────────────────────
    #
    # Inter font (v20, SIL Open Font License) — Primary UI font.
    # Font files in vendor/fonts/*.ttf.
    "google_fonts_inter": "inter.css",
}

_ALWAYS_INCLUDED = {
    "google_fonts_inter",
    "chartjs",
    "hammerjs",
    "chartjs_zoom",
    "chartjs_annotation",
    "chartjs_datalabels",
    "html2canvas",
    "jspdf",
}


def register_cdn(name: str, url: str) -> None:
    """Register a library key for inclusion.

    If the key already exists in _LIBRARIES (has a local vendor file),
    this is a no-op — the local file is always used. The URL is ignored.

    If the key is new (not in _LIBRARIES), raises an error. All libraries
    must be bundled in the vendor directory.
    """
    if name in _LIBRARIES:
        return  # Already bundled — ignore the URL
    raise ValueError(
        f"Library '{name}' is not bundled in trellum/static/vendor/. "
        f"Download it and add to _LIBRARIES in cdn.py. URL was: {url}"
    )


def get_cdn_url(name: str) -> str:
    """Get the vendor file path for a library key (for tests that need URLs)."""
    if name in _LIBRARIES:
        return f"{_vendor_url_base}{_LIBRARIES[name]}"
    raise KeyError(f"Unknown library key: {name}")


def build_cdn_tags(keys: set[str]) -> str:
    """Build HTML tags for requested libraries.

    References local vendor files via the configured vendor URL base --
    ``/_vendor/filename`` by default, or a relative prefix in portable builds.
    Raises FileNotFoundError if a required vendor file is missing.
    """
    merged = keys | _ALWAYS_INCLUDED
    parts: list[str] = []

    for key in _LIBRARIES:
        if key not in merged:
            continue
        filename = _LIBRARIES[key]
        filepath = _VENDOR_DIR / filename

        if not filepath.is_file():
            raise FileNotFoundError(
                f"Vendor file missing: {filepath}\n"
                f"Library '{key}' requires '{filename}' in trellum/static/vendor/.\n"
                f"See trellum/static/vendor/UPDATING.md for how to download it."
            )

        url = f"{_vendor_url_base}{filename}"
        if filename.endswith(".css"):
            parts.append(f'    <link href="{url}" rel="stylesheet">')
        else:
            parts.append(f'    <script src="{url}"></script>')

    return "\n".join(parts)
