"""Making the framework findable, rather than merely documented.

A measured report build ran ``cat CLAUDE.md`` as its second command, found
nothing, and then located ``AGENTS.md`` by searching ``site-packages``. Nothing
had pointed it anywhere. The framework's guidance was installed and effectively
invisible.

There is no filename every agent reads: Claude Code reads ``CLAUDE.md`` and not
``AGENTS.md``; Cursor and Codex read ``AGENTS.md`` and not ``CLAUDE.md``. So this
module writes a small pointer in each, plus an optional session hook that runs
``trellum`` at the start of a session. All three say the same short thing:
*the framework can describe itself, ask it*.

Two delivery paths, deliberately:

* :func:`write_project_files` runs from ``trellum init`` and needs no separate
  step. It writes into the project, so it travels with the repository to every
  teammate and every agent that clones it.
* :func:`install_session_hooks` is opt-in and machine-wide. Convenient, but
  invisible in the repository and easy to have without knowing -- which is why
  it is not the primary path.
"""
from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

MARKER = "trellum"

#: For CLAUDE.md / AGENTS.md in a consumer project. Deliberately tiny: it exists
#: to route, not to teach. The teaching lives behind `trellum guide`, which is
#: fetched when relevant instead of resident always.
#: `{py}` is substituted with the interpreter that actually has the framework
#: installed. It is not decoration: a bare `trellum` is only on PATH inside an
#: activated virtualenv, and agents do not activate virtualenvs -- a measured run
#: burned ten tool calls working out the right invocation because this file told
#: it to type `trellum`.
POINTER = """\
# Reports in this repository

Reports here are built with the BI report framework, installed as a Python
package. **The framework describes itself — ask it rather than reading its
source.**

Use this exact interpreter, **run from the repository root**:

```
{py} -m trellum                      what it is, the components, the commands
{py} -m trellum guide <topic>...     depth on demand; several topics at once
{py} -m trellum checks [<id>...]     validator check ids, or what one means
{py} -m trellum validate reports/<x> validation state of a built report
```

`trellum` alone is not on PATH. Report paths like `reports/<slug>` are
relative to the repository root, so run these from there.{cwd_note}

Before writing a generator, read these two. They are not guessable from the
API, and getting them wrong produces a report that builds cleanly and shows
nothing useful:

```
{py} -m trellum guide queries format
```

Column names come from the warehouse, never from an example — check them before
writing SQL rather than after the build fails:

```
{py} -m trellum data
```

To connect a database, hand the details to this rather than writing
`data-sources/config.yaml` and `.env` yourself. It puts each part where it
belongs, then connects — so a wrong password is reported now, not at the next
build:

```
{py} -m trellum datasource add <name> --type postgres --host <h> --port <p> --database <db> --user <u> --password <pw>
```

Read files with your file-reading tool rather than `cat`, `ls` or `grep` in a
shell: every shell command starts a process and your native tools do not.
Batching reads into one `cat a; cat b; cat c` looks cheaper and is the opposite
— three native reads cost nothing, one shell command does. Keep the shell for
the build, the validator and git.

Ask for several topics in one command rather than one at a time — every command
re-reads your whole context, so four separate calls cost four times the context,
not four times the content. `guide report` returns the whole authoring contract
at once.

Build. `--no-serve` exits when the build is done; without it the command starts
a preview server and never returns:

```
{py} -m trellum.run reports/<slug> --no-serve
{py} -m trellum.new <slug>
```

Serve only when a human is going to look. It blocks until killed, so run it in
the background, and wait for its `Serving at` line before opening the URL — it
rebuilds the report before it binds the port. A single report is served at `/`,
not at `/<slug>`:

```
{py} -m trellum.run reports/<slug>
{py} -m trellum.run --all --serve      every report, index at /
```
{portal}
A report is not finished until the validator reports no FAILs.
"""

