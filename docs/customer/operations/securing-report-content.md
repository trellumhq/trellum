# Securing report content

Built report output — the HTML pages and the `data.json` behind them — is your
customers' data. Who can read it comes down to a single choice:
`TRELLUM_REPORT_ACCESS_MODEL`. The default is safe on its own; the moment you change
it to serve content from a CDN, **sealing the storage becomes your
responsibility**, and this page is about getting that right.

## The three access models

`TRELLUM_REPORT_ACCESS_MODEL` decides *who ships the report bytes to the browser* —
and therefore who is responsible for keeping them private. In every mode the
portal runs the **same permission check** when a viewer opens a report; the
modes differ only in what happens after that check passes.

| Mode | Who serves the bytes | Who authenticates | Bucket exposed? |
|---|---|---|---|
| `proxy` (default) | the portal itself | the portal's own permission check | no |
| `edge-signed` | a CDN/edge worker in front of the bucket | the portal issues a short-lived signed grant; the edge verifies it | yes |
| `edge-external` | a CDN/edge in front of the bucket | **your own** identity layer (Azure AD / Entra, an identity-aware proxy) | yes |

- **`proxy`** is the default and needs no CDN. The portal reads each report from
  the bucket (or local disk) with its own credentials and serves it behind its
  login. Nothing is ever reachable from a browser directly.
- **`edge-signed`** moves the bytes off the portal so a burst of viewers doesn't
  tie up the web tier. The portal signs a short-lived grant scoped to one report
  and the edge worker checks it. Best when the portal is the sole authority for
  who sees what.
- **`edge-external`** also serves from the edge, but you have already put your
  own identity layer in front of the bucket — so the portal issues no grant and
  defers to that layer.

The last two both put the bucket where a browser can reach it, which is where
the rest of this page comes in. The exact settings for each live in the
[configuration
reference](/docs/latest/install/configuration/#serving-report-content-the-access-model).

!!! warning "`edge-external` authenticates, but does not enforce the portal's access control"
    `proxy` and `edge-signed` preserve **who may see which report**. In
    `edge-signed` the portal issues a grant only for the report a viewer opened,
    so another report stays out of reach — and
    sharing the link does not help them, because the grant is an `HttpOnly`,
    short-lived, report-scoped cookie, not something in the URL.

    `edge-external` is coarser. Your identity layer usually checks only *"is
    this a valid org member?"* — it knows nothing about portal studio roles. So
    **anyone it admits can read every report in the bucket**, and a shared link
    works for any colleague who can sign in. Choose it only when "any
    authenticated org member may see any report" is acceptable, or when your
    identity layer itself enforces the finer rules. To hide a report from some
    people **inside** your organization, including Private items, use `proxy`
    or `edge-signed`.

Private report and analysis audiences require the portal's selected-access
enforcement to be ready. Keep the existing
`TRELLUM_REPORT_SCOPED_ACCESS_READY` rollout guard in place when deploying
this enforcement. `edge-external` cannot enforce private audiences and fails
closed when they are present. In `edge-signed`, internal audience changes
take effect through the portal's normal authorization checks; a grant already
issued at the edge can remain valid until its cookie expires, so the cookie
TTL is the revocation delay.

This internal audience check does not revoke explicit public share links or
embeds. Those are separate publication paths; review and revoke active links
on the item's Access page when the content must no longer be available there.

## The default is safe — you may be done already

Out of the box (and whenever `TRELLUM_REPORT_ACCESS_MODEL` is left at `proxy`), the
portal serves every report byte itself, behind the same permission check that
guards the rest of the app. The bucket, if you use one, is never reachable from
a browser — the portal reads it server-side with its own credentials. There is
**no public surface to misconfigure**.

!!! note "If you run a single VM, or object storage in proxy mode, stop here"
    You have nothing to seal. Report content is only ever served through the
    portal's own authentication. The rest of this page applies only if you put a
    CDN or edge in front of the bucket.

## When you serve content from an edge, you own the lock

Both edge modes put the bucket where a browser can reach it — that is what buys
you the scale. The trade-off is the whole point of this page:

!!! danger "The bucket must never be readable without authentication"
    In an edge mode, the bucket — or the CDN in front of it — faces the
    internet. If it is left **publicly readable**, anyone with a URL can read
    report data, and **no portal setting can prevent it**: the exposure lives in
    your cloud account, not in the app. Sealing it is your job, not the
    portal's.

What "sealed" means, concretely: an anonymous request — no credentials, no
session, no signed grant — must be **refused**. Not "returns a 404 because the
key is unknown"; refused with a `403`, because the store declines to serve
anonymous callers at all.

## The safety net: the portal refuses to run exposed

You should not have to *hope* you sealed it correctly, so the portal checks your
work and fails loudly when the answer is wrong:

- **`manage.py doctor` — the `report access` check.** In any edge mode it fires
  an anonymous, credential-less request at your content origin and **fails hard
  unless it is refused.** A green line means a stranger genuinely cannot read
  your reports; a red line means they can, and tells you so in plain words.
- **At runtime, it refuses rather than leaks.** If the portal cannot prove a
  stranger is denied, it returns `503` for report content instead of handing out
  a URL to an open bucket. It never silently falls back to serving the bytes
  itself — that would just move the problem.

!!! warning "A net, not a substitute"
    The check catches the mistake; it does not seal the bucket for you. Treat a
    red `report access` line as "report data is currently public," and fix it
    before anyone can reach the install.

**Run `doctor` before you point viewers at an edge install, and keep it green.**

## Checklist by mode

### proxy (the default)

- Nothing to seal — the bucket is never exposed.
- Defence in depth anyway: confirm the bucket blocks public access, so a
  future change can't quietly expose it.
- `doctor` should read: `report access: proxy … nothing is exposed`.

### edge-signed

- The bucket blocks **all** public access — only the edge worker may read it
  (e.g. a bound R2 bucket, or S3 with Origin Access Control).
- The worker is deployed with the **public** key and refuses any request
  without a valid grant.
- `manage.py doctor` → `report access` is **green**.
- Remember the trade-off: `TRELLUM_CDN_COOKIE_TTL_SECONDS` is also your revocation
  latency. Keep it short.
- Before setting `TRELLUM_REPORT_SCOPED_ACCESS_READY=true`, deploy this version
  to every web and worker process, then wait one full configured cookie TTL
  (3600 seconds by default). This expires grants minted by an older version for
  a whole studio.

### edge-external

- Your identity layer (Azure AD / Entra, an identity-aware proxy) is
  **actually** in front of the content origin, not just planned.
- An anonymous request is redirected to sign-in, or refused — never served.
- `manage.py doctor` → `report access` is **green** (it treats a redirect to
  auth as protected, and a `200` as a leak).
- Selected-report permissions are unavailable because the external identity
  layer cannot enforce portal report assignments or Private item audiences.
  Existing selected grants or Private items make `doctor` fail, and the portal
  refuses report bytes.

## Verify it yourself

Trust, then check. From anywhere with no portal session, request a report
content URL directly and confirm you are turned away:

```bash
# Should return 403 (or a redirect to your identity provider), never 200
curl -si https://reports.example.com/content/<org>/<studio>/<report>/builds/x/index.html \
  | head -1
```

If that ever returns `200` with report HTML, your storage is public — seal it
immediately. The portal's `doctor` check runs this same test on a reserved probe
path so you don't have to remember to; this is how you confirm it by hand.

## What is *not* affected

Uploaded data-source files and git checkouts never travel through the edge —
they live on the data volume and are read only by the portal and the runners.
Only *built report output* is served this way, so only it is in scope here.
