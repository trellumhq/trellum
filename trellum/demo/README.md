# Demo project

A self-contained fake consumer project: generated data, working dashboards, no
database and no credentials. Use it to develop and verify framework
changes locally.

Everything here is invented. The schema models a fictional mobile-games
publisher ("Nova Play") because that shape exercises the framework's filtering,
cohort, and monetisation features well.

## Quick start

```powershell
cd demo
.\dev.ps1 setup                    # creates the virtualenv, installs deps, builds fixtures
.\dev.ps1 run player-overview      # builds and serves on http://localhost:8050
```

`dev.ps1` uses the repo's `.venv` interpreter directly and creates it on first
use, so there is **nothing to activate** and `python` never needs to be on
your PATH.

## Commands

| Command | What it does |
|---|---|
| `.\dev.ps1 setup` | Create the virtualenv, install dependencies, generate fixtures |
| `.\dev.ps1 all` | **Run every report** against the fixtures (aliases: `build`, `run-all`, `run all`) |
| `.\dev.ps1 serve [port]` | Run every report **and serve them all on one port** with an index (`:8050`) |
| `.\dev.ps1 run <slug>` | Run one report and serve just that one |
| `.\dev.ps1 list` | List reports with their last run status |
| `.\dev.ps1 test` | Run every report against mock data — no database needed |
| `.\dev.ps1 fixtures [args]` | Regenerate the demo database |
| `.\dev.ps1 new <slug>` | Scaffold a new report from `_template` |
| `.\dev.ps1 clean` | Remove generated output and the query cache |
| `.\dev.ps1 baseline` | Capture visual baselines |
| `.\dev.ps1 check` | Compare rendered output against the baselines |
| `.\dev.ps1 ci` | build → validate → visual check, in one command |

## Viewing them all

Reports can't each own a port, and they don't need to — one server hosts the
whole output directory with a generated index:

```powershell
.\dev.ps1 serve       # builds everything, serves it at http://localhost:8050
```

`python -m trellum.run --all --serve` builds the index from each report's
`_meta.json`, so browsing a whole output directory needs nothing but this
repository. That is the point of this project: it is the fixture that proves
the framework stands on its own.

`.\dev.ps1 all` builds without serving (the batch/CI path) and prints the links
at the end, marking them live if a server is already up.

`all` passes extra arguments through to the framework:

```powershell
.\dev.ps1 all --max-concurrent 3
.\dev.ps1 all --category Revenue
.\dev.ps1 all --debug
```

Extra arguments pass straight through to the framework:

```powershell
.\dev.ps1 run player-overview --no-serve
.\dev.ps1 run monetization --port 8060 --debug
.\dev.ps1 fixtures --small                        # 60 days, fast
.\dev.ps1 fixtures --anchor 2026-06-30 --seed 42  # pinned, reproducible
```

Missing fixtures are generated automatically the first time you need them.

## Two businesses, nine reports -- and one generated

The demo is organised the way the product is meant to be used: **per business,
around the questions that business asks** — not as a catalogue of chart types.
Few reports, each one big, because a feature reads better as a section of a
real page than as a page of its own — nine cards is what a visitor will
actually browse, and every one of them answers a different question. The
tenth, `metrics`, is not written at all: `python -m trellum metrics --report`
generates it from `metrics.yaml`, one KPI and trend per bound metric. Two
fictional companies share the warehouse, each with its own `studio` and its
own annotated events. Signature charts state what they demonstrate in a
`· demo:` suffix — this is a demo, and the feature is the content.

### Nova Play — a mobile-games publisher

