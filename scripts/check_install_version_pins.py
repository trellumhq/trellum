"""Check that current installation examples pin the release in both packages."""

from pathlib import Path
import re
import sys


INSTALL_GUIDES = (
    "docs/customer/install/try-it.md",
    "docs/customer/install/docker-compose.md",
    "docs/customer/install/verify-images.md",
)
VERSION_RE = re.compile(r'^__version__ = "([^"]+)"$', re.MULTILINE)
PIN_RE = re.compile(r"\bTRELLUM_VERSION\s*=\s*['\"]?(v\d+\.\d+\.\d+)")


def check(root: Path) -> list[str]:
    errors = []
    versions = {}
    for package in ("trellum_portal", "trellum"):
        path = root / package / "__init__.py"
        match = VERSION_RE.search(path.read_text(encoding="utf-8"))
        if not match:
            errors.append(f"could not read {package}.__version__ from {path}")
        else:
            versions[package] = match.group(1)

    if len(versions) == 2 and len(set(versions.values())) != 1:
        errors.append(
            "package versions disagree: "
            + ", ".join(f"{name}={version}" for name, version in versions.items())
        )

    if len(versions) == 2 and len(set(versions.values())) == 1:
        expected = f"v{next(iter(versions.values()))}"
        for guide in INSTALL_GUIDES:
            path = root / guide
            pins = set(PIN_RE.findall(path.read_text(encoding="utf-8")))
            if pins != {expected}:
                errors.append(f"{guide} must pin TRELLUM_VERSION={expected}; found {sorted(pins)}")
    return errors


if __name__ == "__main__":
    failures = check(Path(__file__).resolve().parents[1])
    if failures:
        print("\n".join(f"ERROR: {failure}" for failure in failures), file=sys.stderr)
        raise SystemExit(1)
    print("Installation guide release pins match both package versions.")
