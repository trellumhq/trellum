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

Everything written into the project can be taken back out:
:mod:`trellum.agent.agentremoval` (``trellum setup project --remove``).
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

Build. `--no-serve` exits when the build is done:

```
{py} -m trellum.run reports/<slug> --no-serve
{py} -m trellum.new <slug>
```

When a person will review a local report, start review mode. It prints the
preview URL and enables feedback:

```
{py} -m trellum review start reports/<slug>
{py} -m trellum review poll --report <slug>
```
Keep the report-scoped poll active. Apply feedback, rebuild with `--no-serve`,
then reply and continue polling:

```
{py} -m trellum.run reports/<slug> --no-serve
{py} -m trellum review poll --report <slug> --session <session> --reply "what changed"
```

Continue until the user ends review; use `review end` if they ask you to wrap
up. Build-only work stops after `--no-serve`. If someone explicitly asks for a
plain preview without feedback, use `{py} -m trellum serve --background` and
share its printed URL.
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
    from trellum.agent.agentremoval import strip_hooks

    cleaned, _ = strip_hooks(settings)
    hooks = dict(cleaned.get("hooks") or {})
    for event, groups in hook_config(root).items():
        existing = hooks.get(event) or []
        # Repair the historical bare command, without dropping its neighbours.
        kept = []
        for group in existing:
            if (isinstance(group, dict) and set(group) == {"matcher", "hooks"}
                    and group["matcher"] == groups[0]["matcher"]
                    and isinstance(group["hooks"], list)):
                left = [h for h in group["hooks"] if h != {"type": "command", "command": "trellum"}]
                if left:
                    kept.append({**group, "hooks": left})
            else:
                kept.append(group)
        hooks[event] = kept + groups
    result = {**cleaned, "hooks": hooks}
    return result, result != settings


def dump_json(data: dict) -> str:
    """How every JSON file we touch is written back: 2-space indent, readable
    non-ASCII kept as typed (not turned into ``\\uXXXX`` escapes), final newline."""
    return json.dumps(data, indent=2, ensure_ascii=False) + "\n"


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
    path.write_text(dump_json(merged), encoding="utf-8")
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

For human-facing local previews, the current default is
`{py} -m trellum review start reports/<slug>`. Keep
`{py} -m trellum review poll --report <slug>` active; rebuild with `--no-serve`
before replying with the feedback session, and continue until the user ends
review. Follow `{py} -m trellum guide review` for the full loop. Build-only work
stops after the build; use plain preview only when explicitly requested.
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
    # One blank line between their text and our block. The separator is chosen
    # so removal can take back exactly what was added: a newline-terminated
    # file gets one more newline, never a normalised amount.
    separator = "\n" if existing else ""
    return existing + separator + block + "\n", True


def read_exact(path: Path) -> str | None:
    """File text with its line endings untouched; None when unreadable.

    ``Path.read_text`` translates CRLF to LF and ``write_text`` then writes the
    platform's ending, which would quietly rewrite every line of somebody
    else's file.
    """
    try:
        with open(path, encoding="utf-8", newline="") as handle:
            return handle.read()
    except (OSError, UnicodeDecodeError):
        return None


def write_exact(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(text)


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
        existing = read_exact(path)
        if existing is None:
            results.append((name, "unreadable"))
            continue
        if existing.replace("\r\n", "\n") == pointer:
            # Our own file, unchanged: re-running must not append a signpost
            # to the full pointer it wrote last time.
            results.append((name, "current"))
            continue
        merged, changed = _merge_signpost(existing, py, portal)
        if not changed:
            results.append((name, "current"))
            continue
        write_exact(path, merged)
        results.append((name, "appended"))

    settings = root / ".claude" / "settings.json"
    results.append((str(Path(".claude") / "settings.json"),
                    _update_settings_file(settings, root)))
    return results


def command_for(root: str | os.PathLike[str], tail: str) -> str:
    """A ``trellum`` command to paste into a terminal for the project at *root*.

    From the project root it is the same interpreter the pointers name. From
    anywhere else the relative interpreter would not resolve, so the absolute
    one is used and the project is passed as ``--dest``.
    """
    import shlex

    root = Path(root).resolve()
    if root == Path.cwd().resolve():
        return f"{shlex.quote(hook_interpreter(root))} -m trellum {tail}"
    return f"{shlex.quote(Path(sys.executable).as_posix())} -m trellum {tail} --dest {shlex.quote(str(root))}"


def setup_summary(root: str | os.PathLike[str], rows: list[tuple[str, str]]) -> list[str]:
    """Where the guidance and hooks went, how to check them, how to undo them.

    Shared by ``trellum init`` and ``trellum setup project`` so both end the
    same way. *rows* is what :func:`write_project_files` returned; a file it
    could not read is not listed as set up.
    """
    done = [name for name, status in rows if status != "unreadable"]
    guidance = [n for n in done if n in ("CLAUDE.md", "AGENTS.md")]
    hooks = [n for n in done if n == ".claude/settings.json"]
    lines = ["Agent setup:"]
    if guidance:
        lines.append(f"  guidance  {', '.join(guidance)}")
    if hooks:
        lines.append(f"  hooks     {', '.join(hooks)}  (SessionStart, PostToolUse)")
    if len(lines) == 1:
        return []
    lines += [f"  check     {command_for(root, 'doctor')}",
              f"  remove    {command_for(root, 'setup project --remove')}"
              "   (only Trellum's parts; your own content stays)"]
    return lines


def remove_project_files(root: str | os.PathLike[str]) -> list[tuple[str, str]]:
    """Take out what :func:`write_project_files` and ``write_mcp_files`` added.

    ``[(relative_path, status)]``; see :mod:`trellum.agent.agentremoval` for
    what is removed, what is kept, and why.
    """
    from trellum.agent import agentremoval

    return [(row.name, row.status) for row in agentremoval.remove(root)]


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
