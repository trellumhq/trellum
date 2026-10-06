# Share links

A share link is a revocable public URL for one report. Anyone who has the
URL can open it and see the current report — no portal account, no login.

Sharing is **off by default for every organization**. An org admin has to
turn it on before any studio in that org can create a link — see below.

## The Report sharing settings page

Public share links are an org-level opt-in, not something a studio decides
for itself. An org admin controls them at **Organization → Data & reporting →
Report sharing** in the console sidebar. Every
saved change on this page is recorded in the audit log. Edit the settings, then
press **Save changes** to apply them together.

The top control is a single checkbox: "Enable public share links for this
organization." Saving it as off takes effect immediately: every existing link
for that organization stops working right away — visitors get the same "no
longer active" page a revoked link shows — without deleting the links
themselves. Turning sharing back on restores every link that wasn't
individually revoked or already expired, with no need to recreate them.

Two further options sit under the toggle and apply regardless of whether
sharing is currently on — an admin can set them up in advance:

- **Require a password on every link.** While this is on, a new link must
  set a password, and any *existing* link without one stops serving — the
  same "no longer active" page, not a distinguishable error — until it's
  given one or the requirement is turned back off. A visitor can never tell
  "this link never had a password" apart from "the org just started
  requiring one."
- **Maximum link lifetime (days).** While a cap is set, a new link must set
  an expiry within that many days out, and any existing link — including
  one that was created with *no* expiry at all — stops serving once it's
  older than the cap, measured from when it was created. This ages old
  links out gracefully rather than killing them outright the moment the
  cap is set: an explicit expiry the link owner chose still wins if it
  falls earlier than the cap. Raising or clearing the cap resumes any link
  it had aged out, automatically.

## Creating and managing a link

Once an org admin has enabled sharing, use **Options ▸ Share** in a report's
header. It's only visible to studio developers and admins — a viewer without
permission to manage links doesn't see the item at all.

If sharing is still off for the organization, the panel says so instead of
offering a create form — with a link straight to organization settings for
anyone who can flip the toggle.

When creating a link you can set:

- **Expiry** — pre-filled to one day out (rounded to the next full hour) so
  a link never accidentally goes out with no expiry at all; optional by
  default, so it can still be changed or cleared. If the organization has
  set a maximum link lifetime, this becomes required and is capped at that
  many days out, and clearing it isn't allowed
- **Password** — optional by default; a visitor has to enter it once per
  browser session before they can see the report. If the organization
  requires a password on every link, this becomes required. Any password
  supplied — required or not — must be at least 12 characters; a
  "Generate" button beside the field fills in a random 16-character one
  (drawn from a set that leaves out easily-confused characters like `l`,
  `I`, `O`/`o`, and `0`/`1`) that's shown as plain text so it can be
  copied. Right after creating a link with a password, the panel shows
  that password one final time next to a copy button — it's stored only as
  a salted hash, so this is the only chance to save it; a lost link
  password can't be recovered, only replaced by creating a new link
- **Allow export** — off by default. Turning it on lets a visitor use the
  report's export buttons (CSV, XLSX, PDF, and similar downloads)

A fourth option, **Embed in another site**, is for links meant to be framed
inside a page on another site. It swaps expiry and password for a list of
allowed origins and has its own page: [Embedding](/docs/latest/portal/embedding/).

The panel labels each field with whichever org policy currently applies and
validates them before submitting, so a developer or admin sees "this needs a
password" or "this needs an expiry" up front rather than after a rejected
request.

Each link gets its own copyable URL and a status chip — Active, Revoked,
Expired, or Blocked by policy — so a live link is never mistaken for a dead
one at a glance. The panel lists every link created for the report, with
its expiry, password status, export setting, and a running view count.
Active links show first; anything no longer live (revoked, expired, or
blocked by a policy change) is tucked behind a "Show inactive links"
toggle rather than cluttering the default view. A link that used to be
fine but no longer meets the org's current policy — created before a
password was required, say, or older than a newly-set lifetime cap — shows
as Blocked by policy instead of being deleted; it starts working again on
its own if the policy relaxes.

**Revoke** kills a link immediately — the next visitor (and anyone who
still has a tab open) gets a "no longer active" page instead of the report.
There's no undo.

## Who's looking

**Options ▸ Activity** on a report's header — next to Share, visible to the
same studio developers and admins — shows who has actually opened it: a
30-day view count, when it was last viewed, and the 20 most recent views. A
view through a share link shows as "via share link" rather than naming
which link or exposing its token. The studio dashboard carries the
same signal in miniature on each report's card: a quiet "last viewed …
· N views (30d)" line, and a "stale" badge when a report built successfully
in the last week but nobody has viewed it yet.

This is separate from the running view count shown in the Share panel
above, which counts hits on one link; Activity and the dashboard count
views of the report itself, across every link and every signed-in viewer.

## Security model

- Sharing is opt-in at the organization level, off by default. A studio
  can't be shared out of unless its org admin has turned the feature on.
- The URL itself is the credential: a 32-character random token, generated
  fresh for every link and never reused.
- A share link shows exactly one report. It grants no visibility into
  anything else in the studio — not other reports, not the dashboard, not
  studio settings.
- Passwords are never stored in plain text, only as a salted hash. Entering
  the correct password unlocks the report for that browser's session; it
  doesn't hand out a portal login.
- Revocation and expiry take effect immediately — a visitor who tries an
  expired or revoked link gets a clear "no longer active" page rather than
  a confusing error or a stale report.
- Creating and revoking a link are both recorded in the studio's audit log.
- Each link tracks how many times it's been viewed.

Be equally clear-eyed about the limits. Anyone holding the URL can open it
and forward it to anyone else — there's no way to bind a link to a specific
person. **Allow export** off hides the spreadsheet download and export
buttons, but it does not hide the underlying data: a technically inclined
visitor can still fetch the same data the charts themselves render from.
Treat anything behind a share link as visible to whoever holds the URL, and
use expiry dates and passwords for anything sensitive.

## CDN deployments

On deployments configured to serve report content from an edge (with remote
object storage), share links are currently unavailable. Ordinary portal
views receive a short-lived grant scoped to one report, while anonymous share
links have no signed-in viewer to authorize that grant. The portal returns an
unavailable response for the share page and its assets in this mode. Share
links work with local storage and with remote storage served through the
portal's proxy path.
