"""SQL placeholder scanning for safe parameter binding."""

from __future__ import annotations

import re


def _sql_param_spans(sql: str, dialect: str = "standard"):
    """Yield original executable placeholder spans for binding and validation."""
    # Walk only the original SQL: inserted values must never become SQL tokens.
    tokens = re.compile(r'''--|/\*|['"`\[]|\$(?:[A-Za-z_]\w*)?\$|(?<![:\w]):[A-Za-z_]\w*|#''')
    pos = 0
    while match := tokens.search(sql, pos):
        start, end = match.span()
        lexeme = match.group()
        if lexeme.startswith(":"):
            yield start, end, lexeme[1:]
            pos = end
            continue
        if lexeme == "--" or (lexeme == "#" and dialect == "backslash"):
            newline = re.search(r"[\r\n]", sql[end:])
            end = end + newline.start() if newline else len(sql)
        elif lexeme == "/*":
            depth = 1
            while depth and end < len(sql):
                boundary = re.search(r"/\*|\*/", sql[end:])
                if boundary is None:
                    end = len(sql)
                    break
                depth += 1 if boundary.group() == "/*" else -1
                end += boundary.end()
        elif lexeme.startswith("$"):
            closing = sql.find(lexeme, end)
            end = closing + len(lexeme) if closing >= 0 else len(sql)
        elif lexeme not in ("#", "[") or (lexeme == "[" and dialect == "bracket"):
            closing = "]" if lexeme == "[" else lexeme
            if dialect == "googlesql" and sql.startswith(lexeme * 3, start):
                closing = lexeme * 3
                end = start + 3
            # PostgreSQL E'...' also uses backslash escapes.
            escapes = dialect in ("backslash", "googlesql") or (
                lexeme == "'" and start > 0 and sql[start - 1] in "eE"
                and (start == 1 or not (sql[start - 2].isalnum() or sql[start - 2] == "_"))
            )
            while end < len(sql):
                if escapes and sql[end] == "\\":
                    end += 2
                elif sql.startswith(closing, end):
                    end += len(closing)
                    if len(closing) == 1 and sql.startswith(closing, end):
                        end += 1
                    else:
                        break
                else:
                    end += 1
        pos = end
