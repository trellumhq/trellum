"""Shared size limits for bounded reads (see docs/performance-patterns.md).

Below these, lists and pickers render every row with instant client-side
search; above them they page or search on the server.
"""

#: Rows a list, picker or catalog shows inline before it pages or searches on the server.
INLINE_LIMIT = 500
