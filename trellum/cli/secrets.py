"""CLI secret input without requiring secrets in argv."""
import getpass
import os
import sys


def add_secret_arguments(parser, name, help_text):
    group = parser.add_mutually_exclusive_group()
    group.add_argument(f"--{name}", help=help_text)
    group.add_argument(f"--{name}-stdin", action="store_true", help="read one secret line from stdin")
    group.add_argument(f"--{name}-env", metavar="NAME", help="read the secret from environment variable NAME")


def resolve_secret(args, name, prompt, *, interactive=True):
    value = getattr(args, name, None)
    if value is not None:
        return value
    if getattr(args, f"{name}_stdin", False):
        line = sys.stdin.readline()
        if not line:
            raise ValueError("Secret input ended before a value was supplied.")
        return line.removesuffix("\n").removesuffix("\r")
    variable = getattr(args, f"{name}_env", None)
    if variable:
        if variable not in os.environ:
            raise ValueError("Secret environment variable is not set.")
        return os.environ[variable]
    if interactive and sys.stdin.isatty():
        # getpass can fall back to echoed input; refuse that fallback.
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            try:
                return getpass.getpass(prompt)
            except (EOFError, getpass.GetPassWarning):
                raise ValueError("A hidden terminal prompt is unavailable; use stdin or environment input.") from None
    return None