| Report | Its question |
|---|---|
| `player-overview` | The flagship. Health, geography (choropleth), behaviour overlap (Venn) and cohort retention — four questions, three data grains, one page |
| `conversion` | Where do we lose buyers? Per-title scopes with funnels, plus a Sankey that follows the title switcher |
| `monetization` | Who pays, for what? Two data sources, tabs, pivot, treemap, spend-vs-installs — and **The Journey**: acquisition-to-purchase as a living metro map where every light is one player-session walking the measured `fact_journey` rates, colored by spender tier |
| `experiments` | The studio's whole A/B program on one page, three tests with three different reads: checkout_v2 (CUPED, winsorisation, bootstrap CIs — running), starter_pack_price (a conversion lift that ARPU doesn't confirm — past its declared end), onboarding_v2 (read by Day-1 Return rather than by revenue — concluded). Its `ab_test:` **list** gives each test its own row and lifecycle on the portal's Experiments overview |
| `economy-firehose` | **A lot of data.** Every currency source and sink at full grain — **2,013,984 rows** on the published gallery, all of them live in the browser at once: usable immediately on the three inline months while the other 49 stream in behind you, and every filter re-measured over the whole two million in ~60ms (the page says so itself, timed in your own browser). Sized deliberately: see `ECONOMY_HISTORY_MULT` for the browser-heap ceiling that picks this number. A real economy bug is hidden inside. A project install generates a slice of the same thing (`python -m trellum.demo --full` for the whole set) |
| `insert-coin` | **The report that is a minigame** — drawn as a chart, because it is one. At rest: a line chart of the filtered window (DAU terrain, revenue orbs sized by value, outage days as gaps in the line, real event annotations). Press Start and you ride it as a comet; nothing moves until you do. Filters rebuild the world; one selected tier changes the comet you play as |
| `user-event-log` | **The live-lookup demo.** Type a user id, read that user's raw event stream from `fact_user_events`. The artifact bakes one user's log at build time; served by a host advertising a live-query endpoint, the same input queries the warehouse on demand — the log itself is never compiled in |

### Northwind Threads — an apparel shop

| Report | Its question |
|---|---|
| `store-health` | The Monday-morning page: revenue, margin, mix, and the restock decision ranked by *kept* margin — the sale that gave its lift away and the returns lag trap are both in here |
| `cart-funnel` | Where does the money leak, and for whom? The animated leak (pure canvas), the funnel, per-channel conversion — and **The Fix We Tried**: the blue-vs-green checkout button test the leak provoked, the smallest test in the warehouse, not guaranteed to win |

## The metrics catalog

`metrics.yaml` names the business numbers that must mean the same thing
everywhere: 12 defined, 11 claimed. Adoption is broad rather than token —
every report whose data can genuinely compute a definition claims it, so most
metrics show 2-4 claiming reports, not one:

| Metric | Claimed by |
|---|---|
| `gross_revenue`, `iap_revenue`, `ad_revenue` | `monetization`, `player-overview`, `insert-coin` |
| `transactions` | `monetization`, `player-overview`, `experiments` (checkout and pricing sections) |
| `payer_share`, `arpdau` | `player-overview`, `insert-coin` |
| `avg_transaction_value` | `player-overview`, `experiments` (checkout and pricing sections) |
| `ad_share` (**v2** — see below) | `player-overview`, `insert-coin` |
| `retention_d1`, `retention_d7` | `player-overview` only — no other report reads `fact_retention` |
| `refund_rate` | `store-health` only — no other report reads `shop_orders` |
| `sessions_per_dau` | nobody — the catalog's "no reports claim this yet" state is worth keeping too |

`cart-funnel`, `conversion`, `economy-firehose` and `user-event-log` claim
nothing: none of their data honestly computes one of these definitions (a
stretched claim — right column name, wrong business or wrong grain — is
worse than an inline KPI dict, so they keep theirs). The `experiments`
page's onboarding section is the pointed example: its `transactions` column
is real, but it counts Day-1 sessions, not IAP purchases, so that section
never claims the `transactions` metric even though the sections beside it
do.

```powershell
python -m trellum metrics          # list every definition + who claims it
python -m trellum metrics --lint   # duplicates, orphans, schema problems
python -m trellum metrics ad_share # one definition's full spec + claimants
```

### Showing the stale badge

The catalog's amber "stale — built against vN" badge compares a claim's
build-time `definition_hash` (baked into that report's `_meta.json` the
moment it was built) against `metrics.yaml`'s *current* hash for that same
metric. The two only disagree when a definition changed after a claiming
report's last build — which is exactly what `ad_share` is set up to
demonstrate: it is `version: 2` in this `metrics.yaml`, redefined from
`ad_revenue / total_revenue` (v1) to `ad_revenue / iap_revenue` (v2), and
claimed by two reports (`player-overview`, `insert-coin`).

