# Framework Migration Guide

Each section documents a breaking or significant change, how to detect affected
code, and the exact replacement.

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
