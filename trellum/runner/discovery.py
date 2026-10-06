"""Finding what a project supplies: reports, themes, components."""

from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from pathlib import Path

import yaml

from trellum.project import get_project_root
from trellum.report import BaseReport


def discover_report(report_dir: str) -> type[BaseReport]:
    """Find and import the BaseReport subclass from a report directory.

    Imports the report as a proper package so relative imports (e.g.
    ``from . import queries``) work correctly.
    """
    report_dir = os.path.abspath(report_dir)
    gen_path = os.path.join(report_dir, "generator.py")
    if not os.path.exists(gen_path):
        raise FileNotFoundError(f"No generator.py found in {report_dir}")

    # Determine package name from directory structure: reports/{slug} -> reports.{slug_underscored}
    slug = os.path.basename(report_dir).replace("-", "_")
    parent_dir = os.path.dirname(report_dir)
    parent_name = os.path.basename(parent_dir)
    pkg_name = f"{parent_name}.{slug}"
    module_name = f"{pkg_name}.generator"

    project_root = get_project_root()
    if project_root not in sys.path:
        sys.path.insert(0, project_root)

    # Import the package __init__.py first (creates the package in sys.modules)
    init_path = os.path.join(report_dir, "__init__.py")
    if os.path.exists(init_path):
        if parent_name not in sys.modules:
            parent_init = os.path.join(parent_dir, "__init__.py")
            if not os.path.exists(parent_init):
                # Create a virtual parent package
                import types
                parent_pkg = types.ModuleType(parent_name)
                parent_pkg.__path__ = [parent_dir]
                parent_pkg.__package__ = parent_name
                sys.modules[parent_name] = parent_pkg

        pkg_spec = importlib.util.spec_from_file_location(
            pkg_name, init_path,
            submodule_search_locations=[report_dir],
        )
        pkg_module = importlib.util.module_from_spec(pkg_spec)
        pkg_module.__package__ = pkg_name
        sys.modules[pkg_name] = pkg_module
        pkg_spec.loader.exec_module(pkg_module)

    # Import generator.py as a submodule of the package
    spec = importlib.util.spec_from_file_location(module_name, gen_path)
    module = importlib.util.module_from_spec(spec)
    module.__package__ = pkg_name
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if (isinstance(attr, type)
                and issubclass(attr, BaseReport)
                and attr is not BaseReport):
            return attr

    raise RuntimeError(f"No BaseReport subclass found in {gen_path}")

def _discover_project_themes(project_root: str) -> None:
    """Auto-discover ``Theme`` instances in a project-level ``themes/`` dir."""
    themes_dir = os.path.join(project_root, "themes")
    if not os.path.isdir(themes_dir):
        return
    from trellum.themes import register_theme
    from trellum.themes.theme import Theme
    for py_file in Path(themes_dir).glob("*.py"):
        if py_file.name.startswith("_"):
            continue
        mod_name = f"themes.{py_file.stem}"
        if mod_name in sys.modules:
            mod = sys.modules[mod_name]
        else:
            spec = importlib.util.spec_from_file_location(mod_name, py_file)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)
        for attr_name in dir(mod):
            obj = getattr(mod, attr_name)
            if isinstance(obj, Theme):
                theme_name = py_file.stem.replace("_", "-")
                register_theme(theme_name, obj)

def _discover_project_components(project_root: str) -> None:
    """Auto-discover ``Component`` subclasses in a project-level ``components/`` dir."""
    comp_dir = os.path.join(project_root, "components")
    if not os.path.isdir(comp_dir):
        return
    for py_file in Path(comp_dir).glob("*.py"):
        if py_file.name.startswith("_"):
            continue
        mod_name = f"components.{py_file.stem}"
        if mod_name not in sys.modules:
            spec = importlib.util.spec_from_file_location(mod_name, py_file)
            if spec is None or spec.loader is None:
                continue
            mod = importlib.util.module_from_spec(spec)
            sys.modules[mod_name] = mod
            spec.loader.exec_module(mod)

def scan_report_configs(
    reports_dir: str | None = None,
    studio: str | None = None,
    category: str | None = None,
    include_hidden: bool = False,
) -> list[dict]:
    """Scan reports/*/report.yaml and return matching report entries.

    Each entry contains ``dir`` (absolute path), parsed ``config``, and ``slug``.
    Used by both the runner and a host's server.
    """
    if reports_dir is None:
        reports_dir = os.path.join(get_project_root(), "reports")

    if not os.path.isdir(reports_dir):
        return []

    found: list[dict] = []
    for entry in sorted(os.listdir(reports_dir)):
        if entry.startswith("_"):
            continue
        yaml_path = os.path.join(reports_dir, entry, "report.yaml")
        gen_path = os.path.join(reports_dir, entry, "generator.py")
        if not os.path.isfile(yaml_path):
            continue
        if not include_hidden and not os.path.isfile(gen_path):
            continue

        # UTF-8 explicitly: without it Windows falls back to the ANSI code
        # page and a report name like "Insert Coin · Minigame" reaches the
        # gallery as mojibake.
        with open(yaml_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)

        if config.get("disabled"):
            continue
        if not include_hidden and config.get("hidden"):
            continue

        if studio and config.get("studio", "").lower() != studio.lower():
            continue
        if category and config.get("category", "").lower() != category.lower():
            continue

        found.append({
            "dir": os.path.join(reports_dir, entry),
            "config": config,
            "slug": config.get("slug", entry),
        })

    return found

def _discover_all_reports(
    studio: str | None = None,
    category: str | None = None,
) -> list[dict]:
    """Backward-compatible wrapper around :func:`scan_report_configs`."""
    return scan_report_configs(studio=studio, category=category)
