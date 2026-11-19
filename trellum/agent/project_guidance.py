"""Managed instruction templates for installed Trellum projects."""

#: Full CLAUDE.md / AGENTS.md guidance for a newly initialized project.
#: Workflow defaults live here; detailed APIs remain behind `trellum guide`.
#: `{py}` is substituted with the interpreter that actually has the framework
#: installed. It is not decoration: a bare `trellum` is only on PATH inside an
#: activated virtualenv, and agents do not activate virtualenvs -- a measured run
#: burned ten tool calls working out the right invocation because this file told
#: it to type `trellum`.
POINTER = """\
# Trellum in this repository

Trellum is a Python toolkit for exploring data, building interactive reports
and dashboards, and writing analysis articles or presentations with captured
evidence. It can use databases, spreadsheets, files and APIs; SQL is optional.
Reports combine Python data preparation with reusable charts, tables, KPIs,
filters and themes, and build to HTML and JSON for local or published viewing.
Local authoring and browser review work without the optional Trellum portal.

**Ask the installed Trellum version for its commands, components and guides**
before choosing an API or copying an example.

At the start of each new chat in this project, run `{py} -m trellum`.
It checks for newer releases at most once per local calendar day. If an update
is available, tell the user and include the upgrade command; continue the task
without upgrading automatically. Offline and opted-out checks stay quiet.

Use this exact interpreter, **run from the repository root**:

```
{py} -m trellum                      what it is, the components, the commands
{py} -m trellum guide <topic>...     depth on demand; several topics at once
{py} -m trellum checks [<id>...]     validator check ids, or what one means
{py} -m trellum validate reports/<x> validation state of a built report
```

Read `context/README.md` and source/metric notes first; use `analysis/README.md` for findings.
Keep exports in `analysis/results/`; update verified context and preserve questions.

{portal}
## Choose the result the user needs

- **A number or an investigation:** inspect configured sources and existing
  metrics; use `trellum guide answer`. Answer with the source, reporting dates,
  method and limitations. Keep useful queries and findings in `analysis/`.
- **An interactive report or dashboard:** work in `reports/<slug>/`; read
  `trellum guide queries format components` before writing a generator.
  Reuse existing reports and components where they fit the question.
- **An article or presentation:** use `trellum guide analysis` and
  `trellum analysis new <slug> [--format presentation]`. Author `content.md`
  or `slides.yaml` with captured evidence; use `trellum analysis session`
  when collecting evidence from reports and reviewing the written result.

Check `trellum metrics` before defining a KPI. Verify source columns, grain,
date coverage and joins; do not substitute invented data for a failed source.
An explicit report request goes through the build and browser handoff below.

`trellum` alone is not on PATH. Report paths like `reports/<slug>` are
relative to the repository root, so run these from there.{cwd_note}

## Discover data and author the result

Inspect configured sources before choosing queries or file readers:

```
{py} -m trellum data
{py} -m trellum metrics
```

For a new source, use `trellum datasource add --help` for supported types and
connection options. The command writes the configuration and tests access.
Supply credentials through its hidden prompt or supported secret input.

Use `trellum guide report` for the generator workflow, `guide validation` for
build findings and `guide upgrade` after upgrading. Read only relevant topics;
one `guide` command accepts several. Edit authored files in `reports/<slug>/`,
then rebuild; generated `output/` files are not the source of a report.

## Build and open for review

Scaffold a new interactive report when needed, then build. `--no-serve` exits
when the build is done:

```
{py} -m trellum.new <slug>
{py} -m trellum.run reports/<slug> --no-serve
{py} -m trellum validate reports/<slug>
```

Read validation results, fix every FAIL and resolve or explicitly explain
WARNs before handing over the report.

Creating or updating a report for a person includes opening it for review
automatically. Do not wait for a separate launch request. After building and
checking validation, start review mode:

```
{py} -m trellum review start reports/<slug>
{py} -m trellum review poll --report <slug>
```
Open the printed report URL in the browser available to the user, verify the
intended report loads, and share a clickable link before polling. A folder
path alone is not a completed handoff. On a remote/headless host, use
`review start reports/<slug> --no-browser` and the agent's browser integration;
a host-browser launch attempt does not prove the user can see it. If access is
unavailable, explain that limitation and provide a working link.
Build changed source first with `--no-serve`, or pass `--rebuild` to review
start, so the review includes the latest edits. Follow `{py} -m trellum guide review`.
For articles and presentations, use `{py} -m trellum analysis session
reports/<slug>` as the review entry point; `guide analysis` explains its
evidence workspace. The browser handoff and report-scoped feedback loop still apply.

Keep the report-scoped poll active. Apply feedback, rebuild with `--no-serve`,
then reply and continue polling:

```
{py} -m trellum.run reports/<slug> --no-serve
{py} -m trellum review poll --report <slug> --session <session> --reply "what changed"
```

Continue until the user ends review; use `review end` if they ask you to wrap
up. Only explicitly requested build-only/source-only work, CI or unattended
publication stops after `--no-serve`; honor a request for no browser.
For an explicitly requested plain preview without feedback, use
`{py} -m trellum serve --background` and share its printed URL.

A report is complete when it is validated and the user can review the result.
"""

