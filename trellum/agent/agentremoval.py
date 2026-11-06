"""Taking the agent integration back out, and only what setup put there.

``trellum setup project`` writes into files a team shares, so undoing it has to
be as careful as writing it was. The rule is the one the writers follow:
**remove what is provably ours, leave everything else byte-for-byte alone, and
say so when something is left.**

What counts as ours, per file:

* ``CLAUDE.md`` / ``AGENTS.md``: the fenced ``trellum:start`` .. ``trellum:end``
  block (the fence is the contract: everything inside is ours to rewrite, and
  so to remove), or the whole file when its entire content is the pointer
  ``setup`` creates. A pointer someone has edited since is *not* deleted: it
  now holds their words too, so it is reported and left for them.
* ``.claude/settings.json``: only hook entries identical in shape to the two
  ``setup`` writes (matcher, ``type``, ``timeout`` and a command of exactly
  ``<interpreter> -m trellum`` / ``<interpreter> -m trellum hook post-edit``).
  A custom hook that merely mentions ``-m trellum`` is not matched.
* ``.mcp.json`` / ``.cursor/mcp.json``: the ``trellum`` server entry, when it
  has the shape ``setup portal`` writes.

``.env`` is never opened: the key stays where it is, and the CLI says so.
Anything unreadable (invalid JSON, undecodable text) is left untouched.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import NamedTuple

from trellum.agent import agentsetup as setup

#: What each outcome means. ``deleted`` and ``removed`` both changed the
#: project; ``kept`` and ``unreadable`` mean part of the integration is still
#: there and the caller should say so (see :func:`incomplete`).
STATUSES = ("removed", "deleted", "nothing", "absent", "kept", "unreadable")


class Row(NamedTuple):
    name: str
    status: str
    note: str = ""


def incomplete(rows: list[Row]) -> bool:
    """Is some Trellum content still there, or was something left unexamined?"""
    return any(r.status in ("kept", "unreadable") or r.note for r in rows)


# ── guidance files ───────────────────────────────────────────────────────

#: A sentinel for the interpreter: the one part of the pointer that differs per
#: machine, and so the one part allowed to differ when recognising our file.
_PY = "\x00py\x00"

#: How the pointer file ``setup`` creates begins. A file that starts this way
#: but is not an exact pointer was ours once and has been edited since.
_POINTER_HEAD = setup.POINTER[:setup.POINTER.index("{py}")]


def _pointer_patterns() -> list[re.Pattern[str]]:
    """The pointer ``setup`` writes, once per variant it can take.

    The cwd note and the Portal section are conditional, and the interpreter is
    whatever ran setup, so a pointer is recognised as ours when it matches one
    of these exactly with a single consistent interpreter -- never loosely.
    """
    patterns = []
    for note in ("", setup.RELATIVE_INTERPRETER_NOTE):
        for portal in ("", setup.PORTAL_SECTION.format(py=_PY)):
            text = setup.POINTER.format(py=_PY, cwd_note=note, portal=portal)
            first, *rest = text.split(_PY)
            parts = [re.escape(first), r"(?P<py>[^\n]+?)", re.escape(rest[0])]
            for part in rest[1:]:
                parts += [r"(?P=py)", re.escape(part)]
            patterns.append(re.compile("".join(parts)))
    return patterns


_POINTERS = _pointer_patterns()


def is_written_pointer(text: str) -> bool:
    """Is *text* exactly the pointer ``setup`` creates (any interpreter)?"""
    text = text.replace("\r\n", "\n")
    return any(p.fullmatch(text) for p in _POINTERS)


def strip_block(text: str) -> tuple[str | None, str]:
    """Return ``(text without our block, "")``, or ``(None, reason)``.

    Reverses :func:`~trellum.agent.agentsetup._merge_signpost` exactly: the one
    blank line and the newline it added come out, and nothing else changes, so
    a file that ended in ``\\n`` ends in ``\\n`` again. ``reason`` is ``"none"``
    when there is no block and ``"unbalanced"`` when the fence markers are not
    one start followed by one end -- ambiguous, so not touched.
    """
    starts, ends = text.count(setup.BLOCK_START), text.count(setup.BLOCK_END)
    if starts == 0 and ends == 0:
        return None, "none"
    start, end = text.find(setup.BLOCK_START), text.find(setup.BLOCK_END)
    if starts != 1 or ends != 1 or end < start:
        return None, "unbalanced"
    before = text[:start]
    after = text[end + len(setup.BLOCK_END):]
    after = after[2:] if after.startswith("\r\n") else after[1:] if after.startswith("\n") else after
    if before.endswith("\r\n"):
        before = before[:-2]
    elif before.endswith("\n"):
        before = before[:-1]
    return before + after, ""


def _remove_guidance(path: Path, name: str) -> Row:
    if path.is_symlink() or path.parent.is_symlink():
        return Row(name, "kept", "symlink; left untouched")
    if not path.is_file():
        return Row(name, "absent")
    text = setup.read_exact(path)
    if text is None:
        return Row(name, "unreadable", "not valid UTF-8 text or not readable; left untouched")

    stripped, reason = strip_block(text)
    if reason == "unbalanced":
        return Row(name, "kept", f"{setup.BLOCK_START} / {setup.BLOCK_END} are not a matched "
                                 "pair; left untouched -- delete the block by hand")
    remainder = text if stripped is None else stripped

    if is_written_pointer(remainder):
        if path.is_symlink():
            return Row(name, "kept", "is a symlink holding Trellum's pointer; delete it by hand")
        path.unlink()
        return Row(name, "deleted")
    if stripped is not None:
        setup.write_exact(path, stripped)
        return Row(name, "removed")
    if remainder.replace("\r\n", "\n").startswith(_POINTER_HEAD):
        return Row(name, "kept", "was created by setup but has been edited since, so it also "
                                 "holds your words; delete the Trellum section by hand")
    return Row(name, "nothing")


# ── JSON files ───────────────────────────────────────────────────────────

#: Exactly what ``hook_config`` writes for each event: group matcher, entry
#: timeout, and the shape of the command. The interpreter is any path-like
#: text without shell syntax, so `X && -m trellum` can never match.
_INTERPRETER = r"[^;&|<>`$\n]+"
_HOOK_SHAPES = {
    "SessionStart": ("", re.compile(_INTERPRETER + r" -m trellum"), 10),
    "PostToolUse": ("Edit|Write", re.compile(_INTERPRETER + r" -m trellum hook post-edit"), 15),
}


def _is_written_hook(entry: object, command: re.Pattern[str], timeout: int) -> bool:
    return (isinstance(entry, dict)
            and set(entry) == {"type", "command", "timeout"}
            and entry["type"] == "command"
            and entry["timeout"] == timeout
            and isinstance(entry["command"], str)
            and command.fullmatch(entry["command"]) is not None)


def strip_hooks(settings: dict) -> tuple[dict, bool]:
    """*settings* without the hook entries ``setup`` wrote; ``(result, changed)``.

    Entry-level and shape-exact. A group is dropped only once none of its
    entries remain, an event only once it has no groups, ``hooks`` only once it
    has no events. Anything that does not look like what we write -- another
    tool's hook, a hand-edited entry, a group with extra keys -- stays.
    """
    hooks = settings.get("hooks")
    if not isinstance(hooks, dict):
        return settings, False
    remaining, changed = dict(hooks), False
    for event, (matcher, command, timeout) in _HOOK_SHAPES.items():
        groups = hooks.get(event)
        if not isinstance(groups, list):
            continue
        kept = []
        for group in groups:
            ours = (isinstance(group, dict) and set(group) == {"matcher", "hooks"}
                    and group["matcher"] == matcher and isinstance(group["hooks"], list))
            if not ours:
                kept.append(group)
                continue
            left = [h for h in group["hooks"] if not _is_written_hook(h, command, timeout)]
            if len(left) == len(group["hooks"]):
                kept.append(group)
                continue
            changed = True
            if left:
                kept.append({**group, "hooks": left})
        if kept:
            remaining[event] = kept
        else:
            remaining.pop(event, None)
    if not changed:
        return settings, False
    result = {k: v for k, v in settings.items() if k != "hooks"}
    if remaining:
        result["hooks"] = remaining
    return result, True


def _mentions_trellum(settings: dict) -> bool:
    """Does any hook still run ``-m trellum``? Reported, never removed."""
    hooks = settings.get("hooks")
    groups = (g for e in (hooks.values() if isinstance(hooks, dict) else ())
              for g in (e if isinstance(e, list) else ()))
    return any("-m trellum" in str(h.get("command", ""))
               for g in groups if isinstance(g, dict)
               for h in (g.get("hooks") if isinstance(g.get("hooks"), list) else ())
               if isinstance(h, dict))


def strip_server(config: dict, name: str) -> tuple[dict, bool, str]:
    """*config* without the ``trellum`` server, when it is the one setup wrote.

    ``(result, changed, note)``; *note* is set when an entry named ``trellum``
    was found that setup did not write (someone else's server of the same name)
    and so was left.
    """
    servers = config.get("mcpServers")
    entry = servers.get(setup.MARKER) if isinstance(servers, dict) else None
    if entry is None:
        return config, False, ""
    url = entry.get("url") if isinstance(entry, dict) else None
    written = (isinstance(url, str) and setup._STUDIO_MCP_URL.fullmatch(url) is not None
               and entry == setup.mcp_servers(url)[name])
    if not written:
        return config, False, ("a `trellum` server entry that setup did not write "
                               "(not the studio URL and key reference); left untouched")
    rest = {k: v for k, v in servers.items() if k != setup.MARKER}
    result = {k: v for k, v in config.items() if k != "mcpServers"}
    if rest:
        result["mcpServers"] = rest
    return result, True, ""


def _remove_json(path: Path, name: str, strip) -> Row:
    """Read, strip, write back -- or delete a file left with nothing in it."""
    if path.is_symlink() or path.parent.is_symlink():
        return Row(name, "kept", "symlink; left untouched")
    if not path.is_file():
        return Row(name, "absent")
    text = setup.read_exact(path)
    try:
        current = json.loads(text) if text is not None else None
    except json.JSONDecodeError:
        current = None
    if not isinstance(current, dict):
        return Row(name, "unreadable", "not a JSON object; left untouched")
    result, changed, note = strip(current)
    if not changed:
        return Row(name, "kept" if note else "nothing", note)
    if result:
        setup.write_exact(path, setup.dump_json(result))
        return Row(name, "removed", note)
    path.unlink()
    try:
        if name.startswith((".claude/", ".cursor/")):
            path.parent.rmdir()          # only if it is now empty; never recursive
    except OSError:
        pass
    return Row(name, "deleted")


def _hooks_strip(current: dict) -> tuple[dict, bool, str]:
    result, changed = strip_hooks(current)
    note = ("a hook that runs `-m trellum` but is not the shape setup writes; "
            "left untouched") if _mentions_trellum(result) else ""
    return result, changed, note


def remove(root: str | os.PathLike[str]) -> list[Row]:
    """Remove the integration from the project at *root*; one :class:`Row` per file."""
    root = Path(root)
    rows = [_remove_guidance(root / name, name) for name in ("CLAUDE.md", "AGENTS.md")]
    settings = Path(".claude") / "settings.json"
    rows.append(_remove_json(root / settings, settings.as_posix(), _hooks_strip))
    for name in setup.MCP_FILES:
        rows.append(_remove_json(root / name, name,
                                 lambda c, n=name: strip_server(c, n)))
    from trellum.scaffold.context_ownership import remove as remove_context

    rows.extend(Row(*row) for row in remove_context(root))
    return rows
