# Assistant answer delivery contract

Portal chat uses the internal `submit_answer` orchestration tool before delivering
an answer. Tool reads remain autonomous. Alert `force_tool` execution and the
public MCP toolbox retain their existing contracts.

## Analytical text and evidence

An analysis submission contains readable `text`, an `interpretation` (question
summary and `exploratory` or `defined` approach), `intended` context per referenced
execution, cell `claims` and the business-analysis `checks`.

Each claim has a unique bounded `id`, actual tool-result ID and evidence hash,
zero-based row, column name and `cell` or `complete_total` scope. An optional
`expected` value is reconciled with the actual cell; NULL and empty results cannot
become zero. Optional legacy claim `context` must match the full actual context
exactly. New claims need not duplicate that context.

Text uses `{{claim_id}}` for a server-rendered cell, `{{label:claim_id}}` for its
column name and `{{context:claim_id:field}}` for supported executor metadata, such
as `period`, `currency`, `filters` or `unit`. Every declared claim must appear as
a value placeholder. Unknown or malformed placeholders are rejected. Values,
labels and metadata are escaped as literal Markdown text, including table pipes,
links, HTML and line breaks. Evidence identities, SQL and full execution-context
JSON stay in the existing technical evidence UI rather than answer prose.
Derived differences or percentages also require tool-calculated cells rather
than model-authored arithmetic.

For example, synthetic model text:

```text
Revenue is {{revenue}} {{context:revenue:currency}} for {{context:revenue:period}}.
Refund treatment still needs confirmation.
```

can render as:

> Revenue is 42 EUR for 2026-10-01 to 2026-11-01 ([start,end)).
> Refund treatment still needs confirmation.

The answer also renders source/date/filter/unit metadata, the question
interpretation and every check's actual rationale. The answer explicitly labels
qualitative explanation and business judgments as model assessments.

## Intended context and business checks

`intended` identifies the requested source/report and canonical metric, when
used, separately from actual executor context. Supplied period, date range,
filters, timezone, unit/currency, dataset and grouping/aggregation fields must
match available executor metadata exactly. Absent fields remain unknown;
absence does not prove a match. This compares two structured descriptions. It
does not independently extract the user's intended meaning from natural language.

Every check requires a distinct, bounded rationale. Question interpretation and
cell reconciliation must be assessed with evidence covering every claimed
execution. Business meaning, assumptions, source suitability and data quality
cannot be dismissed as inapplicable. Grain/units remain applicable analytical
checks even when metadata is missing. Period and filters may be inapplicable only
when corresponding intended and actual fields explicitly establish no date scope
or filters. Missing metadata, including source SQL semantics, means unknown.
Unknown checks describe what remains unresolved, rather than claiming a
pass. Length and duplicate-note checks reject empty/perfunctory submissions;
they do not establish the truth or quality of the model's reasoning.

An assessed canonical business-meaning check requires a defined interpretation
and the matching intended metric identifier. Canonical evidence establishes the
executor's applied definition, not its equivalence to the user's business
meaning. Source SQL leaves semantic period/filter/unit checks unknown. SQL intent
equivalence remains unknown. Exploratory source answers require no new canonical
binding. A complete total still requires untruncated, single-row structured
aggregation evidence; source SQL cell observations cannot certify complete totals.

## Conversational text and numerical guard limits

Clarification, limitation, greeting and help modes accept bounded specific text.
For example: “Should revenue mean booked sales, invoiced sales or collected
payments? Should refunds and tax be excluded?” These modes use an assistant-authored
or not-independently-verified label and cannot reference analytical claims.
Action replies still describe only an actual recorded proposal.
Help may discuss the quoted “Approved calculation” label as a documentation topic;
that does not apply the label to its own conversational answer.

All model-authored visible text, including interpretation and check rationales,
passes a conservative lexical guard against unmapped numerical literals, common
English spelled numbers, numerical comparison idioms and assurance words.
Dates and numeric unit names use metadata placeholders in analytical text; they
are not blanket exceptions for digit-shaped text. Numerical examples in help
must use evidence-backed analytical mode rather than conversational mode.

This is a bounded compromise: lexical checks do not prove natural-language
semantics. They cannot exhaust every language, idiom, obfuscation or misleading
qualitative statement. They can reject harmless numerical documentation text.
Nor do placeholders prove that an adjacent metric name, comparison or causal
explanation accurately describes the referenced cell. The model can still
misinterpret intent or choose the wrong definition while submitting internally
consistent fields. These limitations are why model prose and self-assessments
receive no verification assurance and why users should inspect evidence for
consequential decisions. There is no second model acting as a judge.

## Bounds and continuation privacy

Submissions and rendered answers are capped at 64 KiB; text at 12,000 characters,
interpretation at 1,000, notes at 500, claims at 200, intended contexts at 32 and
references per check at eight. Existing evidence limits (200 rows/columns,
bounded bytes/catalog/history) remain. Placeholder expansion checks its byte
budget during rendering. These bounds permit grouped comparisons beyond a small
sample without copying full execution context into every claim.

Three failed answer submissions/drafts exhaust the question's correction budget.
That budget persists across durable source-read resumes; a new user question
starts a fresh review. Current permissions are checked again before delivery.

Submission arguments and drafts are excluded from outward transcript/history,
SSE and personal exports. Original signed/native provider blocks remain in the
provider-private state to preserve continuation, including original submission
arguments where the provider's replay format requires them. This is necessary
provider protocol state, not outward answer history. Synthetic tests exercise
signed replay, multiple tool batches, correction and a JSON-reloaded follow-up,
as well as source wait/resume, revocation and export boundaries.
The latest interpretation and intended context are retained in the private
review state; they are not added to exported protocol history.
