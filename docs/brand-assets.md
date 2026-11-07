# Brand assets

The lattice mark and lowercase **trellum** wordmark use the existing neutral
palette. Downloadable assets live in `static/brand/`; the website serves identical
copies from `website/static/brand/`.

- `mark-light.svg` / `wordmark-light.svg`: light surfaces.
- `mark-dark.svg` / `wordmark-dark.svg`: dark surfaces.
- `mark-mono.svg` / `wordmark-mono.svg`: monochrome print or inline SVG. They use
  `currentColor` (black when opened standalone).
- `favicon.svg`, `favicon-16.png`, `favicon-32.png`, `favicon-48.png`, and
  `favicon.ico`: the mark on a light tile for browser chrome. The ICO contains
  16, 32 and 48 pixel images.

Keep aspect ratios and clear space. Wordmark letters are paths derived from the
bundled Inter Bold font, so consumers need no installed font. Inter's existing
SIL Open Font License is in `trellum/static/vendor/licenses/inter-4.001-OFL.txt`.

The portal's `_brand_mark.html` is the shared theme-aware inline mark used by
`_shell.html` (including console-mounted reports) and `_brand_lockup.html` on
login/setup cards. It keeps the shell's existing colors, sizing and collapsed
sidebar behavior. Standalone reports retain their authored headers; no product
branding is injected into customer report content.

To regenerate the static assets, install `fonttools`, `cairosvg` and `Pillow` in
a disposable development environment and run `python scripts/build-brand-assets.py`
from the repository. These are build tools, not new product runtime dependencies.
Review both themes and the 16 pixel favicon after regeneration. The website's
existing social preview remains a report image, independent of this asset pack.
