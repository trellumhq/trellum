# Updating Vendor Libraries

All JavaScript and CSS libraries used by the report framework are bundled in this directory. They are embedded inline into every generated report HTML, so reports work offline with no CDN dependency.

## Current Versions

See `MANIFEST.json` for the complete list with versions, filenames, licenses, and source URLs.

## How to Update a Library

1. **Check the current version** in `MANIFEST.json`
2. **Find the latest version** on [npmjs.com](https://www.npmjs.com/) or [jsdelivr.com](https://www.jsdelivr.com/)
3. **Download the new version:**
   ```bash
   # Example: updating Chart.js from 4.5.1 to 4.6.0
   curl -sL -o trellum/static/vendor/chart.umd.min.js \
     "https://cdn.jsdelivr.net/npm/chart.js@4.6.0/dist/chart.umd.min.js"
   ```
4. **Update `MANIFEST.json`** — change the version number and source URL
5. **Update `trellum/rendering/cdn.py`** — change the CDN fallback URL (used only if the local file is missing)
6. **Test** — run a report and verify charts render correctly:
   ```bash
   python -m trellum.run reports/dau-mau-trends
   python -m trellum.run reports/revenue-by-platform
   ```
7. **Run the test suite** for visual regressions:
   ```bash
   python3 -m pytest trellum/testing/test_components.py -v
   python3 -m pytest trellum/testing/test_js_runtime.py -v
   ```

## Major Version Upgrades

When upgrading to a new **major** version (e.g., Chart.js 4 → 5):

1. Read the changelog for breaking changes
2. Check that framework components still render correctly
3. Run the full test suite including browser tests:
   ```bash
   python -m trellum.run --all --test
   python3 -m pytest trellum/testing/test_interactions.py -v
   ```

## Adding a New Library

1. Download the minified JS/CSS to this directory
2. Add an entry to `MANIFEST.json`
3. Add an entry to `_LIBRARIES` in `trellum/rendering/cdn.py`
4. If it should be included in every report, add the key to `_ALWAYS_INCLUDED`
5. If it's only needed by specific components, have the component declare it via `cdn_deps()`

## How Bundling Works

`trellum/rendering/cdn.py` reads files from this directory and embeds them as inline `<script>` or `<style>` tags in the generated HTML. If a local file is missing, it falls back to the CDN URL. This means:

- Reports are **self-contained** — no external network requests needed
- Reports work **offline** and in air-gapped environments
- Version is **locked** — no surprise updates from CDN
- Google Fonts is the only external dependency (loaded via CDN link tag)

## Licenses

All bundled libraries are MIT licensed. The MIT license requires including the copyright notice, which is preserved in the minified files.
