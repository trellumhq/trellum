# Build a local analysis with an agent

Use a report as evidence, then write the finding into a Git-backed article or
presentation. The local analysis workspace keeps the source report, the
destination analysis, and your feedback in one review session.

## Open the analysis workspace

Name the source report in your request when you know which one to use. If you
do not know its name, the agent can show the available report titles and ask
which one to open. Articles are the default; ask for a presentation when you
want slides. Include the reporting period and any filters that matter to the
finding.

From the project root, the agent creates the destination analysis and opens a
workspace with the chosen source report selected:

```bash
python -m trellum analysis new cart-funnel-finding --format article
python -m trellum analysis session reports/cart-funnel-finding --source cart-funnel
```

Use `--format presentation` for slides. The destination and source can have
different slugs. The first `--source` sets the initial report; the source
picker lists all visible reports in the project. Omit `--source` to choose
one in the workspace. The
workspace builds the analysis and serves it with the source reports. If a
source report is missing or out of date, the workspace builds it in the
background and opens it when ready.

The printed **Analysis workspace** URL opens the combined review. Its tabs
show the analysis, source reports, and feedback queue. You can return to the
same workspace while the session remains active. There is one review queue
and one agent listener for this destination analysis.

## Select and route feedback

Use **Select** in the familiar bottom review toolbar. In the source-report
pane, select a whole chart or section and explain what it should contribute
to the analysis. In the analysis pane, select an individual heading, text
block, or other element and describe the edit. **Queue** adds the request to
the shared queue; **Queue & send** adds it and sends the queue to the agent.
Both panes target the active analysis, with the selected source attached to
each request. For example, a source selection can say:

> Add this chart to the analysis: explain why conversion fell over the
> selected dates, using the current filters.

The note and captured chart context travel together. Source selections add
evidence to the analysis by default; analysis selections edit the authored
result. To edit the source report itself, open its ordinary report review.
That independent review keeps its familiar feedback and selection behavior.

The queued item preserves the page, filters, build context, and chart snapshot
from the time you selected it. Later filter changes do not alter that queued
evidence. If the source report is rebuilding or the view changes before its
snapshot is ready, wait for the background build to finish and select the
element again. For a presentation, a selection may refer to an enclosing
section rather than a specific heading; name the intended slide or section
in your note. If the wording is unclear, provide the exact text you want.

You can queue several selections and notes, then send them together. Each
item keeps its source and purpose. Keep the workspace session active
while the agent works through the queue; the agent replies in the workspace
and continues listening until you end the session. The agent uses one poll
scoped to the destination analysis for the whole workspace, processes each
batch, replies, and polls again until the session ends.

For chart comparisons, include the dates and filters you used. Ask the agent
to check the underlying data before stating that a value increased or fell,
and distinguish a percent change from a percentage-point change. Ratios
across groups should use total numerators divided by total denominators, not
the average of each group's ratio.

## Use the workspace from a phone

The printed URL uses `localhost`, which points to the device opening it. On a
phone, `localhost` means the phone itself, not the computer running Trellum.
The review API is loopback-only, so changing the link to a bare LAN address
does not make remote review work. Phone access requires a protected,
authenticated proxy that routes the review requests back to the local server.
Do not expose the development server or review endpoints directly to the LAN.

## Evidence and interrupted sessions

A captured chart is a frozen image with report context, active filters, and
capture/build times when known; it contains no raw dataset. The agent imports
the capture into the analysis and edits its source in Git. Rebuilding the
source report later does not change evidence already in the analysis. Local
review does not publish the analysis or change its sharing permissions.

If a session is interrupted, the agent can list pending capture IDs and
resume importing them:

```bash
python -m trellum analysis inbox reports/cart-funnel-finding
python -m trellum analysis import reports/cart-funnel-finding --inbox <capture-id>
```

Without an active analysis session, **Capture for analysis** downloads a JSON
file that can be imported by file path. For a source-report-only preview, see
[Refine reports in your browser](refine-reports.md).
