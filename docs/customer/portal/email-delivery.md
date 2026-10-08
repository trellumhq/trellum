# Email delivery

Two independent things live under this heading: personal delivery schedules
("send me this report every Monday at 8am") and build failure notices
(automatic, zero-configuration mail when a report's build breaks). They
share a mailer and a "no login needed" design, but nothing else — a delivery
is something you set up; a notice is something that just happens.

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

From a report page, open the **Options** menu and choose **Deliveries**.
**My deliveries** (in your user menu) is the cross-studio list of every
schedule you have set up, with edit/enable/disable/delete inline.

Set:

- **Recipients** — see below
- **Frequency** — daily, weekdays, weekly (pick a day), or monthly (pick a
  day of month, 1–28, so nothing depends on how long the month is)
- **Send time** — hour and minute
- **Timezone** — an IANA zone. New schedules default to your browser's own
  zone; editing an existing one keeps whatever it was already set to.
  Switching the timezone in the form recalculates the shown hour so the
  actual send instant doesn't move — the form always shows what will
  actually be stored
- **Attach as PDF** — adds a PDF copy alongside the inline image
- **Enabled** — a disabled schedule stays configured but sends nothing

Any studio member can set up a schedule for themselves — creating a
delivery is not gated behind the developer role.

Use **Send sample** to get a copy right away without waiting for the next
scheduled run — useful for checking recipients and formatting before you
trust the cadence.

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
Choosing a recipient or group never grants report access. If the schedule's
owner loses access, sending stops while the schedule remains configured; it
can resume if the owner's access is restored.

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
save several named API connections under **System → Email delivery**, test each
one, and explicitly select one for all instance mail. The original SMTP or
`EMAIL_URL` route remains available. Only one route is active at a time.
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