To see green "current" and amber "stale" side by side on the same metric row:

1. **Build everything at v2** (what this repo already ships):
   ```powershell
   python -m trellum.run --all --no-serve
   ```
   Every claimant's `_meta.json` now carries `ad_share`'s v2 hash — both
   claims read as current.
2. **Roll `ad_share` back to v1** in `metrics.yaml` (drop `version: 2`,
   restore `denominator: total_revenue` and the v1 `sql`) — this simulates
   the moment *before* the real change, or equivalently, simulates someone
   editing the definition again after both reports last built.
3. **Sync the portal's studio scan** (`sync_studio_registry`/
   `sync_studio_metrics` — whatever the studio's repo-sync path is wired to;
   in a running portal this is the same pass that re-reads `report.yaml`) so
   `MetricDefinition.definition_hash` for `ad_share` picks up the change.
4. **Rebuild only one claimant**, e.g.:
   ```powershell
   python -m trellum.run reports\player-overview --no-serve
   ```
   Do **not** rebuild `insert-coin`.
5. Open the studio's Metrics tab and find `ad_share`: `player-overview`'s
   usage row reads current (its `_meta.json` now matches the definition the
   portal just synced), `insert-coin`'s reads stale (its `_meta.json` still
   carries the hash from step 1) — green and amber, same metric, side by
   side. Rebuilding `insert-coin` too flips it back to green.

Step 2 is only needed to re-demonstrate the transition by hand; the shipped
state (both claimants built against the v2 `metrics.yaml` in this repo) is
already the "everything current" starting point the walkthrough begins from.

### For contributors: coverage notes

The reports double as the framework's worked examples, so between them they
exercise every placeable component, all four filter types, `ScopedDataSource`,
multi-scope, chunked DataSources, two data sources (sqlite + CSV), `ABCompare`,
and six custom sections (read in this order: `conversion`'s sankey is
`RawHTML` + `extra_cdn` and follows the scope switcher, `player-overview`'s
choropleth adds a runtime-fetched vendor asset and its venn adds filter
reactivity via `fw.filterEngine`, `cart-funnel`'s animated leak shows the
no-vendor-library case — pure canvas, filter-live, theme-aware,
reduced-motion aware — `monetization`'s journey map walks agents through
measured Markov rates from a warehouse table, and `insert-coin` is the
ceiling: a playable, Start-gated game drawn in the report's own chart
language, on the same rules). If you add a
component, one demo report should use it — the scaffold's examples listing and
`guide components` both lean on these being real.

`_template/` is a copy-paste starter, skipped by `--all` because of its
underscore prefix.

## Alert scenarios

`alert-scenarios.yaml` labels the anomalies this warehouse bakes in as alert rules with a known answer (outage, launch, promo, a quiet week, …); the portal's `manage.py alert_scenarios [--only <id>]` builds each report at the scenario's `before` and `after` clocks and scores the org's real model against the labels.
It calls a paid model and its answers are a judgment, so it is not CI; `--dry` runs the loop with a canned decision.

## Regression pipeline

```powershell
.\dev.ps1 ci          # build against fixtures -> validate on mock data -> visual diff
```

Or the steps individually:

```powershell
.\dev.ps1 baseline    # capture reference screenshots
# ... make a change ...
.\dev.ps1 check       # fails, with a diff image, if anything moved
```

Two things make this reliable, and both matter if you extend it:

**The clock is pinned.** Report dates reach chart axes, titles and filter
defaults, so `baseline` and `check` set `FW_NOW=2026-06-30`. Without that a
baseline captured today fails tomorrow for no real reason. `FW_NOW` accepts a
date or a full ISO timestamp and is honoured by `ctx.today` and the mock data
generator.

**Animations are frozen during capture.** The screenshot harness disables CSS
transitions and Chart.js animation before capturing, because resizing the
viewport re-renders every chart and a screenshot taken mid-animation differs
run to run.

