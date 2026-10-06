# Licensing

Owned Trellum code is released under **AGPL-3.0-only**. The full text is in
[LICENSE](../LICENSE). Third-party and pre-existing contributor notices keep
their own terms; see [THIRD-PARTY.md](../THIRD-PARTY.md).

## What it means

You may run, study, modify, and redistribute Trellum, including commercially,
under the AGPL terms. A modified version that supports remote network
interaction must prominently offer those users its Corresponding Source. The
source link must describe the exact version being run; an upstream link is not
enough for a modified fork.

Generated reports identify the embedded Trellum runtime, carry its licence,
and link the matching source. Report authors retain copyright in their own
data and authored content. Processing data does not relicense it. Whether
additional report code forms one combined program with a modified framework
depends on that code and how it is connected, so this document does not make a
blanket promise about every report repository.

Redistributed copies must preserve applicable copyright, licence, and
modification notices. Trellum is provided without warranty; read `LICENSE` for
the complete terms.

## Source links

Official releases link to their immutable
`https://github.com/trellumhq/trellum/tree/v<version>` tag. A fork or modified
deployment sets `TRELLUM_SOURCE_URL` to a public HTTP or HTTPS location
containing its complete Corresponding Source. The portal exposes that URL to
users and report builds record it in `_meta.json` and their HTML footer.

## Source-file notices

The upstream tree uses the repository licence rather than repeating a header
in every owned file. Preserve existing notices and add the modification notices
the AGPL requires when distributing modified work. Files carrying an explicit
third-party or contributor notice remain under that notice.

## Re-running the dependency audit

Every dependency and bundled component must remain compatible with the
framework's distribution terms. `THIRD-PARTY.md` records the current audit.
Python package metadata can be checked from a clean checkout:

```bash
python - <<'PY'
import json, re, urllib.request
from pathlib import Path

pattern = re.compile(r"^([A-Za-z0-9_.\-]+)\s*(\[[^\]]*\])?\s*([<>=!~].*)?$")
names = {}
for path in (Path("requirements.txt"), Path("requirements-drivers.txt")):
    for raw in path.read_text().splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("-r"):
            continue
        match = pattern.match(line)
        if match:
            names.setdefault(match.group(1).lower(), None)

for name in sorted(names):
    with urllib.request.urlopen(f"https://pypi.org/pypi/{name}/json") as response:
        info = json.load(response)["info"]
    licence = info.get("license_expression") or "; ".join(
        classifier.split("::")[-1].strip()
        for classifier in info.get("classifiers", [])
        if classifier.startswith("License ::")
    ) or (info.get("license") or "UNKNOWN").splitlines()[0]
    print(f"{name:<32} {licence}")
PY
```

A package whose metadata says `UNKNOWN` needs its repository licence inspected
directly. Run the audit whenever a requirement is added or a pin moves, and
update `THIRD-PARTY.md` in the same commit.

Bundled front-end libraries are audited separately from
`static/vendor/MANIFEST.json`; every entry records its licence, and
`static/vendor/UPDATING.md` describes the update process. Their notices and
full licence texts travel with every copy under `static/vendor/`.
