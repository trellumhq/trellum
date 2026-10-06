# Alerts

You rarely know what to alert on until you need to be alerted. A threshold
written in advance is a guess; the number that matters this week was not
the one anyone thought of last month. So an alert here is not a threshold.
It is **a report, plain-language instructions and a cadence** — and the AI
assistant decides. Each time the rule runs, the assistant looks at the
report's data, compares it with what it saw last time, and either writes an
alert or records why it stayed quiet.

Alerts live in the studio's **Alerts** tab. Studio developers and admins
create and edit them; viewers see the same rules and their run logs,
read-only. Anyone who can see the studio can be a recipient.

Alerts need the [AI assistant](/docs/latest/portal/ai-assistant/) to be
configured for the organization — the evaluation *is* an assistant turn.

The two meet in both directions: tell the assistant "watch this for me"
in a conversation and it proposes the rule for approval, and every alert
email carries an **Open in the assistant** link that opens a conversation
seeded from that evaluation — see
[Actions and approval](/docs/latest/portal/ai-assistant/#actions-and-approval).

## Writing instructions

Instructions are a few sentences to a colleague who can read the report and
query its data. Say what to watch and when to speak up; the assistant works
out the numbers. Some that work well:

| Ask | Instructions |
|---|---|
| An outage | *Tell me if DAU or sessions drop sharply in any region.* |
| A launch | *We launched Neon Racer on 2026-06-29; tell me whether it moved revenue and DAU.* |
| A promotion | *The weekend coin sale ran 2026-08-23 to 27; tell me if IAP revenue rose and whether the refund rate rose with it.* |
| A threshold in words | *Alert me if D1 retention is below its four-week average for three days running.* |
| Anything off | *(leave blank)* — the assistant reads it as "tell me if anything looks off" |

Two things it already knows, so you do not have to say them: the ordinary —
weekly seasonality, a new high on an existing trend, gradual growth — is not
an alert unless you ask for it, and something it reported in its last few
decisions is not news twice. If it gets a decision
wrong, the fix is usually one more sentence — "compare with the same weekday
last week", "only the EU region", "good news counts too".

## Triggers

- **After every build** (the default). A report's data only changes when it
  is rebuilt, so this is where an evaluation belongs: right after a
  successful build, once per build. A build that failed is not evaluated —
  [build failure notices](/docs/latest/portal/email-delivery/#build-failure-notices)
  already cover that.
- **On a schedule.** Hourly, daily, weekdays, weekly or monthly at a time and
  timezone you pick — for "every Monday morning, tell me whether…".

## Recipients

The same picker as a [delivery](/docs/latest/portal/email-delivery/): people
by name, the studio's admins or developers, everyone in the studio, or an
organization permission group. Roles and groups are resolved when the alert
fires, so a joiner starts hearing from it and a leaver stops. Whoever is
picked, only people who can currently see the studio are mailed.

## Cooldown

After an alert fires, the rule stays quiet for the cooldown (24 hours by
default). Runs during the cooldown are recorded as *skipped* rather than
evaluated — the assistant is not asked, so they cost nothing. Set it to 0 to
alert on every run that warrants it.

## The run log

Every evaluation is recorded under the rule, newest first: the decision, the
title and message the assistant wrote, the figures it cited, what it cost,
who was mailed, and any error. A quiet run is as informative as an alert —
its message says *why* it stayed quiet ("revenue moved within its weekly
range; the 12 % Sunday dip matches the last four Sundays"). That is the
tuning loop: if a quiet run should have alerted, or an alert should have
stayed quiet, the message tells you which sentence to add.

Other things a run can say:

- **Not evaluated** — the report's last build did not succeed.
- **Skipped: cooldown** — an alert fired recently.
- **Skipped: budget** — the owner's assistant budget is spent (see below).
- **Error** — with the reason; a rule with no owner shows this until someone
  edits it.

## Test now

**Test now** evaluates the rule immediately, with delivery switched off:
the decision, message and evidence land in the run log marked as a test,
nobody is emailed, and the cooldown is not touched — it runs even while the
rule is in cooldown, because tuning instructions is what it is for. Use it
after writing or changing a rule, before waiting for the next build.

## What it costs, and who pays

One evaluation is one assistant turn with a small cap on how many queries
it may run, so it costs cents, not dollars; an after-build rule runs once
per build, which the organization already pays for. Spend is billed to the
**assistant budget of the person who created the rule** — the same
per-user budget the assistant panel uses. If that budget is exhausted, the
run is recorded as *Skipped: budget*, the owner gets one email, and the
rule resumes on its own when the budget allows. Deleting the owner's account
parks the rule with an error until someone else saves it.