The threshold is 0.005% of pixels, far tighter than the framework default of
5%. On a full-page screenshot even replacing an entire chart title only moves
0.08% of pixels, so the default would catch almost nothing. Measured here:

| Change | Pixels moved |
|---|---|
| Identical re-run | 0.00% |
| One word in a chart title | 0.01% |
| Whole chart title replaced | 0.08% |

Baselines live in `../testing/baselines/` and are **gitignored** — they are
large, churn on any cosmetic change, and are only comparable on the machine
that made them. Regenerate before a refactor, not after.

Visual comparison needs Pillow and Playwright:

```powershell
python -m pip install Pillow playwright
python -m playwright install chromium
```

## Doing it by hand

`dev.ps1` is a convenience wrapper, not a requirement. To work in an activated
shell instead:

```powershell
cd ..                                        # repo root
py -m venv .venv                             # python3 -m venv .venv on macOS/Linux
.\.venv\Scripts\Activate.ps1                 # source .venv/bin/activate
python -m pip install -r requirements.txt
cd demo
python tools\make_fixtures.py
python -m trellum.run reports\player-overview
```

On Windows, use the `py` launcher to bootstrap: plain `python` is usually the
Microsoft Store alias, which prints *"Python was not found"*.

Any command also works by calling the virtualenv interpreter directly:

```powershell
..\.venv\Scripts\python.exe tools\make_fixtures.py
```

## Troubleshooting

| Symptom | Cause |
|---|---|
| `Python was not found` | `python` is the Microsoft Store alias. Use `.\dev.ps1`, or bootstrap with `py`. |
| `ModuleNotFoundError: No module named 'pandas'` | You are on the system Python. Dependencies live only in `.venv`. |
| `No module named 'framework'` | You are not in `demo/` — the import shim is found via the working directory. |
| `SQLite data source 'demo_db' not found` | Fixtures not generated yet: `.\dev.ps1 fixtures`. |
| `Activate.ps1 cannot be loaded` | Execution policy: `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`. |
| Port already in use | The runner reclaims its own previous server; otherwise pass `--port N`. |

## How `import trellum` works here

Consumers mount this repo as a submodule named `trellum/`, so `import trellum`
resolves naturally there. Run from this directory and it would not: `demo/` is
what lands on `sys.path`, not the repo root. `demo/trellum/__init__.py` is a
small shim that repoints its search path at the repo root, so
`python -m trellum.run` works from this directory exactly as it would in a real
consumer project — including inside subprocesses, which is how any control
plane runs a report. Read the docstring in that file for why it is a shim
rather than a symlink or an editable install.

## Layout

```
demo/
├─ trellum/          import shim (dev-only)
├─ config.yaml         project config (title, subtitle, extensions)
├─ events.yaml         chart annotations  (generated)
├─ data-sources/
│  ├─ config.yaml      demo_db (sqlite) + ua_budget (csv)
│  ├─ demo.sqlite      generated, gitignored
│  └─ uploads/ua_budget.csv
├─ reports/
│  ├─ player-overview/ engagement + revenue dashboard
│  ├─ monetization/    spender tiers, UA spend vs budget
│  └─ _template/       copy-paste starter (skipped by --all)
├─ tools/make_fixtures.py
└─ output/             generated, gitignored
```

## Regenerating fixtures

`make_fixtures.py` is deterministic for a given seed and anchor date:

```powershell
python tools\make_fixtures.py --seed 42 --days 420          # default
python tools\make_fixtures.py --small                        # 60 days, fast
python tools\make_fixtures.py --anchor 2026-06-30 --seed 42  # pinned, for baselines
```

The anchor defaults to today so that reports using `ctx.today`-relative windows
land on data. It also writes `events.yaml`, so the annotated anomalies line up
with the generated numbers.

## Running without the database

`--test` mode patches out every connection and generates mock DataFrames from
the SQL, so reports run with no fixtures at all:

```powershell
python -m trellum.run --all --test
```

That is the fast smoke test. The real fixtures are what make the dashboards
look like plausible dashboards.
