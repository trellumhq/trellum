"""SQL for the itest-smoke report.

Both tables are created and seeded by itest/test_report_build.py before the
report runs (they are not part of test_contract.py's shared "contract"
table -- this report proves the report-build pipeline, not the driver
contract, so it uses its own minimal schema instead of piggybacking on
the other one).
"""

# :param binding proves the same `bind_params` path test_contract.py
# exercises directly against the driver, this time reached through the
# whole report.yaml -> query_df pipeline.
PG_ROWS = """
SELECT id, label, amount
FROM itest_smoke_pg
WHERE label = :who
ORDER BY id
"""

CH_ROWS = """
SELECT id, label, amount
FROM itest_smoke_ch
ORDER BY id
"""
