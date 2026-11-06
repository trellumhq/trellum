"""Create editable investigation guidance without replacing project knowledge."""
from pathlib import Path

FILES = {
    "context/README.md": """# Project knowledge

Read these notes before exploring a source or defining a metric. Update them
when a finding is verified so the next investigation can reuse it. These files
are project conventions; Trellum does not automatically load them into every
portal assistant conversation.

- `warehouse.md`: source names, table grain, keys, safe joins and known gaps.
- `metrics.md`: business meaning and links to canonical `metrics.yaml` entries.
- `../analysis/README.md`: exploration, reproducible queries and local exports.

Record evidence and its date. Separate observed facts, owner-approved business
rules and open questions. Recheck stale observations against the source. Keep
credentials and raw customer records out of these versioned notes.

For each finding, record:

- Question and conclusion (or what remains unknown).
- Source name, tables/columns and grain; query/script path and parameters.
- Data coverage, timezone, filters and exclusions; execution date.
- Evidence location, limitations and who reviewed the interpretation.

A sample is evidence about that sample, not proof of a complete population.
""",
    "context/warehouse.md": """# Source knowledge

No source facts have been verified yet. Replace this sentence as you explore.
Use configured source names from `data-sources/config.yaml`; never store secrets.

For each source, record its purpose, owner, available dates and refresh schedule.
For each relevant table, record:

- Grain: what one row represents, including the time unit and timezone.
- Keys: uniqueness and nullability, supported by a dated query or source docs.
- Columns: meaning, units, nullable values and surprising encodings.
- Joins: keys, expected cardinality and a check for row multiplication.
- Gaps: missing periods, late data, duplicates and deprecated fields.

Illustrative example only (not a table in your project): a daily activity table
with one row per day and account must be joined to an account table unique on
account ID. Verify that uniqueness before joining. Summing daily active counts
does not establish the number of distinct active accounts over a month.

Keep the exact inspection query in `analysis/` and link its dated finding here.
""",
    "context/metrics.md": """# Business definitions

No business definitions have been approved yet. Check `python -m trellum metrics`
before deriving a number; reuse existing definitions rather than inventing a
second formula. `metrics.yaml` is the executable registry; link its IDs here
instead of maintaining competing SQL in these notes.

For each definition, record the question it answers, metric ID (if registered),
owner, approval status/date, grain, timezone, units, inclusions/exclusions,
source fields, numerator/denominator and aggregation rules. Link evidence and
record unresolved interpretation questions explicitly.

Illustrative example only: an average purchase amount might be total eligible
purchase amount divided by eligible purchase count. Decide with the owner how
refunds, taxes, currencies and zero-purchase periods are handled. Across groups,
recompute from total numerator and denominator; do not average group averages.
Do not install this example as an approved metric without that verification.
""",
    "analysis/README.md": """# Explore, retain, reuse

Run commands from the project root using the interpreter that has Trellum
installed (see `AGENTS.md`). Start with `context/` and existing metric definitions:

```sh
python -m trellum metrics
python -m trellum data
python -m trellum guide answer
```

`python -m trellum data <name>` selects one source. The current data command
inspects local SQLite and file sources; for remote warehouses, verify columns
using the source documentation or an appropriate read-only catalog query.
Discover real columns before writing SQL. Use a read-only source account,
select only relevant columns and constrain dates and rows in the query. The
query command's display row limit does not limit database work or CSV exports.

Use the configured source by name. This connection probe uses no table names:

```sh
python -m trellum query "SELECT 1 AS connection_ok" --source <name>
```

Save a useful investigation as `analysis/<question>.py` or `.sql`, with a sibling
Markdown note describing the question, source, parameters, data dates/timezone,
execution date, result, limitations and evidence path. SQL files are records;
the query CLI takes SQL text. Bind values with `--param name=value` rather than
interpolating user input. See `python -m trellum guide answer` for examples.

To retain a local result, use the existing CSV export with a bounded query:

```sh
python -m trellum query "SELECT 1 AS connection_ok" --source <name> --csv > analysis/results/connection-check.csv
```

Check the exit status before relying on the file; shell redirection can leave
an empty file after failure. Use a fresh dated filename for real investigations
so earlier evidence is not silently overwritten. Record the query and provenance
in the sibling note. `analysis/results/` is ignored by Git by default; review
exports before sharing, and remove them when their retention period ends. Git
ignore is not access control and does not untrack files already committed.

Keep reusable queries and sanitized findings in Git. Promote verified source
knowledge to `context/warehouse.md` and agreed metric meaning to
`context/metrics.md` / `metrics.yaml`. Keep provisional findings labelled as such.
Publish a reviewed finding with `python -m trellum analysis new <slug>` and
`python -m trellum guide analysis`; create a recurring report only when needed.
The `analysis/` directory itself is not discovered or scheduled as a report.
""",
    "analysis/results/.gitignore": "*\n!.gitignore\n",
}


def write_context_files(root: Path) -> list[tuple[str, str]]:
    """Seed missing files only, including when setup runs in an existing repo."""
    from trellum.scaffold.context_ownership import seed

    return seed(root)