#: Appended to POINTER only when the interpreter above came out relative, which
#: is the normal case and the one with the confusing failure. Kept conditional
#: because the same file is written with an absolute path when the virtualenv
#: lives outside the project, and a warning about relative paths would then be
#: describing something the reader cannot see.
RELATIVE_INTERPRETER_NOTE = """ The interpreter path is relative too, so a
command run after a `cd` into a subdirectory fails with `No such file or
directory` — naming the interpreter, not your command. If one dies that way,
`cd` back to the root rather than going looking for a Python."""

#: The environment variable both MCP config files reference for the key. The
#: value lives in `.env` and nowhere else.
MCP_ENV_VAR = "TRELLUM_API_KEY"

#: Added to both pointers only when `.mcp.json` names a `trellum` server, so a
#: repository with no portal never reads about one. Under ten lines: the loop
#: itself lives behind `guide portal`, fetched when relevant.
PORTAL_SECTION = """
## Portal

The portal this repository publishes to is reachable as MCP server `trellum`
(see .mcp.json). Use its tools to check what changed since the last publish,
publish, run a report, read build status and errors, test or configure a
data source, and manage alerts. `{py} -m trellum guide portal` walks the
loop. The key is TRELLUM_API_KEY in .env; never paste it anywhere else.
"""


def _hook_entry(command: str, timeout: int = 10) -> dict:
    return {"type": "command", "command": command, "timeout": timeout}


def hook_interpreter(root: str | os.PathLike[str] | None = None) -> str:
    """How a hook should invoke Python.

    Hooks run in a plain shell with no virtualenv activated, so the ``trellum``
    console script is not on PATH and a bare ``trellum`` fails with "command
    not found" -- an error banner on every session start. The interpreter has to
    be named explicitly.

    When the running interpreter lives inside the project (the normal case: a
    ``.venv`` beside the reports) a *relative* path is used, so the resulting
    ``.claude/settings.json`` can be committed and still work for a teammate who
    creates their own virtualenv in the same place. Otherwise fall back to the
    absolute path, which at least works on this machine.
    """
    exe = Path(sys.executable)
    if root is not None:
        try:
            rel = exe.resolve().relative_to(Path(root).resolve())
            return rel.as_posix()
        except ValueError:
            pass
    return exe.as_posix()


def _is_ours(group: dict) -> bool:
    """Is this hook group one we wrote?

    Matches on `-m trellum` rather than the leading token, because the leading
    token is an interpreter path that differs per machine.
    """
    for h in group.get("hooks") or []:
        cmd = h.get("command")
        if not isinstance(cmd, str):
            continue
        if "-m trellum" in cmd or cmd.split()[0:1] == [MARKER]:
            return True
    return False


def hook_config(root: str | os.PathLike[str] | None = None) -> dict:
    """The two hooks, as they appear in a settings.json.

    SessionStart makes the framework's existence known without anyone having to
    look. PostToolUse hands back validator state after a report file changes --
    scoped and silent by default; see ``cli._cmd_hook_post_edit`` for the rules.

    Both invoke ``<interpreter> -m trellum`` rather than the ``trellum``
    console script: hooks run without an activated virtualenv, where the script
    is not on PATH.
    """
    py = hook_interpreter(root)
    return {
        "SessionStart": [{
            "matcher": "",
            "hooks": [_hook_entry(f"{py} -m trellum")],
        }],
        "PostToolUse": [{
            "matcher": "Edit|Write",
            "hooks": [_hook_entry(f"{py} -m trellum hook post-edit", timeout=15)],
        }],
    }


def merge_hooks(settings: dict,
                root: str | os.PathLike[str] | None = None) -> tuple[dict, bool]:
    """Add our hooks to *settings*, replacing only our own previous entries.

    Marker-based rather than positional, so re-running repairs rather than
    duplicating, and somebody else's hooks are never touched.
    """
    settings = dict(settings)
    hooks = dict(settings.get("hooks") or {})
    changed = False
    for event, groups in hook_config(root).items():
        existing = [g for g in (hooks.get(event) or []) if not _is_ours(g)]
        merged = existing + groups
        if merged != (hooks.get(event) or []):
            hooks[event] = merged
            changed = True
    if changed:
        settings["hooks"] = hooks
    return settings, changed


