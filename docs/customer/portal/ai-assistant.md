# The AI assistant

A panel in the corner of the portal that answers questions about your
reports in plain language. Someone asks "why did revenue drop last week",
and it points them at the report that covers it, names the chart, and gives
them the number — read out of the report's own already-built output.

It exists for the people who read reports rather than write them. The
analyst who built the report knows which one answers the question; everyone
else has to go looking, and mostly does not. That gap is the whole purpose.

It is off until an organization admin turns it on, and it needs your own
model provider key. Nothing about it runs, costs anything, or sends data
anywhere until someone deliberately configures it.

## What it can and cannot do

**It reads built report output.** When a report runs, it publishes its data
alongside its charts. The assistant reads that published data — filters it,
aggregates it, and quotes the result. It is the same data anyone with access
to the report can already see, reached a different way.

**It cannot reach your warehouse.** There is no database connection. If no
report carries the answer, the assistant says so rather than going to find
it. A question the reports cannot answer is a question for whoever builds
them.

**It cannot change anything on its own.** Out of the box it has four read
operations and nothing else: no creating reports, no editing them, no
changing settings, no running builds. An organization admin can let it
*propose* a short list of portal actions — configure or test a data source,
build a report, publish repository changes, set up an alert — and each one
waits for a person to approve it in the panel. It never edits the repository. See
[Actions and approval](#actions-and-approval).

**It only sees one studio.** A conversation belongs to the studio it was
started in, and the assistant reads that studio's reports and no others.
Someone who cannot open a report in the portal cannot get its numbers out of
the assistant either — access is checked the same way for both.

**It will not invent a number.** Every figure it states has to come from a
tool result in that conversation. When it cannot retrieve something, the
honest answer is the one it is instructed to give.

## Where your data goes

This is the question worth answering plainly.

When someone asks a question, the assistant sends the model provider:

- your instructions to it (`assistant.md`, see below);
- a catalog of your reports — names, descriptions, dataset names, column
  names and date ranges — and where the person is in the portal (page
  title, path, and the chart they asked about when they asked from one);
- the conversation so far;
- the results of whatever it looked up to answer — typically an aggregated
  table of a few dozen rows, not a raw export;
- when **Let the assistant read report source** is on (the default), a
  report's own source files (`report.yaml`, `queries.py`) if it chooses to
  read one to explain how a metric is computed. Turn it off and the
  assistant is told those files are unavailable.

It never sends data-source connection details (`data-sources/config.yaml`)
— that file is not readable by the assistant at all — and nothing from
outside the studio's project.

Everything the provider sees that came out of your reports or files is
marked as data, and the assistant is instructed to treat it as data rather
than as instructions. That is a mitigation, not a guarantee: a report
description that says "ignore your rules" is still text going to a language
model.

**Which provider is your choice, and so is where it runs.** The
organization supplies its own API key for Anthropic or OpenAI, and billing
is between you and them. Trellum never proxies the request, never sees the
key in the clear, and does not have an account with either provider on your
behalf.

**You can keep all of it on your own network.** Point the custom endpoint
URL at a model server or gateway inside your perimeter and nothing leaves
your infrastructure. See [Routing requests through your own
endpoint](#routing-requests-through-your-own-endpoint).

**Transcripts are stored, and they expire.** Conversations are saved so
people can come back to them, are private to the person who started them,
and are purged after `RETENTION_ASSISTANT_SESSION_DAYS` (180 days by
default). Spend records are kept indefinitely, because a deleted cost
history is an unauditable bill.

## Telling it about your data

The assistant can see the *shape* of your reports automatically — every
built report, its datasets, its columns, and the real date range each one
covers. It refreshes that itself when reports rebuild.

What it cannot see is everything your team knows that the data does not
say. Which revenue definition the business actually uses. That one dataset
is event-grained and counting its rows counts events rather than people.
That "the funnel" means one particular report. Without that, the assistant
gives answers that are defensible from the data and wrong in the room.

**Put it in `assistant.md` at the root of your analytics repository** —
beside `config.yaml`, `events.yaml` and `metrics.yaml`, not inside
`reports/`. Every question asked in that studio is answered with that file
in front of the model. It syncs like any other file in the repository: push,
and the next sync picks it up.

```
your-analytics-repo/
├── assistant.md        ← what the assistant should know
├── config.yaml
├── events.yaml
├── metrics.yaml
└── reports/
    ├── revenue-weekly/
    └── cart-funnel/
```

> **The location is not a convention, it is a constraint.** The portal takes
> a *sparse* checkout of your repository: only your reports directory and a
> short list of named root files ever reach the machine. A context file
> anywhere else — in a subdirectory, in `docs/`, next to a report — is never
> read, and nothing will tell you so. It has to be `assistant.md` at the
> root.

### What to put in it

Write the things that are true about your business and invisible in the
data:

- **Definitions that differ from the obvious reading.** "Revenue is gross,
  before store commission and before refunds. Finance's net revenue is a
  different number and is not in these reports."
- **Traps.** "The economy dataset is event-grained, not user-grained.
  Counting rows counts events, not people."
- **Vocabulary.** "'The funnel' means the cart funnel report. 'Conversion'
  without qualification means purchase conversion."
- **What is not real.** "The `insert-coin` report is built from synthetic
  data. Never quote its numbers as the business."

Do not restate what the assistant already reads. Listing your reports and
their columns adds nothing — it has those — and every line costs a little
of every answer.

A worked example ships with the product, at `assistant.md` in the demo
project.

### What it can and cannot change

The file shapes how the assistant *interprets* your data. It outranks the
assistant's own assumptions about what a metric means or what something is
called.

It cannot widen what the assistant may do. Instructions in it to write
something, read another studio, or state a number without a source are
ignored — those rules sit above it and are not yours to relax from a file in
a repository. Treat it as briefing material, not configuration.

### Keeping it honest

`assistant.md` is a document that claims things about data, which means it
can go stale the same way a comment does. When a metric definition changes,
the file is part of the change. A stale briefing is worse than none: it
makes the assistant confidently wrong, in your own words.

## Actions and approval

Off by default. With **Let the assistant propose actions** turned on (see
[Turning it on](#turning-it-on)), the assistant can also do the things the
portal itself does — and only those:

| Action | What happens on approval | Who can approve |
| --- | --- | --- |
| Configure a data source | Saves the credentials, runs the connection test and queues the reports that were waiting on it — the same path as the Configure form. | Studio admin; organization admin for credentials shared across studios |
| Test a data source | Re-runs the connection test and reports the outcome. | Developer |
| Build a report | Queues a build now instead of waiting for its schedule. | Developer |
| Publish repository changes | Publishes what the studio has fetched but not yet applied (manual publish mode). | Developer |
| Create or change an alert | Saves an alert rule — a report, plain-language instructions, a trigger, recipients and a cooldown — exactly as the studio's Alerts page would. | Developer |

**The repository is never edited.** A report's `report.yaml`, its queries,
validation suppressions and `metrics.yaml` stay a change you make in git.
When the fix is in the repository, the assistant explains which file and
which key; it does not make the change.

If you want to change report source from a local browser preview, use the
[coding-agent review workflow](/docs/latest/workflow/refine-reports/). The
portal assistant remains a Q&A and approved portal-actions feature.

The same actions reach your own coding agent over MCP, where there is no
card: the agent asks you before each call instead, and the key it uses must
carry `write` scope — see
[Working with AI agents](/docs/latest/workflow/working-with-ai-agents/#connect-it-to-the-portal).

**Nothing runs when the assistant asks.** Calling an action only creates a
proposal. A card appears in the panel saying what will happen and on what,
with **Approve** and **Reject**. The assistant is told that it has proposed
and waits; once you decide, it hears the outcome and carries on — re-tests
the source, tells you the build is queued. A card that nobody decides
expires after 24 hours and can no longer be approved.

**Secrets are typed into the card, never into the chat.** When an action
needs a password, key or token, the card has a masked field for it. What
you type there goes straight to the encrypted store, the same as the
Configure form; it never enters the conversation, the stored proposal, the
model's instructions or the audit trail. The assistant is instructed never
to ask for a secret in chat. If it ever does, do not type one — reject the
proposal instead.

**Your role at the moment you click is what counts.** Each action needs a
studio role (the table above). Someone without it never sees the action in
the first place, and a proposal made before a role changed is refused at
approval, not honoured because it was once allowed.

**Every approval is audited.** Two rows: the action's own — data source
updated, connection tested, run queued, publish requested, alert rule
created or updated — carrying the proposal id, and the decision itself
(approved or rejected, with the reason you gave).

**"Watch this for me."** Ask "is paid revenue down this week?", get the
answer, and then say "watch this for me and tell me when it drops again".
The assistant proposes an alert: the card shows the rule as it will be
saved — a name, the report, the instructions, when it is evaluated (after
every build of the report, or on a schedule), who is told and the
cooldown. The instructions carry what you were just discussing — the
report, the filters, the numbers — so the evaluator watches exactly that
and not a vaguer version of it. Approve, and the rule is live; it appears
on the studio's [Alerts page](/docs/latest/portal/alerts/) like any other,
where it can be tested and tuned. People named by email must already be
members of the studio; an address that is not is refused rather than
quietly dropped.

**From an alert back to the assistant.** Every alert email carries an
**Open in the assistant** link. It opens the report with a new
conversation seeded from that evaluation — the rule's instructions, the
decision, the title, the message, the evidence lines it cited and when it
ran — pinned above the transcript as "About alert: …", so your first
question ("why did organic drop, and was it every region?") runs against
the same report the evaluator looked at. The seeded lines are what the
alert said at the time, not live numbers: the assistant reads the report
again for anything it states. The conversation records which alert it came
from, so it is recognisable in your history later.

## Turning it on

**Organization settings → AI settings**, as an organization admin. These settings select the shared provider and model for portal chats and alert evaluations. If the assistant proposes an action, an organization member must approve it before it runs.

Choose **Anthropic, OpenAI, LiteLLM, OpenRouter, DeepSeek, Azure OpenAI,
Amazon Bedrock, Vertex AI**, or a **custom OpenAI-compatible endpoint**. One
saved connection serves both chats and alerts; gateways control their own
routing and fallbacks.

| Setting | What it does |
| --- | --- |
| Enabled | Off by default. Turn it on after choosing a usable connection and model. |
| Authentication | API key, gateway-managed authentication, cloud credentials, or operator-authorized workload identity, depending on the provider. |
| Credentials | Encrypted and write-only. Blank fields keep stored credentials only while provider, resolved endpoint, authentication mode and cloud identity are unchanged. Changing that identity requires replacement credentials. |
| Model | Anthropic defaults to `claude-sonnet-4-6`; OpenAI defaults to `gpt-4o`. Other providers require an explicit model ID. Azure requires a deployment name. |
| Discover models | Optional catalog lookup using the saved connection. Manual entry always works. Azure catalog names are not deployment discovery. |
| Let the assistant read report source | On by default. Off prevents reading `report.yaml` and `queries.py`; the assistant can still read built output. |
| Let the assistant propose actions | Off by default. Each proposed action waits for a person to approve it. |
| Monthly / per-user budget | Spend caps per calendar month. Blank means no cap. |
| Custom endpoint URL | Under *Advanced*. Required for LiteLLM, custom endpoints and Azure; optional for direct APIs. Bedrock and Vertex use native cloud endpoints. |
| Input / output price | USD per million tokens, both or neither. Required for budget enforcement when provider pricing is unknown. Zero and zero are valid for a free model. |

For a new connection, leave **Enabled** off, enter its endpoint and credentials,
then save. Discover or enter a model, enable AI, save again, and use **Test
connection**. Discovery failure does not block manual model entry. Settings
save in place and preserve drafts in neighboring forms.

Cloud connections use these fields:

- **Azure OpenAI:** an HTTPS Azure resource endpoint, deployment name, and
  either an API key or an Entra tenant ID, client ID and client secret.
- **Bedrock:** AWS region and an access key ID plus secret access key; a session
  token is optional. The model ID may also identify an inference profile.
- **Vertex AI:** Google Cloud project and location, plus service-account JSON.
  Only service-account credentials with the trusted Google token endpoint are
  accepted; external-account credential configurations are not accepted here.

Each cloud provider also offers **workload identity** when the instance operator
has authorized this organization for that provider. Organization admins cannot
add that authorization themselves. See [instance configuration](/docs/latest/install/configuration/#ai-workload-identities).
Missing organization credentials never silently select a server identity.

The page separates **Chat**, **Alerts**, and **Cost metering** capability checks
from the overall saved-settings readiness and model pricing. Chat can work when
an endpoint cannot force an alert tool call; alerts remain unavailable in that
case. New providers require verification before use. Existing native provider
configurations retain their established behavior until rechecked.

### Budgets

Spend is recorded per organization and per user, per calendar month, and
checked **before every model request and between the steps of a single
answer** — so a question that turns into a long chain of lookups stops at
the cap rather than discovering it afterwards.

A reached cap blocks new questions and explains why. It does not delete
anything, and the next calendar month starts clean.

Because the key is yours, these caps are Trellum's accounting of what it
spent on your behalf, not a limit enforced by your provider. Set a limit at
the provider too if the number matters.

### Routing requests through your own endpoint

Use **LiteLLM** or **Custom OpenAI-compatible endpoint** for a gateway or local
model server. The endpoint must support Chat Completions, streaming and tools.
**Anthropic** custom endpoints must support the Anthropic Messages format.
Select **No authentication** only when an explicit endpoint authenticates for
you or needs no key. Any API key supplied is sent to the selected endpoint;
changing the destination does not reuse a stored key.

**OpenRouter** and **DeepSeek** have preset endpoints and require a model ID and
API key. Cloud providers use their own authentication and endpoint rules.

Gateways and cloud providers do not inherit direct-provider prices from a model
name. Enter your actual input and output prices if needed. Without prices,
uncapped calls may run with unknown cost; unknown charges are excluded from
recorded totals. Budgeted calls require both pricing and verified token usage.
If a provider stops returning usage, further budgeted inference is blocked until
metering is checked again. Compare recorded estimates with provider billing.

**Test connection** uses only synthetic prompts and harmless synthetic tools.
It verifies streamed chat, a tool roundtrip, a forced alert tool, and token usage.
It uses the settings as last saved and does not execute portal actions or send
report data. A successful provider test can still leave AI unavailable because
it is disabled, lacks budget pricing, or cannot meter usage.

Results belong to the exact configuration revision tested. Saving a new model,
endpoint or credential clears old verification; pricing and budget changes keep
capability results. Delayed results from older connections are discarded.
Connection failures appear as safe messages; no credentials or signed cloud
URLs are shown.

## Who can use it

Any studio member who can view the studio. There is no separate permission:
if someone can open a report, they can ask about it, and if they cannot,
the assistant will not answer about it either.

Viewers granted selected reports use conversations bound to one allowed
report. Those conversations cannot read other reports or the studio-wide
briefing. Changing the report context requires a conversation for that report.
If a person's access is narrowed, older studio-wide conversations are hidden;
report-bound conversations are available only while their report remains
accessible. Restoring broader access restores access to the retained history.
The conversation keeps its original scope after a promotion: report-bound
conversations can propose running that report when the role permits it, but
cannot configure studio-wide data sources or publishing. Action approvals
also require the conversation to remain accessible.

Agentic alerts evaluate only their bound report, using their owner's current
access. Losing that access pauses evaluation and delivery; recipients must
still be able to view the report when an alert is sent.

It appears as a panel in the portal and on built report pages inside the
portal. Shared links and exported copies of a report do not carry it — a
report sent outside the portal has no assistant in it, because it has no
one authenticated to answer for.

## When it is not available

The panel explains itself rather than failing silently. It says the
assistant is not configured for the organization, or is disabled, or has no
API key, or that the budget cap is reached — each with the specific reason,
so an admin knows what to fix.

**The panel says the model provider returned an error.** The request left
the portal and came back broken. The panel shows a safe explanation of the failure. Organization admins can
check streaming, tool support and metering from AI settings. Check that the provider matches
the endpoint's dialect (an Anthropic provider needs an endpoint speaking the
Anthropic Messages API, not the OpenAI format), then run **Test connection**
on the settings page: it fails the same way, with the same detail, without
anyone having to ask a question.
