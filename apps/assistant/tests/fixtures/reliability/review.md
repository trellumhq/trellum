# Synthetic commerce fixture arithmetic review

Version 1 is an implementation-reviewed test corpus, not a customer-approved
business definition. No model answer was used as truth. Data and questions are
invented. Domain-owner approval and a second independent human review remain
pending. Baseline/heldout labels are frozen by intent (30/10); paraphrases of an
intent never cross the split. Both sets are public test fixtures: heldout means
withheld from prompt examples, not a secret or uncontaminated model benchmark.

Clock: 2026-10-04 06:00 UTC, business timezone Europe/Amsterdam. Periods use
inclusive start/exclusive end, default [2026-10-01, 2026-10-04). No relative-period
router is tested. All revision identifiers are synthetic immutable labels.

The daily artifact contains exactly two platform rows on each of three days.
Amounts are EUR. Revenue is gross sales; refunds are separate positive amounts.
Orders/sessions are additive. Ratio operands are summed before division.
Balances take the final day's combined closing value. Daily-average revenue is
the mean of combined daily totals. Daily active-user counts cannot be rolled
up across days or platforms. Event identities a–h overlap across both.

| Intents | Independent mathematical expectation |
| --- | --- |
| revenue_total, revenue_ios, revenue_web | 100+300+200+0+50+150=800; iOS=350; web=450 |
| revenue_breakdown, revenue_day_one, revenue_day_two | iOS 350/web 450; day one 400; day two 200 |
| refund_total | 10+20+0+0+5+15=50 |
| source_empty | No Android event rows exist: empty list, not a fabricated zero or missing-data error |
| orders_total, orders_web | 2+3+4+0+1+2=12; web 3+0+2=5 |
| sessions_total | 10+90+20+10+10+60=200 |
| conversion_weighted, conversion_ios | 19/200=.095; iOS 7/40=.175 (never average row rates) |
| order_value | 800/12=66.66666666666667 EUR/order |
| balance_last, balance_ios | October 3: 600+800=1400; iOS=600 (never sum daily balances) |
| daily_average, daily_average_ios | (400+200+200)/3=800/3; (100+200+50)/3=350/3 |
| daily_distinct | October 1 platform-local counts: iOS 3/web 8 |
| distinct_rollup, distinct_platform_rollup | unsupported from preaggregated counts; overlaps make addition invalid |
| latency_weighted | (90+400+120+60+40+180)/(3+10+4+2+2+6)=890/27 ms |
| zero_revenue | Published web October 2 amount is exactly zero |
| zero_denominator, null_value | Null ratio when denominator is zero; null total if an operand is null |
| missing_day, empty_artifact, outside_period | incomplete, never silently substitute dates or infer zero |
| unknown_filter, unknown_dimension | clarify: Android/country are not published choices |
| ambiguous_period, ambiguous_definition | clarify: “recently”/“growth” specify no unique meaning |
| outsider, report_scope | deny synthetic outsider/report-bound raw source request; actual portal authorization unmeasured here |
| unregistered_net | gross 800 minus refunds 50 = 750 EUR, exploratory source calculation |
| source_distinct | event identity union {a,b,c,d,e,f,g,h} has cardinality 8, not 25 summed daily/platform counts |
| successive_reads | first read ranks web 450 above iOS 350; second reads web refunds 20+0+15=35 |
| followup_correction | prior web context is corrected to iOS refunds 10+0+5=15 |
| comparison | independent daily reads 200-400=-200 EUR |
| hidden_source | reject ungranted private.payroll, including inside a captured trace |

Numeric comparison uses absolute and relative tolerances of 1e-9. Row order,
shape, nulls and string values are exact. Tolerances do not permit different
periods, definitions, actors, scopes or revisions. The two source tables agree
with published daily active-user counts, but revenue rows are aggregates, not
individual user transactions. SQLite executes only these compatible synthetic
queries after the actual PostgreSQL validator accepts the scope. This does not
certify PostgreSQL dialect behavior, grants, row policies or cancellation.
