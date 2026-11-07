"""Deterministic additive leaf facts for the optional hierarchy table."""

import pandas as pd

from trellum.components import DataSource, FilterBar, HierarchyTable, Panel, PivotTable


def spend_facts(*, incomplete=False):
    amounts = [("Paid search", "iOS", 3200, 4000), ("Paid search", "Android", 2000, 2800),
               ("Paid social", "iOS", 1200, 1600), ("Paid social", "Android", 800, 1000),
               ("Partners", "iOS", 0, 250), ("Partners", "Android", 400, 600)]
    records = []
    for source, platform, sep, oct_ in amounts:
        for month, spend in [("Sep", sep), ("Oct", oct_)]:
            records.append({"Fact": f"spend-{len(records)+1:02d}", "Source": source,
                            "Platform": platform, "Month": month,
                            "Spend": None if incomplete and (source, platform, month) == ("Paid social", "Android", "Oct") else spend})
    return pd.DataFrame(records)


def add_sections(ctx):
    for identity, title, incomplete, known, expanded in [
        ("hierarchy_collapsed", "Hierarchy — complete, collapsed", False, False, None),
        ("hierarchy_expanded", "Hierarchy — complete, expanded", False, False,
         [["Paid search"], ["Paid social"], ["Partners"]]),
        ("hierarchy_withheld", "Hierarchy — unavailable input, withheld totals", True, False, [["Paid social"], ["Partners"]]),
        ("hierarchy_known", "Hierarchy — explicit known subtotals", True, True, [["Paid social"], ["Partners"]]),
    ]:
        facts = spend_facts(incomplete=incomplete)
        ctx.add_section(title, [
            DataSource(identity, facts),
            FilterBar(identity, facts, filters=[{"column": "Platform", "type": "toggle", "label": "Hierarchy platform", "options": ["iOS", "Android", "Web"]}]),
            HierarchyTable(facts, title, rows=["Source", "Platform"], value="Spend", record_id="Fact",
                           column="Month", value_format="currency", dataset_id=identity,
                           known_subtotals=known, expanded_paths=expanded),
            *([Panel([PivotTable(facts, rows=["Source", "Platform"], cols="Month", values="Spend",
                          title="Flat spend table", value_format="currency", dataset_id=identity)],
                     title="Optional flat PivotTable comparison")]
              if identity == "hierarchy_collapsed" else []),
        ])
