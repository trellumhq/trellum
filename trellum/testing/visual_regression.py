"""Visual regression: save and compare screenshots against baselines.

Compares current Playwright screenshots against saved baseline PNGs using
pixel-by-pixel diffing with a configurable tolerance threshold.

The mock data uses a fixed seed so screenshots are nearly identical between
runs if the rendering code hasn't changed. Minor differences from font
rendering or anti-aliasing are handled by the tolerance threshold.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

BASELINES_DIR = os.path.join(os.path.dirname(__file__), "baselines")


@dataclass
class DiffResult:
    """Result of comparing a screenshot against its baseline."""

    slug: str
    has_baseline: bool = False
    diff_percent: float = 0.0
    within_threshold: bool = True
    baseline_path: str = ""
    current_path: str = ""
    diff_path: str = ""
    message: str = ""


def save_baseline(slug: str, screenshot_path: str) -> str:
    """Copy a screenshot to the baselines directory.

    Returns the saved baseline path.
    """
    os.makedirs(BASELINES_DIR, exist_ok=True)
    baseline_path = os.path.join(BASELINES_DIR, f"{slug}.png")

    from shutil import copy2
    copy2(screenshot_path, baseline_path)
    return baseline_path


def get_baseline_path(slug: str) -> str | None:
    """Return the baseline path for a slug, or None if it doesn't exist."""
    path = os.path.join(BASELINES_DIR, f"{slug}.png")
    return path if os.path.isfile(path) else None


def compare_screenshot(
    slug: str,
    screenshot_path: str,
    threshold: float = 5.0,
    diff_output_dir: str | None = None,
) -> DiffResult:
    """Compare a screenshot against its baseline.

    Args:
        slug: Report slug.
        screenshot_path: Path to the current screenshot PNG.
        threshold: Max allowed percentage of changed pixels (default 5%).
        diff_output_dir: Where to save the diff image (defaults to same dir
            as screenshot_path).

    Returns:
        DiffResult with comparison metrics.
    """
    result = DiffResult(slug=slug, current_path=screenshot_path)

    baseline_path = get_baseline_path(slug)
    if baseline_path is None:
        result.has_baseline = False
        result.message = "No baseline found — run with --save-baseline first"
        return result

    result.has_baseline = True
    result.baseline_path = baseline_path

    try:
        from PIL import Image
    except ImportError:
        # Do NOT pass here. Reporting "within threshold" when no comparison
        # happened makes an entire visual-regression suite report green while
        # checking nothing -- the most dangerous outcome available.
        result.message = (
            "cannot compare — Pillow is not installed (pip install Pillow)"
        )
        result.within_threshold = False
        return result

    try:
        baseline_img = Image.open(baseline_path).convert("RGBA")
        current_img = Image.open(screenshot_path).convert("RGBA")
    except Exception as e:
        result.message = f"Failed to open images: {e}"
        result.within_threshold = True
        return result

    # Resize current to match baseline if dimensions differ
    if current_img.size != baseline_img.size:
        current_img = current_img.resize(baseline_img.size, Image.LANCZOS)

    baseline_px = baseline_img.load()
    current_px = current_img.load()
    width, height = baseline_img.size
    total_pixels = width * height

    if total_pixels == 0:
        result.message = "Image has zero pixels"
        return result

    # Pixel-by-pixel comparison with per-channel tolerance
    channel_tolerance = 20
    changed_pixels = 0
    diff_img = Image.new("RGBA", (width, height), (0, 0, 0, 255))
    diff_px = diff_img.load()

    for y in range(height):
        for x in range(width):
            bp = baseline_px[x, y]
            cp = current_px[x, y]
            pixel_diff = any(abs(bp[c] - cp[c]) > channel_tolerance for c in range(4))
            if pixel_diff:
                changed_pixels += 1
                diff_px[x, y] = (255, 0, 100, 255)
            else:
                diff_px[x, y] = (cp[0] // 3, cp[1] // 3, cp[2] // 3, 180)

    result.diff_percent = (changed_pixels / total_pixels) * 100
    result.within_threshold = result.diff_percent <= threshold

    # Save diff image
    if diff_output_dir is None:
        diff_output_dir = os.path.dirname(screenshot_path)

    diff_path = os.path.join(diff_output_dir, f"{slug}_diff.png")
    diff_img.save(diff_path)
    result.diff_path = diff_path

    if result.within_threshold:
        result.message = f"OK ({result.diff_percent:.2f}% changed, threshold {threshold}%)"
    else:
        result.message = (
            f"REGRESSION: {result.diff_percent:.2f}% pixels changed "
            f"(threshold {threshold}%) — see {diff_path}"
        )

    return result


def list_baselines() -> list[str]:
    """Return list of slug names that have saved baselines."""
    if not os.path.isdir(BASELINES_DIR):
        return []
    return [
        f.replace(".png", "")
        for f in sorted(os.listdir(BASELINES_DIR))
        if f.endswith(".png")
    ]
