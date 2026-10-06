# Two workflows, one repository

Most analytics questions are asked once and never again. A few are asked every
Monday for three years. Those two need different treatment, and trying to serve
both the same way is how BI tools end up full of dashboards nobody opens.

So there are two workflows here, and they share one repository.

{{figure:two-workflows}}

!!! note
    Everything in this section is a recommendation. {{BRAND}} requires very
    little of your repository — see
    [The analytics repository](/docs/latest/workflow/analytics-repository/) for
    the short list of what is actually enforced.

## Ad-hoc: get an answer

Someone asks why signups dipped last week. You — or the person who asked,
working in their own AI assistant — open the repository. The assistant already
knows which warehouse is authoritative, how signups are defined, and which
tables to trust, because that is written down there. It queries the configured
data source, writes a short script, and answers.

No report is created. No build runs. Nothing is scheduled. That is the point:
answering a one-off question should cost minutes, not produce a dashboard
someone has to maintain forever.

## Reports: make it reproducible

When an answer turns out to matter — the team wants it weekly, or the number is
going in front of the board — promote it to a report. Now it is a `report.yaml`,
a generator and its SQL: reviewed in a pull request and versioned, so the
derivation can be inspected and repeated. The result can change as the source
data or reporting window changes.

This is the traceable half. The value is not the chart; it is that a number
on a dashboard can be traced back to a commit somebody approved.

## The promotion path

The two are not separate worlds. The usual life of a good report is:

1. Someone asks a question
2. An ad-hoc script answers it
3. The question comes back
4. The script becomes a report

Skipping straight to step 4 for every question is how dashboard sprawl starts.
Letting a question prove itself first is cheaper and leaves less behind.

## What they share

One repository, one set of data sources, one set of definitions. An ad-hoc
script and a built report reach your warehouse through the same configured
connection, so a question answered on Tuesday and a report shipped on Friday
cannot quietly disagree about what "revenue" means.

## And then the portal

Reports run fine on a laptop. The portal is for when they need to reach people
who will never clone a repository: on a schedule, behind your single sign-on,
with access control and an audit trail. Nothing about the reports changes —
you point a studio at the same repository.

## Next

- [Who builds it, who it serves](/docs/latest/workflow/who-builds-it/) — the team model this depends on
- [The analytics repository](/docs/latest/workflow/analytics-repository/) — what to put in it
