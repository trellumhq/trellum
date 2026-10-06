"""Select one gallery revision; deliberately dependency-free for CI tests."""
import json
import os
import re
import sys

TAG = re.compile(r"^v(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


def stable_releases(payload):
    pages = payload if isinstance(payload, list) else []
    releases = [release for page in pages for release in (page if isinstance(page, list) else [page])]
    return sorted(
        (release for release in releases if not release.get("draft") and not release.get("prerelease") and TAG.match(release.get("tag_name", ""))),
        key=lambda release: tuple(map(int, TAG.match(release["tag_name"]).groups())),
        reverse=True,
    )


def select(event, repository, ref_name, sha, deploy_input, payload):
    trusted = repository == "trellumhq/trellum"
    if event == "push":
        if trusted and ref_name == "main":
            releases = stable_releases(payload)
            if releases:
                return releases[0]["tag_name"], False, True
        return sha, True, False
    if trusted and ref_name == "main" and (
        event == "schedule" or (event == "workflow_dispatch" and deploy_input)
    ):
        releases = stable_releases(payload)
        if releases:
            return releases[0]["tag_name"], False, bool(event == "schedule" and trusted or deploy_input and trusted)
    return sha, True, False


if __name__ == "__main__":
    payload = json.load(sys.stdin) if not sys.stdin.isatty() else []
    ref, preview, deploy = select(
        os.environ.get("EVENT_NAME", ""), os.environ.get("REPOSITORY", ""),
        os.environ.get("REF_NAME", ""), os.environ.get("SHA", ""),
        os.environ.get("INPUT_DEPLOY", "false").lower() == "true", payload,
    )
    output = os.environ.get("GITHUB_OUTPUT")
    lines = [f"ref={ref}", f"preview={'true' if preview else 'false'}", f"deploy={'true' if deploy else 'false'}"]
    if output:
        with open(output, "a", encoding="utf-8") as stream:
            stream.write("\n".join(lines) + "\n")
    else:
        print("\n".join(lines))
