# Framework Migration Guide

Each section documents a breaking or significant change, how to detect affected
code, and the exact replacement.

## 0.9.0: pandas 3, and repeated text from `query_df` as `category` columns

pandas 3.0 is now required, and `query_df` returns a text column whose values
repeat (at most half distinct) as a pandas `category` column instead of plain
text. Values, row order and all other columns are unchanged; the column's
dtype is what changed, and a few pandas operations behave differently on it:
`.map()` followed by arithmetic, `pd.to_datetime()` followed by `<`/`>`,
`.min()`/`.max()`, `fillna()` or assignment with a new value, and `+` with a
string. Each raises a `TypeError` naming `Categorical` or `category`, and a
build that hits one prints a `[HINT]` line pointing at the guide.

**Detecting affected code:** in each report's `generator.py`, the lines that
work on a text column from `query_df`:

```
grep -nE '\.map\(|to_datetime\(|\.min\(\)|\.max\(\)|fillna\(|\+ *["'"'"']|\.unique\(\)|value_counts\(|== *object|\]\[' reports/*/generator.py
```

**The fix:** the before/after for each operation, the pandas 3 changes that
reach report code (chained assignment, `dtype == object`), and how to check
that a report's output is unchanged are in `python -m trellum guide upgrade`
(the `upgrade` section of `AGENTS.md`). `query_df(..., compact_text=False)`
returns plain text where a fix is not worth it.

---

## 0.2.0: `_meta.json` `metrics_used` entries became objects

`META_SCHEMA_VERSION` moved 1 → 2. A build's claimed metrics still live at
`metrics_used` in `_meta.json`, but each entry is now an object instead of a
bare id string, so a host can tell a claim's build-time definition apart from
the current one in `metrics.yaml` without opening `data.json`:

```jsonc
// before (schema v1)
"metrics_used": ["gross_revenue", "paying_share"]

// after (schema v2)
"metrics_used": [
  {"id": "gross_revenue", "definition_hash": "3f9a0c1e2b7d", "version": 2},
  {"id": "paying_share",  "definition_hash": "a11ce0ffee42", "version": 1}
]
```

**Detecting affected code:** search for `metrics_used` outside this repo. Any
reader that assumed a bare id list (e.g. iterating entries as strings, or
comparing an entry directly against a metric id) needs to read `entry["id"]`
instead.

**The fix:** call `trellum.meta.normalize_metrics_used(meta.get("metrics_used"))`
rather than reading the key directly. It accepts both shapes — a v1 build's
bare ids normalize to `{"id": ..., "definition_hash": None, "version": None}`
("unknown", since a v1 artifact never recorded either — nothing else to read
them from) — so one call reads a claim list correctly regardless of which
schema version produced it. This is also how `python -m trellum metrics`
itself reads old builds now.

`data.json`'s `_metrics` block (label/format/agg spec/version/definition_hash/
component_ids per claimed metric) is unchanged.

---

**Before 0.2.0 there was nothing to migrate.** 0.1.0 was the first release, so
no report written against it needed editing.

The next entry lands here when a Tier 1 surface changes — see the deprecation
process in [COMPATIBILITY.md](COMPATIBILITY.md). An entry names the version that
changed it, gives a search pattern for finding affected code, and shows the
before and after.
