# Updating Vendor Assets

The assets in this directory are served as files. Generated reports reference
them under `/_vendor/`; the local report server serves that route from the
installed framework package. `--portable` builds copy the assets to
`output/_vendor/` and use relative URLs. The assets are not inlined, and there
is no CDN fallback: a missing required file stops report generation, while a
host must serve vendor files at the URLs referenced by each report.

## Current inventory

`MANIFEST.json` records asset versions, filenames, licences, and upstream
sources. `LICENSES.md` contains the full bundled-asset notices, and
`../../THIRD-PARTY.md` records the repository's third-party inventory. Check
all three before changing a bundled asset.

## Updating an asset

1. Choose and verify the exact upstream release. Do not substitute an
   unverified “latest” URL; the committed file and manifest must name the same
   release.
2. Download the required distribution file or files from that release. For
   example, replace `VERSION` with the release you verified:

   ```bash
   curl -fsSL -o trellum/static/vendor/chart.umd.min.js \
     "https://cdn.jsdelivr.net/npm/chart.js@VERSION/dist/chart.umd.min.js"
   ```

3. Update the asset's version, filename, and source in `MANIFEST.json`. Check
   the upstream licence and copyright notice; update the matching full notice
   in `LICENSES.md` and the entry in `../../THIRD-PARTY.md` as needed.
4. Check `trellum/rendering/cdn.py` to ensure the logical key still maps to the
   local filename. Existing keys always use their local files; there is no
   fallback URL to update.
5. Build representative demo reports from `trellum/demo/`:

   ```bash
   # From trellum/demo/
   python -m trellum.run reports/store-health --no-serve
   python -m trellum.run reports/player-overview --no-serve
   ```

   `player-overview` exercises the bundled Venn and map libraries as well as
   the standard charts. Run the relevant framework tests too:

   ```bash
   # Still in trellum/demo/
   python -m pytest ../testing/test_components.py -v
   python -m pytest ../testing/test_js_runtime.py -v
   ```

## Adding an asset

1. Add the pinned upstream distribution file or files here and record its
   version, filenames, licence, and source in `MANIFEST.json`.
2. Add the complete upstream notice to `LICENSES.md` and the package to
   `../../THIRD-PARTY.md`.
3. Add a logical-key-to-filename entry to `_LIBRARIES` in
   `trellum/rendering/cdn.py`.
4. Add the key to `_ALWAYS_INCLUDED` only when every report needs it. Otherwise
   request it from the relevant component's `cdn_deps()` or a report's
   `extra_cdn` setting.
5. Run the build and tests above.

## Licences

Do not assume vendor assets share one licence. The current inventory includes
MIT and ISC JavaScript packages, Inter fonts under the SIL Open Font License
1.1, and Natural Earth data in the world-atlas file under a public-domain
declaration. Use `MANIFEST.json` and `LICENSES.md` for each asset's exact
licence and notice; preserve those notices when updating or redistributing it.
