"""Ensure the benchmark report records metrics without browser diagnostics."""

import unittest
from types import SimpleNamespace

from benchmark_metrics import measurements, validate_scaling


class MetricsTests(unittest.TestCase):
    def test_numeric_metrics_only(self):
        metric = SimpleNamespace(name="page_load", duration_ms=123.4,
                                 extra={"heap_mb": 12.5, "note": "private", "trace": ["private"]})
        report = SimpleNamespace(slug="sample", rows=1000, metrics=[metric], errors=["private"])
        self.assertEqual(measurements([report]), [
            {"report": "sample", "rows": 1000,
             "metrics": [{"name": "page_load", "duration_ms": 123.4, "extra": {"heap_mb": 12.5}}]},
        ])

    def test_incomplete_scaling_and_missing_browser_measurements_fail(self):
        metric = SimpleNamespace(name="page_load", duration_ms=123.4, extra={})
        complete = [SimpleNamespace(slug="sample", rows=rows, metrics=[metric]) for rows in (1000, 10000)]
        validate_scaling(complete, "sample", (1000, 10000))
        for results in ([], complete[:1], list(reversed(complete)),
                        [SimpleNamespace(slug="sample", rows=1000, metrics=[])],
                        [SimpleNamespace(slug="wrong", rows=1000, metrics=[metric])]):
            with self.assertRaises(AssertionError):
                validate_scaling(results, "sample", (1000, 10000))


if __name__ == "__main__":
    unittest.main()
