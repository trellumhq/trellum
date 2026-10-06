"""Terminal output that survives a default Windows console."""

from __future__ import annotations


def force_utf8_output() -> None:
    """Make this process's output survive a non-UTF-8 console.

    `runner.py` has done this since long before this CLI existed, for exactly
    the same reason: a Windows console defaults to a legacy codepage, and one
    character outside it raises UnicodeEncodeError and kills the command. The
    documents this CLI prints are full of them -- checkmarks, em-dashes, arrows.

    A measured run hit that on its second command, `guide report`, and then
    prefixed `PYTHONIOENCODING=utf-8` to every Python invocation for the rest of
    the session. Making our output printable is not the caller's job.

    Delegates rather than reimplementing, so there is one such fix to maintain.
    """
    from trellum.runner import _force_utf8_console

    _force_utf8_console()
