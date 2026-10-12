# Build an analysis locally with an agent

Use a local analysis session to turn a report view into a written finding or a
presentation. It works from your analytics repository with Cursor, Codex, or
another coding agent; no portal setup is required.

## Start a session

From the repository root, create an analysis. The format defaults to an
article; choose `presentation` for ordered slides:

```bash
python -m trellum analysis new september-conversion --format article
# Or: python -m trellum analysis new september-conversion --format presentation
```

Then start its local session:

```bash
python -m trellum analysis session reports/september-conversion
```

This builds the analysis, starts the local review server, activates one
analysis inbox for the project, and prints the preview and gallery URLs. Keep
the command running while you review. The browser can display the analysis and
collect feedback; it does not edit source files or publish the analysis.

## Capture a report view

Open a local report, set its filters to the view you want to explain, and
choose **Export > Capture for analysis > Send to agent**. Add an optional note
to explain what to inspect. With an active local analysis session, the capture
is sent to that session. Without one, **Capture for analysis** still downloads
the capture JSON for the existing file import workflow.

A capture is a frozen PNG with provenance, including its report context,
filters, and capture/build times when known. It contains no raw dataset. A
pending capture is stored outside the repository and output tree under
`~/.trellum/analysis-inbox/<project-id>`. If importing it fails, it remains
pending so the agent can correct the import and retry. To discard a pending
capture deliberately, run:

```bash
python -m trellum analysis discard reports/september-conversion <capture-id>
```

If the session is interrupted, list pending captures with
`python -m trellum analysis inbox reports/september-conversion`, then resume
the import using its capture ID.

## Work through the finding

Keep the agent polling for the analysis while you inspect the report:

```bash
python -m trellum review poll --report september-conversion
```

The poll returns the capture ID and any annotation feedback. The agent imports
the capture into the analysis:

```bash
python -m trellum analysis import reports/september-conversion --inbox <capture-id> --name checkout-filtered
```

Downloaded captures can still be imported by file path:

```bash
python -m trellum analysis import reports/september-conversion path/to/capture.trellum-capture.json --name checkout-filtered
```

For an article, the agent edits `reports/september-conversion/content.md` and
uses the imported evidence in the explanation. For a presentation, it edits
the ordered `reports/september-conversion/slides.yaml`. A report's filters
shape the capture; the resulting PNG becomes fixed evidence in the analysis.
The agent builds source changes without starting a second server:

```bash
python -m trellum.run reports/september-conversion --no-serve
```

The active browser reloads when the build is ready. The agent acknowledges the
round in the browser review panel and continues polling:

```bash
python -m trellum review poll --report september-conversion --reply "Added the filtered checkout capture and explained the change."
```

Repeat the capture, annotation, import, and rebuild steps until the analysis
answers the question. Review the final source and evidence diff in Git before
committing it. Use the project's normal review and publication process to
share the finished analysis.

## Reading a presentation

Presentations use four built-in layouts and stable slide IDs, so edits can
reorder slides without changing their identity. They remain readable without
JavaScript and support keyboard navigation, mobile screens, and a fullscreen
meeting view. Source changes still happen in Git-backed `slides.yaml`; the
browser is for viewing and feedback.

Articles and presentations use the same existing analysis sharing permissions.
Local preview and feedback do not publish or change those permissions.
