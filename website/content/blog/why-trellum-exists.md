# Why {{BRAND}} exists

I wanted answers from data faster. That was the whole starting point.

I tried using AI to turn questions into reports. An agent could write a query,
shape some data, and produce an HTML dashboard much faster than I could build
the same thing by hand. It made ad-hoc analysis feel wonderfully cheap.

But each report was also an improvisation. The layout changed between runs.
Logic that should have been shared was written again. A polished HTML file did
not explain where a number came from or make the next refresh dependable. If a
report became useful enough to run again, I ended up writing custom code to
stabilize it.

That was the useful lesson: the agent should help write a durable report, not
re-perform the whole report every time.

## Put the report in ordinary code

{{BRAND}} grew from that idea. A report is a small directory in your own
repository. SQL loads rows. Python transforms them and assembles components.
YAML records the report's identity and configuration. One command builds the
same interactive HTML and JSON again.

You can write those files yourself. You can also work with Codex, Claude Code,
Cursor, or any other coding agent that can edit normal files and run commands.
There is no special agent integration to buy or learn. The framework describes
its own commands through `python -m trellum`, so an agent can read the same
guide as a person and work inside the repository that already holds your data
definitions.

This changes the AI workflow in a practical way. The creative step still
happens in conversation: ask for a revenue breakdown, refine a chart, add a
filter. The result is Python and SQL that remain after the conversation ends.
The next build runs that code instead of asking a model to invent the dashboard
again.

## Ad-hoc when you need it, recurring when it matters

Not every question needs a report. For a quick answer, query a configured
source and move on. When the answer needs readers, filters, review, or another
refresh next week, promote it into a report directory.

That recurring report does not require the {{BRAND}} portal. Build it on a
laptop, run it from cron or your existing CI, and serve the portable output
from any static server. The optional self-hosted portal adds managed schedules,
authentication, repository builds, and team sharing when those services are
useful. It is an extra operating layer, not a prerequisite for the framework.

## Connect the data you need

{{BRAND}} includes connectors for supported databases and common files. A
report can load warehouse rows, CSV, Parquet, Excel, SQLite, or another
configured source and combine the resulting DataFrames in Python.

For a service without a built-in connector, use its Python SDK or write a small
adapter. Authentication, pagination, and source-specific behavior stay explicit
in code, where they can be tested and reviewed.

The metrics layer lets reports reuse named definitions. Validation catches
many structural mistakes, such as components pointing at missing columns or
filters that cannot reach a chart. These are guardrails. They do not certify
that a business definition is correct or that source data tells the truth.
People still need to review the SQL, grain, assumptions, and result.

## Git is part of the reporting workflow

Once reports are code, ordinary Git practices become useful. A pull request can
show a changed query beside the chart code that consumes it. Commit history
records when a metric or filter changed. A revert restores the earlier
definition. A branch can hold an experiment without replacing the report that
readers use today.

That makes reports easier to reproduce and easier to discuss. It also means the
work belongs in your repository, alongside the context that explains it,
instead of disappearing into a one-off generated file.

## Why I made it free and open source

I want more people to be able to work directly with data and turn questions
into useful, repeatable reports. That is why {{BRAND}} is a free
AGPL-3.0-only project. The source, framework, optional portal, documentation,
and demo are public. Third-party components keep their own licences and
notices.

Open source does not make data work automatic, and AI does not remove the need
for judgment. It does make the machinery inspectable. You can see the query,
change the Python, review the metric, run the build, and decide where the output
is hosted.

## Try the released framework

Start in a clean directory or an existing repository with Python 3.11 or newer.
These commands use the published v0.1.0 wheel and the included synthetic demo:

```bash
python -m venv .venv
```

Activate it with the command for your shell.

macOS or Linux:

```bash
. .venv/bin/activate
```

Windows PowerShell:

```powershell
.venv\Scripts\Activate.ps1
```

Then install and build:

```bash
python -m pip install https://github.com/trellumhq/trellum/releases/download/v0.1.0/trellum-0.1.0-py3-none-any.whl
python -m trellum.demo --dest trellum-demo
cd trellum-demo
python -m trellum.run reports/player-overview --no-serve --portable
python -m trellum serve --background
```

Or give your coding agent this starting point:

<div class="agentline"><span class="tag">paste to your agent</span><button class="copybtn" type="button" data-copy="agent-prompt-post">copy</button><code id="agent-prompt-post">{{AGENT_PROMPT}}</code></div>

Explore the [demo gallery]({{DEMO_URL}}), then read the
[framework guide]({{FRAMEWORK_REPO}}) when you are ready to build in your own
repository.

<div class="signoff">
<p>I built {{BRAND}} because I needed this workflow. I am sharing it because I want reliable data work to be available to more people.</p>
<span class="sig">— Apollo</span>
</div>
