"""Scaffold project-level files for the BI report framework.

Usage:
    python -m trellum.init              # scaffold in the project root
    python -m trellum.init /some/path   # scaffold in a specific directory
"""

from __future__ import annotations

import argparse
import os

EVENTS_YAML_TEMPLATE = """\
# Centralized timeline of notable events (optional).
# Reports display these as chart annotations on LineChart and BarChart.
#
# Fields:
#   date       (required) - Event start date (YYYY-MM-DD)
#   end_date   (optional) - Event end date for ranges (creates shaded region)
#   label      (required) - Short display label
#   type       (required) - One of: campaign, ab_test, release, incident, event
#   studio     (optional) - Filter to a specific studio (defaults to shared)
#
# To disable annotations for a specific report, set `annotations: false`
# in that report's report.yaml.

events: []
  # - date: "2026-01-15"
  #   label: "v2.0 Release"
  #   type: release
  #   studio: shared
  #
  # - date: "2026-02-01"
  #   end_date: "2026-02-07"
  #   label: "Winter Campaign"
  #   type: campaign
  #   studio: my-studio
"""

ENV_EXAMPLE_TEMPLATE = """\
# BI Report Framework -- Environment Variables
#
# Connection details (host, port, database) are in data-sources/config.yaml.
# Only credentials and auth config go here.

# Local auth (remove in production)
AUTH_LOCAL_ROLE=admin

# Database credentials (prefix must match credentials.local in config.yaml).
# `python -m trellum datasource add` writes these for you, and checks they work.
# BI_PRIMARY_USER=your_username
# BI_PRIMARY_PASS=your_password
"""

DATASOURCE_CONFIG_TEMPLATE = """\
# data-sources/config.yaml
# Central registry of all data sources.
#
# You do not have to write this file by hand. This adds a source, puts its
# credentials in .env, and connects to check it actually works:
#
#   python -m trellum datasource add <name> --type postgres \\
#       --host <host> --port <port> --database <db> --user <user>
#
# Non-secret connection info (host, port, database) lives here.
# Credentials (user, password) live in .env locally or Secrets Manager in production.
# Reports reference sources by name in their report.yaml.

sources:
  # Example database source:
  # my_database:
  #   type: vertica
  #   description: "Production database"
  #   host: db-host.example.com
  #   port: 5433
  #   database: mydb
  #   credentials:
  #     local: BI_MY_DB              # reads BI_MY_DB_USER, BI_MY_DB_PASS from .env
  #     production: bi-reports/mydb  # opaque id for a registered resolver

  # Example file source:
  # my_excel:
  #   type: file
  #   description: "Reference data"
  #   path: data-sources/uploads/my-data.xlsx
  #   upload: true
"""


def _default_root() -> str:
    from trellum.project import get_project_root
    return get_project_root()


def _write_if_missing(path: str, content: str) -> bool:
    """Write *content* to *path* only if it doesn't already exist.

    Returns True if created, False if skipped.
    """
    if os.path.exists(path):
        return False
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return True


def init_project(target: str | None = None) -> None:
    root = target or _default_root()
    root = os.path.abspath(root)

    print(f"Initializing project in {root}\n")

    # Create directories
    for d in ["reports", "data-sources", "data-sources/uploads"]:
        dir_path = os.path.join(root, d)
        if not os.path.isdir(dir_path):
            os.makedirs(dir_path, exist_ok=True)
            print(f"  created  {d}/")
        else:
            print(f"  exists   {d}/")

    # Create template files
    files = [
        ("events.yaml", EVENTS_YAML_TEMPLATE),
        (".env.example", ENV_EXAMPLE_TEMPLATE),
        (os.path.join("data-sources", "config.yaml"), DATASOURCE_CONFIG_TEMPLATE),
    ]

    for name, content in files:
        path = os.path.join(root, name)
        if _write_if_missing(path, content):
            print(f"  created  {name}")
        else:
            print(f"  exists   {name}")

    # Agent pointers. Scaffolding is the only delivery path that needs no
    # separate setup step -- we are already writing into this project -- and the
    # result travels with the repository to every teammate and every agent that
    # clones it. Without it, an agent has no signal the framework can describe
    # itself, and falls back to reading its source.
    from trellum.agent import agentsetup

    for name, status in agentsetup.write_project_files(root):
        print(f"  {status:<8} {name}")

    print("\nDone. Next steps:")
    print("  1. Copy .env.example to .env and add your database credentials")
    print("  2. Configure data sources in data-sources/config.yaml")
    print("  3. Add events to events.yaml for chart annotations (optional)")
    print("  4. Create your first report: python -m trellum.new my-report")
    print("\nRun `trellum` to see what the framework offers.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Initialize project-level files for the BI report framework",
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="Target directory (defaults to project root)",
    )
    args = parser.parse_args()
    init_project(args.target)


if __name__ == "__main__":
    main()
