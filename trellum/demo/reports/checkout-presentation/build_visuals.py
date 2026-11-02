"""Rebuild the two authored charts from the committed September snapshot.

Run ``PYTHONPATH=. python trellum/demo/reports/checkout-presentation/build_visuals.py``
from the repository root. These are derived
illustrations, not browser captures; the original capture PNG/JSON pairs stay
unchanged. The charts use the bundled Inter fonts and Trellum light palette.
No warehouse or regenerated demo fixture is used.
"""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import trellum
from trellum.themes import DefaultTheme


ROOT = Path(__file__).resolve().parent
FONTS = Path(trellum.__file__).resolve().parent / "static" / "vendor" / "fonts"
PALETTE = DefaultTheme
SCALE = 2


def canvas(width, height):
    image = Image.new("RGB", (width * SCALE, height * SCALE), PALETTE.bg_card)
    return image, ImageDraw.Draw(image)


def text(draw, xy, value, size=28, color=None, bold=False, anchor=None):
    font = ImageFont.truetype(str(FONTS / f"inter-{700 if bold else 400}.ttf"), size * SCALE)
    draw.text(tuple(v * SCALE for v in xy), str(value), font=font,
              fill=color or PALETTE.text_main, anchor=anchor)


def rect(draw, bounds, color, radius=0):
    draw.rounded_rectangle(tuple(v * SCALE for v in bounds), radius=radius * SCALE, fill=color)


def build_comparison(data):
    image, draw = canvas(1040, 710)
    rect(draw, (32, 32, 56, 56), PALETTE.bg_header, 5)
    text(draw, (70, 25), "Mobile", 30, bold=True)
    rect(draw, (270, 32, 294, 56), PALETTE.text_secondary, 5)
    text(draw, (308, 25), "Desktop", 30)
    text(draw, (1008, 28), "Stage conversion", 24, PALETTE.text_muted, anchor="ra")
    steps = [("Product view → cart", "product_views", "carts"),
             ("Cart → checkout", "carts", "checkout_starts"),
             ("Checkout → purchase", "checkout_starts", "purchases")]
    left, length = 32, 862
    for i, (label, prior, following) in enumerate(steps):
        y = 108 + i * 168
        text(draw, (left, y), label, 32, bold=True)
        for j, (device, color) in enumerate((("mobile", PALETTE.bg_header),
                                            ("desktop", PALETTE.text_secondary))):
            value = data[device][following] / data[device][prior]
            bar_y = y + 51 + j * 43
            rect(draw, (left, bar_y, left + length, bar_y + 29), PALETTE.bg_main, 5)
            rect(draw, (left, bar_y, left + length * value, bar_y + 29), color, 5)
            text(draw, (1008, bar_y - 5), f"{value:.1%}", 30, color, bold=True, anchor="ra")
    text(draw, (32, 614), "0%", 24, PALETTE.text_muted)
    text(draw, (894, 614), "100%", 24, PALETTE.text_muted, anchor="ra")
    text(draw, (32, 667), "September 2026 · All channels · Synthetic snapshot", 24,
         PALETTE.text_secondary)
    image.save(ROOT / "evidence" / "stage-comparison.png", optimize=True)


def build_scenario(data):
    image, draw = canvas(1200, 540)
    mobile = data["mobile"]
    increase = data["scenario_completion_increase_pp"]
    extra = mobile["checkout_starts"] * increase / 100
    current = mobile["purchases"]
    scenario = current + extra
    completion = current / mobile["checkout_starts"]
    text(draw, (36, 20), "ILLUSTRATIVE SCENARIO · NOT A FORECAST", 27,
         PALETTE.bg_header, bold=True)
    rect(draw, (36, 90, 390, 424), PALETTE.bg_main, 18)
    text(draw, (66, 117), f"+{increase} pp", 84, PALETTE.bg_header, bold=True)
    text(draw, (66, 223), "checkout completion", 27)
    text(draw, (66, 285), f"{completion:.2%} → {completion + increase / 100:.2%}", 31, bold=True)
    text(draw, (66, 343), "Same 3,657 checkout starts", 23, PALETTE.text_secondary)

    left, length, ceiling = 446, 664, 2500
    for i, (label, value, color) in enumerate((("Current mobile purchases", current, PALETTE.text_secondary),
                                              ("With a 5 pp improvement", scenario, PALETTE.bg_header))):
        y = 104 + i * 158
        text(draw, (left, y), label, 29, bold=True)
        rect(draw, (left, y + 51, left + length, y + 104), PALETTE.bg_main, 6)
        rect(draw, (left, y + 51, left + length * value / ceiling, y + 104), color, 6)
        text(draw, (left + 16, y + 59), f"{value:,.0f}", 32, PALETTE.on_accent, bold=True)
    text(draw, (446, 405), f"≈ {extra:,.0f} extra purchases  /  +{extra / current:.1%}", 32,
         PALETTE.bg_header, bold=True)
    text(draw, (36, 479), "September 2026 · Synthetic baseline · Traffic and earlier funnel stages held constant", 24,
         PALETTE.text_secondary)
    image.save(ROOT / "evidence" / "recovery-scenario.png", optimize=True)


if __name__ == "__main__":
    snapshot = json.loads((ROOT / "evidence" / "checkout-snapshot.json").read_text())
    build_comparison(snapshot)
    build_scenario(snapshot)
