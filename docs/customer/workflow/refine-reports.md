# Refine reports in your browser

When you ask a coding agent to create or update a report, Trellum's project
guidance includes opening it for review and listening for feedback. You can
request a focused change from the report itself. Trellum relays that feedback
to the coding agent working in your repository; the agent edits the source,
rebuilds, and the preview reloads.

## The live review loop

1. **Ask your coding agent to create or update a report.** It builds and
   validates the result, opens the local review, and gives you a clickable link.
2. **Inspect the report while the agent listens.** Review starts as part of
   the report request. If the agent cannot open your browser, it explains the
   limitation and provides a link. Explicit build-only requests skip this step.
3. **Select a chart or table, or type a general request, and send it.** Both
   an element selection and an element-free message are supported.
4. **Let the agent edit and rebuild.** It changes the report definition or its
   source files, runs the build, and replies in the panel. The browser refreshes
   when the rebuild is ready.
5. **Repeat, then inspect the source diff.** Keep the resulting source in
   your normal Git review and publishing process.

To start a new review of an existing report without requesting an edit first,
you can use this prompt with your coding agent:

> Start a live review of reports/store-health. Keep listening for my browser
> feedback, update and rebuild the report, and reply in the review panel.

### Commands for the coding agent

The agent can run this loop from the repository root, using the session number
returned with the polled feedback for `<session>`:

```bash
python -m trellum review start reports/store-health
python -m trellum review poll --report store-health
# Apply requested edits to reports/store-health source files.
python -m trellum.run reports/store-health --no-serve
python -m trellum review poll --report store-health --session <session> --reply "Updated the report and rebuilt the preview."
```

`poll` waits for feedback, so the agent must keep that process alive (or
restart it if its timeout expires). Running `start` alone serves the report and
enables review; it does not create a model, editor, or coding agent. A reply
needs the original `--session` or an explicit `--feedback` ID to mark picked-up
work complete; replies without either identity only add chat text. The
`--report` option isolates feedback when several reports share a project.
When the user presses **End**, `poll` exits with code 3; **Send & end** delivers
the final batch before that exit. Do not restart a session after it has ended.
When the review is finished from the agent side, use
`python -m trellum review end` when the user asks you to wrap up. The installed framework's
`python -m trellum guide review` is the canonical reference for current flags
and lifecycle details.

The agent edits files under `reports/` and rebuilds the report. Do not hand-edit
the generated `output/` HTML or expose the loopback review endpoints. The
feedback relay itself needs no provider key; the coding agent you choose has
its own account, setup, and runtime requirements.

## Local review and the portal assistant

Local review is for changing report source in your repository with a coding
agent. The optional portal assistant answers questions about portal reports and
can propose supported portal actions with approval; it never edits your Git
repository. Public or exported reports do not retain the local editing chat.

For a complete local workflow that turns filtered report captures into an
article or presentation with an agent, see [Local analysis](local-analysis.md).
