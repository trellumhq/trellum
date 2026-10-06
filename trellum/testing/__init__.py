"""Test harness for BI reports: mock data generation, structural checks, visual regression."""

from trellum.testing.mock_data import generate_mock_df
from trellum.testing.runner import test_all_reports, test_report
from trellum.testing.visual_regression import compare_screenshot, list_baselines, save_baseline

__all__ = [
    "generate_mock_df",
    "test_report",
    "test_all_reports",
    "save_baseline",
    "compare_screenshot",
    "list_baselines",
]
