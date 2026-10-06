# Product walkthrough: Nova Play's reporting workflow

**A 2–3 minute script** for the Trellum framework and its optional self-hosted
portal. Nova Play's **Product Insights** studio is the main story. The demo's
Store Health report belongs to a separate fictional retail business, so it is
shown as its own example rather than combined with game data.

## The conversation

| Time | Dialogue | What we see |
|---|---|---|
| 0:00–0:12 | **User:** “Game revenue is moving. Did player retention move too? I want something the team can check, not just a number in chat.” | Open **Nova Play → Product Insights**. The dashboard gives the team one home for its reports. |
| 0:12–0:27 | **User to local coding agent:** “Inspect the demo sources and `metrics.yaml`. Which existing measures can answer this?” **Agent:** “The project has an offline SQLite source, `demo_db`, and a CSV source, `ua_budget`. Gross revenue and Day-1/Day-7 retention are defined metrics. I’ll keep the question within the game data.” | Show the local project and `python -m trellum data` / `python -m trellum metrics`. The optional user-acquisition CSV is a separate source; it is not needed for this question. |
| 0:27–0:42 | **User:** “Build a reviewable view of game revenue and retention over time. Reuse the shared metric IDs, keep the title and platform filters, and show me the source diff before we publish.” **Agent:** “I’ll work in the report project, build from `demo_db`, and validate the result.” | The agent edits the existing Nova Play `player-overview` report in a local Git branch. Show the actual Python/YAML diff and run `python -m trellum.run reports/player-overview --no-serve` followed by `python -m trellum validate output/player-overview`. |
| 0:42–0:57 | **User:** “What can I explore in the report?” **Agent:** “The built report keeps the calculations reviewable and lets you filter the result in the browser.” | Open the built Player Overview report. Demonstrate its real filters and charts. Let the values speak for themselves; do not add an unverified explanation for a trend. |
| 0:57–1:12 | **User:** “How do other reports know what ‘gross revenue’ means?” **Agent:** “The project defines it once in `metrics.yaml`. Reports claim that metric by ID, and the catalog shows which builds use the current definition.” | Show `metrics.yaml`, then the studio **Metrics** page at `/s/demo/demo/metrics`. The demo defines 12 metrics; 11 are claimed by reports. Show the actual catalog state in the seeded instance. |
| 1:12–1:27 | **User:** “How does this get into the portal?” **Agent:** “Connect the studio to a Git repository and branch, then publish the reviewed commit. The portal fetches the source; our changes still go through Git.” | Open **Studio settings → Repository** at `/s/demo/demo/settings/repo`. Explain the repository fields and publishing mode. This demo has no connected remote repository, so present this as setup rather than a completed sync. |
| 1:27–1:42 | **User:** “Where do the data connections go?” **Agent:** “The project declares source names and non-secret settings. A studio admin supplies and tests any required credentials in the portal.” | Show **Studio settings → Data sources** at `/s/demo/demo/settings/datasources`. The sample project uses local synthetic SQLite and CSV data; there is no live warehouse connection. On a deployed portal, repository declarations appear after project import or publish. |
| 1:42–1:56 | **User:** “Mark the launch dates so the charts have context.” **Agent:** “I’ll add them to the project's `events.yaml`. The studio calendar and report annotations read those same declarations.” | Show the demo event file and the annotations calendar at `/s/demo/demo/annotations`. The calendar is read-only in the portal; event edits are reviewed in Git. |
| 1:56–2:10 | **User:** “And our experiments?” **Agent:** “The Nova Play experiment reports declare their tests. The portal overview reads their latest built results and lifecycle.” | Show `/s/demo/demo/experiments`. The seeded project has three A/B tests. Point to the actual report-backed values and confidence intervals; the overview does not calculate new experiment statistics. |
| 2:10–2:23 | **User:** “Can we watch retention after future builds?” **Agent:** “You can set a plain-language alert rule on a report. Its evaluation uses the portal assistant, which hasn't been configured in this demo.” | Show the Alerts page at `/s/demo/demo/alerts` and, if available, its rule form at `/s/demo/demo/alerts/new`. Explain the report, instructions, trigger, and recipient fields. Leave evaluation and delivery unrun. |
| 2:23–2:35 | **User:** “Could I ask the portal a quick question instead?” **Agent:** “Buddy answers questions about built reports. An organization admin must configure a model provider first.” | Show organization assistant settings at `/orgs/demo/settings/assistant`. The local demo has no provider configured, so stop at setup; the local coding agent and Buddy are separate tools. |
| 2:35–2:50 | **User:** “I also wanted to look at margin. Is that part of this game comparison?” **Agent:** “Margin is in Store Health, our separate synthetic retail example. Let’s inspect that report on its own rather than combine different businesses.” | Open Store Health in Trellum Dark. Show its retail revenue and margin views as a second example, distinct from Nova Play's gaming reports. |

## Setup notes

- The public [demo gallery](https://trellum.dev/demo/) serves interactive
  **reports only**; it is not a live portal. The portal needs a self-hosted
  backend. See the [local Docker trial guide](https://trellum.dev/docs/latest/install/docker-compose/).
- In this seeded project, the organization and studio display names are **Nova
  Play** and **Product Insights**; the portal URL slugs are `demo/demo`. The
  report project declares `demo_db` (generated SQLite) and `ua_budget`
  (`data-sources/uploads/ua_budget.csv`). Both are synthetic and offline.
- Ten reports are built in the demo: nine authored reports plus the generated
  metrics report. Nova Play includes Player Overview and three experiments;
  Store Health is a separate Northwind Threads retail report. Keep their
  questions and data separate.
- The local portal demo has no connected Git remote, model provider, or email
  delivery configuration. Show repository and source setup as a workflow,
  not a successful live connection. Buddy answers and alert evaluations need
  an administrator-configured provider; do not stage a response or alert
  outcome until the root owner has configured and verified it.
- The script's local coding-agent prompts are illustrative. They describe an
  agent working in the repository with the installed Trellum CLI; they do not
  assume a portal MCP/API connection. The portal's Buddy assistant is a
  separate, optional feature.
- Use only values visible in the report builds. Avoid narration that asserts
  a cause or trend unless the captured synthetic data supports it.
