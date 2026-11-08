"""Resolve a signing reference from a descriptor read at the published registry."""

import json
import re
import sys


def signing_reference(repository: str, descriptor: object) -> str:
    """Keep the intended GHCR repository instead of a local image alias."""
    if repository not in {"ghcr.io/trellumhq/trellum", "ghcr.io/trellumhq/trellum-runner"}:
        raise ValueError("unexpected release repository")
    if not isinstance(descriptor, dict):
        raise TypeError("registry descriptor must be an object")
    digest = descriptor.get("digest")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("registry descriptor must contain a SHA-256 manifest digest")
    return f"{repository}@{digest}"


def main() -> int:
    try:
        if len(sys.argv) != 2:
            raise ValueError("usage: release_image_ref.py GHCR_REPOSITORY")
        payload = sys.stdin.read(65537)
        if len(payload) > 65536:
            raise ValueError("registry descriptor is too large")
        print(signing_reference(sys.argv[1], json.loads(payload)))
    except (TypeError, ValueError) as exc:
        print(f"Invalid published image descriptor: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
