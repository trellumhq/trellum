# Email delivery

Two independent things live under this heading: scheduled report deliveries
("send this report every Monday at 8am") and build failure notices
(automatic, zero-configuration mail when a report's build breaks). Studio developers and admins can configure shared
schedules in each Studio’s **Deliveries** page. They share a mailer and a "no login
needed" design, but nothing else — a delivery is something you set up; a notice
is something that just happens.

Neither is an alert. An [alert](/docs/latest/portal/alerts/) watches a
report's *data* — "tell me if revenue drops" — and the AI assistant decides
when to write; it has its own page.

## What a delivery mail looks like

A scheduled delivery mails the report as an image: a snapshot of the
report's latest successful build, rendered and embedded inline so it reads
in the mail client itself — no login, no click required to see the numbers.
Every delivery mail also carries an **Open report** link to the live page,
for anyone who wants to drill in. Attaching a PDF copy is optional, set per
schedule.

If a schedule's report hasn't built successfully — the last run failed, or
it has never run — the recipient gets a short **Not sent** notice instead of
a stale or empty image.

## Creating a schedule

Open **Deliveries** in the Studio navigation and choose **Add delivery**.
Choose a report or analysis, enter its schedule and recipients, then **Save
delivery** to return to the list. **Edit** opens a dedicated page; **Cancel**
returns without saving. Failed validation keeps your draft. The report's
**Options > Deliveries** drawer also remains available.

Set:

- **Recipients** — see below
- **Frequency** — daily, weekdays, weekly (pick a day), or monthly (pick a
  day of month, 1–28, so nothing depends on how long the month is)
- **Send time** — hour and minute
- **Timezone** — an IANA zone. New schedules use organization defaults,
  inheriting instance defaults when blank (UTC on a fresh instance).
  Editing an existing one keeps whatever it was already set to.
  On the dedicated form, the hour and minute are stored in the selected
  timezone. The report drawer recalculates the hour when changing timezone.
- **Attach as PDF** — adds a PDF copy alongside the inline image
- **Enabled** — a disabled schedule stays configured but sends nothing

Developers and admins can create, edit, enable, disable and delete shared
schedules for reports they can access. Viewers can read every schedule for
their accessible reports, including disabled schedules and schedules that do
not address them. Each new schedule requires an explicit audience.

API clients must supply `recipient_ids`, `recipient_roles` or
`recipient_group_ids` when creating a schedule; an omitted or empty audience
is rejected. Schedule responses use `creator_name` for creation history and
`editable` for the caller's management permission. The schedule-list response
also includes `can_manage`, including when no schedules exist. `owner_id`
reassignment is unsupported.

Use **Send sample** to get a copy right away without waiting for the next
scheduled run — useful for checking recipients and formatting before you
trust the cadence.

## Managing shared deliveries

Schedules belong to the Studio. Developers and admins can manage every
schedule for reports they can access, regardless of who created it. Viewers
have read-only access. Support-operator access alone grants no schedule or
report-data access. Existing `/me/deliveries` bookmarks show accessible shared
schedules with links to Studio deliveries. Search or filter by report, creator
and enabled state. AI alerts have their own Studio **Alerts** page with rules
and run logs.

The Studio list shows next future occurrence, last attempt and transport outcome.
Accepted means the transport accepted the message; it does not prove inbox
receipt. Legacy delivery timestamps have an uncertain outcome. A missing-build
notice is not an accepted report delivery.

Organization admins, including admins granted through a permission group,
set pause and future schedule defaults in **Organization > Delivery settings**.
Instance superusers use **Instance settings > Scheduled deliveries** to set
instance-wide policy. Existing `/deliveries` bookmarks redirect to these settings.
This pauses scheduled reports and all AI-alert mail, including owner
budget warnings. Alert evaluations continue and consume their normal budget;
alerts with no accepted delivery do not advance cooldown. Instance pause
overrides organization resume. Build failure/recovery, account/security,
on-demand personal samples and explicit operator tests remain outside this
pause. Stored schedules stay intact. Resume uses future
occurrences only and sends no missed-email backlog.

The same controls set enabled state and timezone defaults for **new email
schedules only**. Blank organization defaults inherit the instance. Existing
schedules and AI alerts retain their configuration, and each new schedule can
explicitly override the defaults.

Schedules survive deletion, deactivation or role changes of their creator.
The creator is retained only as audit history and cannot be reassigned. The
schedule continues for its eligible recipients. AI alerts retain their
separate billing-owner and access requirements.

Delete confirmation identifies the schedule, report and recipients. Deletion
stops future sends; it cannot recall a message already accepted by a transport.
Configuration changes and deletion identifiers are recorded in audit.
Current permissions, recipients and pause state are checked again before each
message. A pause and resume during rendering or evaluation stops that old
attempt; the next future occurrence or a fresh alert evaluation can deliver.
Messages already in transport cannot be recalled, and an interrupted transport
may leave acceptance uncertain.

## Recipients

A schedule's audience is not frozen at creation time — it is resolved fresh
every time the schedule fires. It can be built from:

- Individually picked people
- Role chips — **All admins**, **All developers**, or **Everyone** in the
  studio
- Permission groups

Whichever mix you pick, the resolved list is always intersected with the
report's *current* permitted audience. A person picked individually, or reached
through a role or group, who has since lost access to the report simply stops
receiving the mail — nobody has to remember to edit the schedule when
someone's access changes. The flip side: someone who joins later starts
receiving it automatically too, with no edit required.

The recipient picker lists only people who can currently view the report.
Choosing a recipient or group never grants report access. Only active people
with current report access can receive a delivery, including a final check
immediately before sending.

## Unsubscribing

Every scheduled delivery mail carries an unsubscribe link. Following it
removes you from that schedule's individually-picked recipient list.

There's a known gap: if you're only reached through a role or group chip
(not picked individually), the unsubscribe link currently has nothing to
remove you from — the next send still resolves you back in. Getting off
that schedule for good means changing your studio role or your permission
group membership, not clicking unsubscribe.

## Build failure notices

Separate from deliveries, and not configurable: when a report's build
transitions from working to broken, every studio developer and admin gets a
mail. When it transitions back to working, they get a recovery mail. This
is operational duty mail, not a subscription — there's no per-report opt-in
and no unsubscribe link on it.

You get exactly one mail per transition, not one per failed run — a report
stuck failing for a week doesn't re-notify on every retry.

If several reports in the same studio break within a short window of each
other — a shared data source going down, a git-sync outage — the notices
coalesce into a single summary mail per recipient instead of a flood of
one-per-report mails. The summary lists only the broken reports that
recipient actually has access to.

A notice says the *build* broke. For the numbers inside a report that built
fine, see [Alerts](/docs/latest/portal/alerts/).

## Operator notes

Outgoing mail uses the instance's selected delivery route. An operator can
save several named API connections under **Instance settings → Email delivery**
(`/instance/settings#email-delivery`), test each one, and explicitly select one
for all instance mail. The original SMTP or `EMAIL_URL` route remains available.
Only one route is active at a time.
An active API error stops that send; it does not switch routes automatically.

The built-in API choices are SendGrid, Amazon SES, Mailgun, Postmark, Brevo,
Resend, Mailjet, MailerSend and Mailtrap Email Sending. Each asks for a verified
sender address and its own service credentials. SES can use the deployment's
AWS credential chain or separately stored access keys. SendGrid's integration
uses its Web API v3 directly; its Anymail integration is currently unsupported.
Brevo sends the PNG snapshot as a file attachment and labels it accordingly;
other built-in services retain inline snapshot presentation when supported.
Trellum caps the encoded request at 10 MB for the seven other managed HTTP
presets, 25 MiB for SendGrid JSON, and 28 MiB for Amazon SES raw MIME. Custom
HTTPS defaults to 10 MiB and can be set no higher than 25 MiB. These caps
include attachment encoding; your provider or account may impose a lower
limit. Managed HTTP and Custom HTTPS responses are read up to 64 KiB.

**Custom HTTPS** is for a public HTTPS POST JSON mail API. Configure the exact
endpoint, bearer/API-key-header/Basic authentication, optional encrypted extra
headers, typed JSON field mapping, attachment mode and accepted response rules.
The editor offers synthetic simple and report payload previews without sending
or showing credentials. It does not support private-network endpoints,
non-443 ports, OAuth refresh, signing, multipart upload or raw MIME. A saved
active custom connection can rotate credentials or change its display name;
create and test an inactive copy to change its endpoint or send contract.

The editor starts with an object-shaped example. A gateway expecting address
strings and a different content structure can instead use a mapping like:

```json
{
  "from": {"$value": "message.from.formatted"},
  "recipients": {"$each": "message.to", "$template": {"$value": "item.address"}},
  "carbon": {"$value": "message.cc", "$omit_if_empty": true},
  "blind": {"$value": "message.bcc", "$omit_if_empty": true},
  "reply": {"$value": "message.reply_to", "$omit_if_empty": true},
  "title": {"$value": "message.subject"},
  "content": {
    "plain": {"$value": "message.text"},
    "rich": {"$value": "message.html"}
  },
  "files": {"$value": "message.attachments"}
}
```

These keys are examples, not provider field names. Use your service's JSON
contract and preview both message types before a test send. The typed mapping
does not expand strings or run expressions.

**Send simple test** and **Send report test** address only the current operator.
The report test includes synthetic image and PDF content. A test result belongs
to the shown configuration revision; an older result is marked stale. “Accepted”
means the service accepted the request, not that the recipient received it.
Changing routes does not resend previously failed messages. See the
[Configuration reference](/docs/latest/install/configuration/) for the setup
route and SMTP fallback.

Rendering the inline snapshot needs a headless Chromium available via
Playwright in the runtime image; if that's missing, delivery mail still
sends, just without the embedded image.