def _update_json_file(path: Path, merge) -> str:
    """Read, ``merge(current) -> (merged, changed)``, write. One-word status.

    The one read-merge-write every JSON file we touch goes through: the hooks
    in settings.json, the server entry in the two MCP files.
    """
    try:
        current = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        return "unreadable"
    if not isinstance(current, dict):
        return "unreadable"
    merged, changed = merge(current)
    if not changed:
        return "current"
    status = "updated" if path.is_file() else "created"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(merged, indent=2) + "\n", encoding="utf-8")
    return status


def _update_settings_file(path: Path, root: Path | None = None) -> str:
    """Merge our hooks into a settings.json. Returns a one-word status."""
    return _update_json_file(path, lambda current: merge_hooks(current, root))


def verify_hook(root: str | os.PathLike[str]) -> tuple[bool, str]:
    """Run the SessionStart command exactly as the harness will, from *root*.

    Writing a hook without checking it is how a broken one ends up firing on
    every session start. Cheap to verify, so verify.
    """
    import shlex
    import subprocess

    cmd = hook_config(root)["SessionStart"][0]["hooks"][0]["command"]
    argv = shlex.split(cmd)

    # The harness runs the command through a shell with the project as the
    # working directory, so a relative interpreter resolves against the project.
    # subprocess does not do that on Windows -- it looks the executable up
    # against the PARENT's cwd and ignores `cwd=` for that lookup -- so resolve
    # it here rather than reporting a working hook as broken.
    exe = Path(argv[0])
    if not exe.is_absolute():
        argv[0] = str((Path(root) / exe).resolve())

    try:
        proc = subprocess.run(argv, cwd=str(root),
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"{cmd!r} could not run: {exc}"
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        return False, f"{cmd!r} exited {proc.returncode}: " \
                      f"{detail[0] if detail else 'no output'}"
    return True, cmd


#: Fence markers for the block appended to a file the user already owns.
#: Everything between them is ours to rewrite; everything outside is theirs and
#: is never touched. Same contract as ``merge_hooks`` -- re-running repairs in
#: place instead of appending a second copy.
BLOCK_START = "<!-- trellum:start -->"
BLOCK_END = "<!-- trellum:end -->"

#: What we add to somebody else's file. Deliberately a few lines rather than
#: the full POINTER: their file is theirs, and a signpost only has to name the
#: interpreter and the command. The framework answers the rest itself, so
#: nothing here can go stale.
SIGNPOST = """\
{start}
## Reports in this repository

Reports here are built with the Trellum reporting framework. **It describes
itself — ask it rather than reading its source.** Run from the repository root:

```
{py} -m trellum                  what it is, the components, the commands
{py} -m trellum guide <topic>... depth on demand, several topics at once
```

`trellum` alone is not on PATH, so name the interpreter as shown.
{portal}{end}
"""


def _signpost(py: str, portal: str = "") -> str:
    return SIGNPOST.format(start=BLOCK_START, end=BLOCK_END, py=py, portal=portal)


def _merge_signpost(existing: str, py: str, portal: str = "") -> tuple[str, bool]:
    """Return (new_text, changed) with our block added or refreshed.

    Marker-based like the hooks: an older block is replaced where it sits
    rather than a second one being appended, so re-running after an upgrade
    repairs instead of accumulating.
    """
    block = _signpost(py, portal).rstrip("\n")
    start = existing.find(BLOCK_START)
    end = existing.find(BLOCK_END)
    if start != -1 and end != -1 and end > start:
        current = existing[start:end + len(BLOCK_END)]
        if current == block:
            return existing, False
        return existing[:start] + block + existing[end + len(BLOCK_END):], True
    separator = "" if existing.endswith("\n\n") else (
        "\n" if existing.endswith("\n") else "\n\n")
    return existing + separator + block + "\n", True


def write_project_files(root: str | os.PathLike[str]) -> list[tuple[str, str]]:
    """Write the pointer files into a consumer project.

    Returns ``[(relative_path, status)]`` where status is created / appended /
    current / updated / unreadable.

    A file that does not exist gets the full pointer. A file that does exist
    keeps everything it already says and gains a short marked signpost at the
    end. Skipping it entirely was the old behaviour, and it was right when an
    AGENTS.md was rare; now that most projects have one, skipping means the
    framework installs and stays invisible to every agent that reads only that
    file. Their content is still never rewritten -- only our own fenced block
    is, which is the same bargain ``merge_hooks`` strikes with settings.json.
    """
    root = Path(root)
    results: list[tuple[str, str]] = []
    py = hook_interpreter(root)
    portal = PORTAL_SECTION.format(py=py) if portal_server(root) else ""
    pointer = POINTER.format(
        py=py,
        cwd_note="" if Path(py).is_absolute() else RELATIVE_INTERPRETER_NOTE,
        portal=portal,
    )

    for name in ("CLAUDE.md", "AGENTS.md"):
        path = root / name
        if not path.exists():
            path.write_text(pointer, encoding="utf-8")
            results.append((name, "created"))
            continue
        try:
            existing = path.read_text(encoding="utf-8")
        except OSError:
            results.append((name, "unreadable"))
            continue
        if existing == pointer:
            # Our own file, unchanged: re-running must not append a signpost
            # to the full pointer it wrote last time.
            results.append((name, "current"))
            continue
        merged, changed = _merge_signpost(existing, py, portal)
        if not changed:
            results.append((name, "current"))
            continue
        path.write_text(merged, encoding="utf-8")
        results.append((name, "appended"))

    settings = root / ".claude" / "settings.json"
    results.append((str(Path(".claude") / "settings.json"),
                    _update_settings_file(settings, root)))
    return results


def remove_project_files(root: str | os.PathLike[str]) -> list[tuple[str, str]]:
    """Strip our signpost block, leaving the user's own content untouched.

    The counterpart to appending: anything we add to a file we do not own, we
    must be able to take back out.
    """
    root = Path(root)
    results: list[tuple[str, str]] = []
    for name in ("CLAUDE.md", "AGENTS.md"):
        path = root / name
        if not path.is_file():
            results.append((name, "absent"))
            continue
        try:
            existing = path.read_text(encoding="utf-8")
        except OSError:
            results.append((name, "unreadable"))
            continue
        start = existing.find(BLOCK_START)
        end = existing.find(BLOCK_END)
        if start == -1 or end == -1 or end < start:
            results.append((name, "no block"))
            continue
        stripped = (existing[:start].rstrip("\n")
                    + "\n" + existing[end + len(BLOCK_END):].lstrip("\n"))
        path.write_text(stripped.rstrip("\n") + "\n", encoding="utf-8")
        results.append((name, "removed"))
    for name in MCP_FILES:
        path = root / name
        if not path.is_file():
            results.append((name, "absent"))
            continue
        status = _update_json_file(path, _without_our_server)
        results.append((name, {"current": "no entry", "updated": "removed"}.get(status, status)))
    return results


# ── the portal: server entry, key, and what doctor checks ─────────────────

#: The two files `setup portal` writes, each in its harness's own shape. The
#: key is in neither: both reference MCP_ENV_VAR the way that harness expands
#: it, and the value sits in `.env` beside the other secrets.
MCP_FILES = (".mcp.json", ".cursor/mcp.json")


def mcp_servers(url: str) -> dict[str, dict]:
    """``{file: server entry}`` for one studio, ``<portal>/s/<org>/<studio>``."""
    url = url.rstrip("/")
    if not url.endswith("/mcp"):
        url += "/mcp"
    return {
        ".mcp.json": {"type": "http", "url": url,
                      "headers": {"Authorization": "Bearer ${TRELLUM_API_KEY}"}},
        ".cursor/mcp.json": {"url": url,
                             "headers": {"Authorization": "Bearer ${env:TRELLUM_API_KEY}"}},
    }


def merge_mcp(config: dict, server: dict) -> tuple[dict, bool]:
    """Set ``mcpServers.trellum`` to *server*; every other server is theirs
    and stays exactly as it is. The ``merge_hooks`` contract."""
    servers = dict(config.get("mcpServers") or {})
    if servers.get(MARKER) == server:
        return config, False
    servers[MARKER] = server
    return {**config, "mcpServers": servers}, True


def _without_our_server(config: dict) -> tuple[dict, bool]:
    servers = dict(config.get("mcpServers") or {})
    if MARKER not in servers:
        return config, False
    del servers[MARKER]
    return {**config, "mcpServers": servers}, True


def write_mcp_files(root: str | os.PathLike[str], url: str) -> list[tuple[str, str]]:
    """Write or refresh our server entry in both MCP files."""
    root = Path(root)
    return [(name, _update_json_file(root / name, lambda c, s=server: merge_mcp(c, s)))
            for name, server in mcp_servers(url).items()]


def portal_server(root: str | os.PathLike[str]) -> dict | None:
    """The ``trellum`` server entry in the project's ``.mcp.json``, if any."""
    try:
        conf = json.loads((Path(root) / ".mcp.json").read_text(encoding="utf-8"))
        entry = conf["mcpServers"][MARKER]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return entry if isinstance(entry, dict) else None


def env_value(path: Path, key: str) -> str:
    """One value out of a ``.env``; empty when absent or unreadable."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    for line in lines:
        k, sep, v = line.partition("=")
        if sep and k.strip() == key:
            return v.strip()
    return ""


def env_ignored(root: str | os.PathLike[str]) -> bool | None:
    """Does git ignore ``.env`` here? None outside a git checkout (or without
    git): nothing to commit it into, nothing to check."""
    import subprocess

    try:
        proc = subprocess.run(["git", "check-ignore", "-q", ".env"], cwd=str(root),
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return {0: True, 1: False}.get(proc.returncode)


_STUDIO_MCP_URL = re.compile(r"https?://[^/\s]+(/\S*)?/s/[^/\s]+/[^/\s]+/mcp")


def portal_problems(root: str | os.PathLike[str]) -> list[str] | None:
    """What is wrong with the portal wiring: ``[]`` when nothing, None when
    there is no server entry to check. Each of these is silent otherwise --
    the harness simply never lists the server's tools."""
    server = portal_server(root)
    if server is None:
        return None
    problems = []
    url = str(server.get("url") or "")
    if not _STUDIO_MCP_URL.fullmatch(url):
        problems.append(f"url {url!r} is not <portal>/s/<org>/<studio>/mcp")
    auth = str((server.get("headers") or {}).get("Authorization") or "")
    if MCP_ENV_VAR not in auth:
        problems.append(f"Authorization header does not reference {MCP_ENV_VAR}")
    if not env_value(Path(root) / ".env", MCP_ENV_VAR):
        problems.append(f"{MCP_ENV_VAR} is not set in .env")
    return problems


def install_session_hooks(home: str | os.PathLike[str] | None = None) -> list[tuple[str, str]]:
    """Install the hooks machine-wide, for every project.

    Opt-in on purpose. A hook here is invisible from any repository, which makes
    it easy to end up with one and not know why an agent behaves as it does.
    """
    base = Path(home) if home else Path.home()
    targets = {
        "Claude Code": base / ".claude" / "settings.json",
    }
    return [(name, _update_settings_file(path)) for name, path in targets.items()]