#: Appended to POINTER only when the interpreter above came out relative, which
#: is the normal case and the one with the confusing failure. Kept conditional
#: because the same file is written with an absolute path when the virtualenv
#: lives outside the project, and a warning about relative paths would then be
#: describing something the reader cannot see.
RELATIVE_INTERPRETER_NOTE = """ The interpreter path is relative too, so a
command run after a `cd` into a subdirectory fails with `No such file or
directory` — naming the interpreter, not your command. If one dies that way,
`cd` back to the root rather than going looking for a Python."""

#: Added to both pointers only when `.mcp.json` names a `trellum` server, so a
#: repository with no portal never reads about one. Role boundaries precede
#: authoring instructions; the full loop lives behind `guide portal`.
PORTAL_SECTION = """
## Portal roles

MCP server `trellum` (see .mcp.json) acts under the API key owner's role.
Viewers ask questions and read permitted reports/data through available tools.
They cannot create, edit, build or publish reports, even with a write-scoped key.
Report authoring needs a developer/admin with repository access. Help viewers
with permitted data questions; refer report requests to a developer.
`{py} -m trellum guide portal` covers role, scope and action requirements.
The key is TRELLUM_API_KEY in .env; never paste it anywhere else.
"""

#: Fence markers for the block appended to a file the user already owns.
#: Everything between them is ours to rewrite; everything outside is theirs and
#: is never touched. Same contract as ``merge_hooks`` -- re-running repairs in
#: place instead of appending a second copy.
BLOCK_START = "<!-- trellum:start -->"
BLOCK_END = "<!-- trellum:end -->"

#: Managed guidance appended to an existing instruction file. The marker
#: boundaries let setup refresh this block while preserving authored text.
SIGNPOST = """\
{start}
## Trellum in this repository

Trellum supports data exploration, interactive reports and dashboards, and
analysis articles or presentations with captured evidence. Use configured
databases, spreadsheets, files or APIs; local work does not require a portal.
Ask the installed version for its API. Run from the repository root:

```
{py} -m trellum                  what it is, the components, the commands
{py} -m trellum guide <topic>... depth on demand, several topics at once
```

Run `{py} -m trellum` at the start of each new chat. It checks for updates
at most once per local calendar day; tell the user when one is available and
include the upgrade command. Continue the task without upgrading automatically.

Read `context/README.md` and source/metric notes first; use `analysis/README.md` for findings.
Keep exports in `analysis/results/`; update verified context and preserve questions.

`trellum` alone is not on PATH, so name the interpreter as shown.
{portal}

For a number or investigation, read `trellum guide answer` and check
`trellum metrics`. For a report/dashboard, read `trellum guide queries format
components`; edit `reports/<slug>/`, then build and validate. For a written
article or presentation, read `trellum guide analysis` and use
`trellum analysis new <slug> [--format presentation]`. Use `analysis session`
for the evidence workspace. Edit report source, then rebuild generated output.

Creating or updating a report for a person includes opening it for review
automatically, without a separate launch request. Build and validate, then run
`{py} -m trellum review start reports/<slug>`. Open the printed report URL in
the user's browser, verify the intended report loads, and share a clickable
link; a folder path alone is not a completed handoff. On a remote/headless
host, use the agent's browser integration; explain if opening is unavailable
and provide a working link. Keep
`{py} -m trellum review poll --report <slug>` active; rebuild with `--no-serve`
before replying with the feedback session, and continue until the user ends
review. Follow `{py} -m trellum guide review` for the full loop. Only explicitly
requested build-only/source-only work, CI or unattended publication stops after
the build. Honor a no-browser request;
use plain preview only when explicitly requested.
{end}
"""
