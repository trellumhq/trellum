# Publish an analysis

An analysis is a readable article that keeps a finding together with its
evidence, assumptions, and recommendation. The demo's **Where Northwind loses
buyers** is a worked example: it explains September 2026 checkout findings
with two captured report views and a recommendation. Read the
[demo article](https://trellum.dev/demo/checkout-findings/) or inspect its
[Markdown and evidence](https://github.com/trellumhq/trellum/tree/main/trellum/demo/reports/checkout-findings).

Articles live in your analytics Git repository and appear under **Analyses**
in the studio. They use the same access groups, favorites, sharing policies,
activity tracking, exports, and snapshot delivery as reports.

The studio has a separate default audience for new analyses. It is applied on
first discovery, before the article becomes visible, and later rebuilds keep
the article's audience. Administrators can set a different audience for an
individual article from **Options → Access**. See
[Access control](/docs/latest/portal/access-control/) for Studio audience,
Private, and explicit group grants.

## Capture the evidence

Open a report and set its filters to the view you want to explain. Choose
**Capture for analysis** from **Options** in the portal, or **Export** in a
standalone report. Select a chart or section; press Escape to cancel.

The downloaded capture contains a PNG of the selected view and its source
context: report name, selected filters, capture time, and source build time
when available. It does not include the report's underlying dataset. Capture
time and build time identify when the artifacts were made; they do not establish
the period the data describes. State that reporting period in the article, as
the Northwind example does for its synthetic September 2026 evidence.

## Create an article

From your analytics repository:

```bash
trellum analysis new september-conversion
trellum analysis import reports/september-conversion path/to/report.trellum-capture.json --name conversion
```

The import prints a Markdown image reference to paste into `content.md`.
It writes the image and a provenance file under `evidence/`; keep both in Git.
Importing another capture with the same name fails rather than replacing the
old evidence. Use a new name when you intend to revise a finding.

The analysis has this structure:

```text
reports/september-conversion/
├── report.yaml
├── content.md
└── evidence/
    ├── conversion.png
    └── conversion.json
```

Its metadata uses the existing report manifest:

```yaml
kind: analysis
slug: september-conversion
name: Why conversion fell in September
description: Findings, evidence, and recommended next steps for September conversion.
author: Analytics team
category: Conversion
tags: [conversion, september]
```

An analysis needs no `generator.py`, data sources, or data-refresh schedule.
The scaffold suggests headings for the question, findings, evidence,
assumptions, and recommendation. Change that structure to fit your argument.
Use Markdown headings, tables, lists, images, and links. Images need descriptive
alternative text. Raw HTML is disabled; images must be PNG files local to the
analysis directory. Normal external links are supported. Choose a built-in
Trellum theme; analyses do not execute custom Python components or themes.

## Preview, review, and publish

Build an analysis with the same command used for a report:

```bash
python -m trellum.run reports/september-conversion --no-serve
trellum review start reports/september-conversion
```

The preview has a contents menu, section links, and evidence captions. Select
an image to open it at its original size. The browser review loop also works
with a coding agent editing the Markdown and rebuilding the article.

Review the text, captured images, and provenance in your normal Git workflow.
Commit and push the analysis directory. The studio's existing automatic or
manual [publishing setting](/docs/latest/portal/publishing-from-git/) determines
when it is published. If a rebuild fails, the last successful article remains
available.

## Share with the right audience

Find the article under **Analyses** and copy its page or section link into
chat. A signed-in reader needs access to the analysis. Administrators can
assign selected groups using **Options → Access**, just as for a report.

The analysis has its own audience. Its included evidence is published to that
audience; the reader does not need access to every source report. A Private
analysis is hidden from ordinary studio Viewers unless they are explicitly
granted access. Opening a source link still requires the source report's
normal permissions.

Public [share links](/docs/latest/portal/share-links/) and
[embeds](/docs/latest/portal/embedding/) follow the existing organization
policies and deployment limitations. They do not grant access to source reports.

## Keep the finding stable

Source report refreshes do not change an article's captured evidence. The
article builds from its committed Markdown and images, so it can be rebuilt
without warehouse credentials even after the source report is removed.

To change a conclusion or replace evidence, make a new Git change and publish
it. Git keeps the revision history. Existing output-retention and repository
removal behavior also apply to analyses; capture images in Git remain available
to rebuild them.

Analyses currently use one article with still images. Interactive snapshot
charts, chapter navigation, slide mode, and editing inside the portal are not
part of this format.
