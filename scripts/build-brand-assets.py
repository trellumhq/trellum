"""Rebuild the downloadable lattice assets (fonttools, cairosvg, Pillow required)."""
from io import BytesIO
from pathlib import Path
import shutil

import cairosvg
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.ttLib import TTFont
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'static/brand'
DEST.mkdir(parents=True, exist_ok=True)
font = TTFont(ROOT / 'website/static/fonts/inter-700.ttf')
glyphs = font.getGlyphSet()
cmap = font.getBestCmap()
scale = 28 / font['head'].unitsPerEm
paths = []
x = 0
for letter in 'trellum':
    glyph = glyphs[cmap[ord(letter)]]
    pen = SVGPathPen(glyphs)
    glyph.draw(pen)
    paths.append(f'<path transform="translate({x} 0)" d="{pen.getCommands()}"/>')
    x += glyph.width
width = round(48 + x * scale + 2, 3)


def svg(body, width=40):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} 40" width="{width}" height="40">{body}</svg>\n'


def mark(grid, teal, violet):
    return (f'<g stroke="{grid}" stroke-width="2.6">'
            '<path d="M8 8V32M20 8V32M32 8V32"/></g>'
            f'<circle cx="8" cy="32" r="4.4" fill="{teal}"/>'
            f'<circle cx="20" cy="20" r="4.4" fill="{violet}"/>'
            f'<circle cx="32" cy="8" r="4.4" fill="{teal}"/>')


for mode, grid, teal, violet, ink in (
    ('light', '#E2E6EA', '#0E857C', '#7468D2', '#172126'),
    ('dark', '#A7ADB8', '#2DD4BF', '#A78BFA', '#F3F4F6'),
    ('mono', 'currentColor', 'currentColor', 'currentColor', 'currentColor'),
):
    lattice = mark(grid, teal, violet)
    (DEST / f'mark-{mode}.svg').write_text(svg(lattice))
    word = f'<g fill="{ink}" transform="translate(48 30) scale({scale} {-scale})">{"".join(paths)}</g>'
    (DEST / f'wordmark-{mode}.svg').write_text(svg(lattice + word, width))

# A light tile keeps the tiny mark legible in either browser chrome theme.
favicon = svg('<rect width="40" height="40" rx="8" fill="white"/>' + mark('#C7CBE0', '#0D9488', '#7C6FE0'))
(DEST / 'favicon.svg').write_text(favicon)
for size in (16, 32, 48):
    cairosvg.svg2png(bytestring=favicon.encode(), write_to=str(DEST / f'favicon-{size}.png'), output_width=size, output_height=size)
large = Image.open(BytesIO(cairosvg.svg2png(bytestring=favicon.encode(), output_width=64, output_height=64)))
large.save(DEST / 'favicon.ico', sizes=[(16, 16), (32, 32), (48, 48)])
site = ROOT / 'website/static/brand'
site.mkdir(parents=True, exist_ok=True)
for asset in DEST.iterdir():
    shutil.copyfile(asset, site / asset.name)
