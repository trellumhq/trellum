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
standalone report. Select the chart or section; press Escape to cancel. The
capture saves the selected view as frozen evidence so an article can cite the
exact view you reviewed.

The downloaded JSON file includes the PNG image **inside the same file**, plus
the report name, selected filters, capture time and source build time when
available. You do not need to download or upload a separate image. Capturing
downloads a file to your device; it does not publish an image or an article.
It does not include raw report rows or generate an analysis story. Capture
time and build time identify when the artifacts were made; they do not establish
the period the data describes. State that reporting period in the article, as
the Northwind example does for its synthetic September 2026 evidence.

## Turn the capture into an analysis with AI

**Capture a view → attach the file to your AI authoring tool → review the draft → publish.**

Use the AI tool that works with your analytics repository. Attach the downloaded
capture file and explain the finding you want to share. For example:

> Create a Trellum analysis from this capture. Explain the finding, include the
> image and source, and show me a draft before publishing.

The authoring tool imports the capture, extracts its image and source details,
and writes an article around them. Review the text and the evidence before
publishing. The published analysis then includes the image and source caption
for its chosen audience. The picture stays fixed when the original report updates.

The portal **AI assistant** can answer questions about reports, but cannot yet
import capture files or write analysis articles. This authoring step uses a
separate AI tool with access to the repository.

## Advanced: create an article with commands

From your analytics repository:

```bash
trellum analysis new september-conversion
trellum analysis import reports/september-conversion path/to/report.trellum-capture.json --name conversion
```

The import prints a Markdown image reference to paste into `content.md`, where
you write the finding, assumptions, and recommendation. Then build the article
and publish it through your repository's configured publishing workflow.
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

## Create a presentation

Use a presentation when the finding is best explained in an ordered set of
slides. The default analysis format remains an article.

```bash
trellum analysis new september-decision --format presentation
trellum analysis import reports/september-decision path/to/report.trellum-capture.json --name conversion
```

The scaffold creates `report.yaml` with `kind: analysis` and
`format: presentation`, `slides.yaml`, and an empty `evidence/` directory.
It does not require `content.md`. Import evidence as usual, then put the local
PNG path and descriptive alternative text in a slide:

```yaml
version: 1
slides:
  - id: question
    layout: title
    title: Where should we investigate conversion?
    text: September 2026 findings and recommendation.
  - id: mobile-finding
    layout: image-text
    title: Mobile checkout needs investigation
    text: |
      Mobile conversion is **1.80%**. Compare channel mix before choosing an experiment.
    image: evidence/conversion.png
    alt: September mobile checkout funnel showing sessions and purchases
```

Version 1 supports `title`, `text`, `image`, and `image-text` layouts. Every
slide needs a unique lowercase ID and a title. Text and image-text slides need
Markdown `text`; a title slide may also include it. Image and image-text slides
need a local PNG `image` and non-empty descriptive `alt`. Other fields are
rejected. Raw HTML is disabled and remote images are rejected. Keep IDs stable
when revising: links use `#slide-mobile-finding`, for example.

Slides keep their authored order, remain readable without JavaScript, and use
the same evidence captions as articles. The
[Northwind presentation source](https://github.com/trellumhq/trellum/tree/main/trellum/demo/reports/checkout-presentation)
demonstrates all four layouts using frozen synthetic evidence.

### Present in a meeting

Choose **Present** to show one slide at a time. In a review session, turn
**Select** off first. Use **Next** and **Previous**, the arrow keys, or a
horizontal swipe to move between slides. **Home** and **End** jump to the first
and last slide. **Escape** or **Exit presentation** returns to the readable
document. Keyboard focus returns to the Present button. If the browser denies
fullscreen, the presentation stays usable inside its window.

For Teams, open the presentation on the computer you are presenting from,
choose Present, and share that browser window or screen. Click inside the
slide after starting the share so arrow keys reach the presentation. See
[Microsoft's screen-sharing instructions](https://support.microsoft.com/en-us/teams/meetings/present-content-in-microsoft-teams-meetings).
Participants viewing the shared screen do not need access to your local
server; anyone opening a separate analysis link needs its usual access.

Trellum presentations are browser documents. PowerPoint Live requires
`.pptx` or `.ppsx` files; Trellum does not export those formats. Browser printing
includes every slide and hides the presentation controls, including when you
print while presenting.

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

Analyses support articles and ordered presentations with still images.
Interactive snapshot charts, PowerPoint export, and editing inside the portal
are separate capabilities.
