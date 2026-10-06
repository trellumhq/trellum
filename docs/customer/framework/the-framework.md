# The framework

Reports are written with the **{{BRAND}} framework** — a Python library
released under **AGPL-3.0-only**. You can use it entirely on its own:
install it, add a report to any git repository, and build interactive
dashboards on your laptop with no server, no account, and no portal.

The licence permits commercial and internal use. If you distribute a modified
framework or let users interact with a modified version over a network, follow
the AGPL source-sharing terms. Report data and authored content keep their own
copyright and terms; generated pages identify the embedded Trellum runtime and
link its exact source.

## Getting it, and learning it

Both live in the same place — the framework is open source, so its manual sits
beside the source it describes:

**[github.com/trellumhq/trellum]({{FRAMEWORK_REPO}})**

If you are new to the package, follow [Try Trellum locally](/docs/latest/install/try-it/).
For a topic-by-topic route into the framework reference, see
[Framework capabilities](/docs/latest/framework/capabilities/).

The README there is the complete reference: how to install it, report
structure, every component, filters and cross-filtering, themes, the data
layer, multi-scope reports, and the built-in statistics. It is maintained by
the people changing the code, in the same commits — which is why this site
does not keep a second copy of it, including of the install steps. A second
copy is only ever a copy that goes stale.

## What the portal needs from your repository

The one thing worth knowing on this side, because it is our contract rather
than the framework's: report discovery uses directories under `reports/` that
contain `report.yaml` and `generator.py`. Other project files are not needed
to discover a report, though supported configuration such as data-source
declarations is read when a report uses those features. See
[The analytics repository](/docs/latest/workflow/analytics-repository/).

## What this site documents instead

These pages cover the **portal**: running it, connecting repositories to it,
permissions, single sign-on, and day-two operations. The portal uses the same
AGPL-3.0-only licence.
These pages exist because operating the portal is a different task from
authoring reports, not because either codebase is hidden.

The split is simple:

| To learn | Go to |
|---|---|
| How to write a report, what components exist, how filters work | [The framework repository]({{FRAMEWORK_REPO}}) |
| How to run the portal, connect a repository, manage access | These pages |
| How the two fit together in practice | [Recommended workflow](/docs/latest/workflow/two-workflows/) |

## You do not need the portal to start

The framework stands alone, and nothing you build with it is locked to us. When
your reports need to reach people who will never clone a repository — on a
schedule, behind your single sign-on, with access control — the portal runs the
same repository unchanged.

See [Two workflows](/docs/latest/workflow/two-workflows/) for how the two halves
fit together, and
[Connecting a repository](/docs/latest/portal/connecting-a-repository/) when you
are ready to hand your reports to the portal.
