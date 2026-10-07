# Refine reports in your browser

Once a report is open locally, you can request a focused change from the
report itself. Trellum relays that feedback to the coding agent working in
your repository; the agent edits the source, rebuilds, and the preview reloads.

## The live review loop

1. **Build and open a report locally.** Follow [Try Trellum locally](../install/try-it.md)
   for the sample project, or open your own report's local preview.
2. **Ask your coding agent to start review and keep listening.** The review
   session is a relay between your browser and an active external agent.
3. **Select a chart or table, or type a general request, and send it.** Both
   an element selection and an element-free message are supported.
4. **Let the agent edit and rebuild.** It changes the Python or SQL source,
   runs the build, and replies in the panel. The browser refreshes when the
   rebuild is ready.
5. **Repeat, then inspect the source diff.** Keep the resulting source in
   your normal Git review and publishing process.

Use this prompt with your coding agent:

> Start a live review of reports/store-health. Keep listening for my browser
> feedback, update and rebuild the report, and reply in the review panel.

### Commands for the coding agent

The agent can run this loop from the repository root:

```bash
python -m trellum review start reports/store-health
python -m trellum review poll --report store-health
# Apply requested edits to reports/store-health source files.
python -m trellum.run reports/store-health --no-serve
python -m trellum review poll --report store-health --reply "Updated the report and rebuilt the preview."
```

`poll` waits for feedback, so the agent must keep that process alive (or
restart it if its timeout expires). Running `start` alone serves the report and
enables review; it does not create a model, editor, or coding agent. The
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
