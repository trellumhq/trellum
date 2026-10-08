# Who builds it, who it serves

This way of working is not "AI replaces the analyst". It is close to the
opposite: the people who understand the data write down what they know, once,
and everyone else gets to stand on it.

{{figure:ownership-bands}}

## 1. The data team builds and owns the context

Metric definitions, source knowledge, connections, conventions, and the reports
that matter are built and maintained by the people who are professionals at it.

This is expert work and it stays expert work. It is the part that does *not* get
delegated to a model, because being wrong here is both invisible and expensive:
a subtly incorrect definition of "active customer" does not throw an error, it
just quietly misinforms every decision made downstream of it for a year.

## 2. Everyone else works inside it

Product, operations, commercial — people who are not analysts and never will be
— open the same repository as context in their own AI assistant and answer their
own questions.

The important part is what they are *not* doing. They are not guessing which
source or fields to use, not inventing a definition of revenue, and not waiting
three days for someone to run a query. They are working inside the definitions
your team wrote, whether they realise it or not.

!!! tip
    This is the difference from self-service BI. A query builder hands the hard
    part — knowing which data to trust — to the person least equipped to judge
    it, and the organisation quietly accumulates wrong numbers. Here the
    expertise is written down and applied upstream, so a non-specialist's
    question is answered *through* your team's judgement rather than around it.

## 3. It compounds into data autonomy

Most questions get answered without joining a queue. The ones that recur get
promoted into reviewed, scheduled reports. Every question that exposes a missing
or ambiguous definition sends someone back to improve the context, which makes
the next answer better.

Autonomy without a free-for-all: the answers multiply, while correctness stays
owned, curated and taught by the people who understand the subject.

## What each group needs to know

| Who | What they do | What they need to learn |
|---|---|---|
| Data team / analysts | Build the context; write reports for anything recurring | The framework's data helpers and report components |
| Product, ops, commercial | Ask their own questions against the repository | Nothing about {{BRAND}} — the context files do the work |
| Whoever owns the platform | Connect the repository to a studio so reports reach everyone | The portal |

## Where teams get this wrong

**Writing reports before definitions.** An assistant with good definitions and
no reports will write a good report. One with ten reports and no definitions
will confidently reproduce whatever the last report happened to assume.

**Treating the context as documentation.** It is not a wiki nobody reads — it
is the input to every answer produced in the repository. Stale definitions cause
wrong numbers, so they deserve the same review as code.

**Opening it up before it is ready.** Invite the rest of the business in once
the definitions cover the questions they actually ask. Too early and you have
recreated self-service BI with extra steps.

## Next

- [The analytics repository](/docs/latest/workflow/analytics-repository/) — what to write down, and what we actually require
