#!/usr/bin/env python
"""Django management entry point for the trellum control plane."""
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "trellum_portal.settings.dev")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Did you activate the virtualenv and "
            "install requirements-django.txt?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
