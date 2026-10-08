"""Host-only build artifacts, shared by publishers and HTTP readers."""
from __future__ import annotations

import re
from urllib.parse import unquote


def is_private_artifact(path: str) -> bool:
    """Reject the SQL manifest and URL/Windows filename aliases of it.

    Malformed URL escapes also fail closed at the serving boundary.
    """
    path = path.split("?", 1)[0].split("#", 1)[0]
    while "%" in path:
        if re.search(r"%(?![0-9a-fA-F]{2})", path):
            return True
        try:
            decoded = unquote(path, errors="strict")
        except UnicodeDecodeError:
            return True
        if decoded == path:
            break
        path = decoded
    for name in path.replace("\\", "/").split("/"):
        # NTFS stream suffixes such as ::$DATA can address the same file.
        name = name.split(":", 1)[0].rstrip(" .").casefold()
        if name.endswith(".gz"):
            name = name[:-3].rstrip(" .")
        if name == "_live_queries.json":
            return True
    return False
