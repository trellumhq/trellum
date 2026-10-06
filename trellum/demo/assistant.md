# What the assistant should know about this data

This file is read into the AI assistant's system prompt on every question
asked in this studio. It is for the things the data itself does not say:
which definition the business actually uses, what a column means when its
name is ambiguous, and where a number is easy to get wrong.

It is not a place to restate what the assistant can already see. It reads
every built report's datasets, columns and date coverage automatically.

---

## What we mean by our headline numbers

- **Revenue** is gross, before store commission and before refunds. The
  finance team's "net revenue" is a different number and is not in these
  reports — if someone asks for revenue "the way finance reports it", say
  that it is not available here.
- **A payer** is a user with at least one purchase in the period being
  counted. Someone who paid last month and not this month is not a payer
  this month.
- **ARPDAU** is revenue ÷ daily active users, both taken from the same day's
  rows. Never average the per-row ARPDAU column across rows.

## Traps in this data

- `insert-coin` is a demo report built from synthetic data. Never quote its
  numbers as if they described the business.
- The `economy-firehose` dataset is event-grained, not user-grained. Counting
  its rows counts events, not people.
- The `experiments` report covers three tests on one page. Read the metric and
  analysis mode named in each section; checkout and pricing include adjusted
  estimates, while onboarding is evaluated by Day-1 return.

## Vocabulary

- "The funnel" means the cart funnel report, not the onboarding funnel.
- "Conversion" without qualification means purchase conversion, not
  install-to-registration.
