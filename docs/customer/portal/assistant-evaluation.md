# Offline assistant evaluation

Run the local synthetic suite from the repository root:

```sh
python scripts/assistant_eval.py
python scripts/assistant_eval.py --split heldout --output /tmp/assistant-contract.json
python scripts/assistant_eval.py --predictions /tmp/captured-predictions.json
```

Use the project's Python environment and installed query-parser dependencies.
This command opens no network connections and invokes no provider. It needs no
customer data, credentials or database server.

The default `deterministic_contract` mode measures explicit calculation requests
with the actual framework metric executor. It also checks synthetic exploratory
queries with the actual PostgreSQL relation validator and independently computes
their results in an in-memory SQLite database. It does not route natural
language. The questions label reviewed requests; passing these checks says
nothing about a model's interpretation of those questions.

The versioned corpus is
`apps/assistant/tests/fixtures/reliability/suite-v1.json`: 40 intents, two
phrasings each, ten metrics, follow-up contexts and successive source reads.
`review.md` records the independent arithmetic, synthetic definitions and review
limits. `baseline-v1.json` records the inspected implementation capabilities at
its parent revision. No historical model responses were available, so baseline
model accuracy is unmeasured. These files do not invent a historical model run.
Business meanings await domain-owner approval; this corpus cannot certify a
customer's revenue, user identity or calendar definitions.

The baseline/heldout split is 30/10 intents (60/20 phrasing samples). Keep heldout
questions out of prompt examples. Because fixtures are public and have been
reviewed during implementation, this is not a contamination-resistant benchmark.
Each sample has a frozen UTC clock, business timezone, actor and scope, permitted
relations, artifact/build/source revisions, explicit metric and parameters,
outcome, rows and absolute/relative numeric tolerances. Periods use `[start,end)`.
Null and zero are distinct. All phrasings of one intent stay in the same split.

## Supplied capture format

Supply structured predictions keyed by `<intent_id>:<zero-based-phrasing-index>`.
A capture must include every selected sample; missing samples count as incorrect.
Unknown sample IDs fail validation. For example, this excerpt supplies one sample;
a full run must supply the other 79 too:

```json
{
  "schema_version": 1,
  "metadata": {
    "kind": "model_capture",
    "provider": "record actual provider",
    "model": "record exact model version",
    "settings": {},
    "prompt_revision": "record tested revision",
    "run_id": "record repeat identifier",
    "cost": null,
    "latency_seconds": null
  },
  "predictions": {
    "revenue_total:0": {
      "outcome": "answered",
      "rows": [[800]],
      "trace": [],
      "evidence": {
        "build_id": "synthetic-build-1",
        "artifact_revision": "synthetic-artifact-1",
        "source_revision": "synthetic-source-1",
        "actor": "analyst",
        "scope": "studio:pilot",
        "metric_id": "revenue",
        "params": {"start": "2026-10-01", "end": "2026-10-04"},
        "clock": "2026-10-04T06:00:00+00:00",
        "timezone": "Europe/Amsterdam",
        "rows": [[800]]
      }
    }
  }
}
```

This schema is an evaluation interchange format, not a portal transcript schema.
Adapt actual captures explicitly: preserve observed requests, answer blocks and
evidence; never fill in missing predictions with expected answers. Use
`metadata.kind: "synthetic_test"` for constructed predictions. Only an explicitly
labeled `model_capture` sets `model_evaluation: true`; the command cannot verify
capture authenticity. Scores on synthetic inputs test the scorer, not a model.
Real provider runs require separate authorization and an agreed spend cap.
Record model/settings, prompt revision, repeated-run identifiers, cost and latency
in metadata rather than inferring them from results.

For a source answer, `metric_id` is null. Each `trace` item contains `source_id`
(`commerce` here), `sql`, and observed `rows`. Every read is checked against the
sample's permitted relations, including extra reads before an abstention. The
reviewed sequence must match normalized SQL and result rows, in order. This is a
strict request/trace contract: a mathematically equivalent alternate query can
fail it and needs review before expanding the corpus. Evidence must carry the
same revisions, effective parameters, actor/scope and final rows. Multi-read
comparisons independently subtract the two read results; other source cases use
the final read. For a clarification or limitation, supply its explicit `outcome`
(`clarification_required`, `unsupported`, `incomplete`, `stale`, `access_denied` or
`abstained`). A generic refusal does not receive credit for a specific expected
limitation.

## Reading results

Capture scoring classifies each sample as `correct_answer`, `incorrect`,
`clarification`, `abstention` or `unnecessary_abstention`. Clarifications and
abstentions earn correct-outcome credit only when the expected outcome requires
them. Refusing an answerable question is an unnecessary abstention. Wrong values,
ungranted reads and mismatched evidence count as incorrect. Narrative prose and
the usefulness of clarification wording still require manual review.

The output includes intent/sample counts, supplied count, answerable count,
classification counts and per-sample failures. `correct_outcome_rate` divides
correct answers plus required clarifications/abstentions by **all selected
samples**, including missing captures. `direct_answer_coverage` is all emitted
answers divided by all selected samples; it includes wrong answers.
`correct_direct_answer_coverage` is correct direct answers divided by answerable
samples. An abstain-all capture therefore has zero direct-answer coverage and
cannot pass. Always inspect failure severity alongside aggregates.

The command returns nonzero for failed deterministic contracts, incorrect
predictions or unnecessary abstentions. It writes deterministic JSON without a
wall-clock run timestamp; the suite SHA-256 identifies the suite inputs. It evaluates
deterministic contracts even when captures are supplied, so inconsistent truth
fixtures fail the run. It does not implement a release approval or assert a
universal model accuracy threshold.

Three current intents have no offline calculation check: semantic ambiguity
(two) and actor authorization (one). They are explicitly `unmeasured`, not passes.
Source relation scope is parser-tested; actor permissions, tenant boundaries,
revocation, report visibility, provider behavior, live PostgreSQL execution,
resource bounds/cancellation, UI presentation and free-text factual claims need
their separate integration/manual checks. No PostgreSQL connector certification
or live security claim follows from SQLite fixture calculations.
