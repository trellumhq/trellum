# Embedding

An embed link is a share link made to be framed: it shows one report inside
a page on another site — your own product, an intranet, a customer portal —
in an `<iframe>`. It never expires and takes no password. In their place it
carries a list of the sites allowed to frame it, and the browser refuses to
show it anywhere else.

Everything on [Share links](/docs/latest/portal/share-links/) applies here
too: the report is the current build, the URL is the credential, revoking is
immediate, links are listed and audited in the same panel. This page covers
only what embedding adds.

## Turning it on

Embedding is a second org-level opt-in under the first. At **Organization
settings → Report sharing** an org admin turns on both:

- **Enable public share links for this organization**, and
- **Allow embed links.**

Turning **Allow embed links** off stops every existing embed link in the
organization at once, without deleting them; turning it back on restores
them. The password and lifetime policies on the same page don't apply to
embed links — see the trust model below.

## Creating an embed link

In a report's header, **Options ▸ Share**, tick **Embed in another site** in
the create form. The expiry and password fields switch off and a field for
**allowed origins** takes their place: one origin per line, in the form
`scheme://host[:port]` — `https://app.example.com`,
`http://localhost:8070`. A single `*` allows any site to frame the report.

Two more choices sit under the origins, and both belong to the link rather
than to the page that frames it:

- **Theme** — the report renders in the theme you pick here, whatever the
  visitor's browser last stored for another report on this portal. The list
  offers exactly the themes this report has, and **Studio default** leaves it
  following the studio, as it does today. A host page can still override it
  at runtime with a `trellum:theme` message.
- **Hide the filter bar** — the embedded report shows the data it was built
  with and no filter controls, for a host that wants a static panel rather
  than something its visitors change.

Neither appears on an ordinary share link: there is no host page to render
for, so they would promise something the link never delivers.

Once created, the link's row shows an **Embed** chip and its origins where
other links show expiry and password, plus a **Copy embed code** button that
copies a ready-to-paste snippet:

```html
<iframe src="https://portal.example.com/share/<token>/" style="width:100%;border:0;min-height:480px" loading="lazy" title="Fleet analytics"></iframe>
<script>
(function () {
  var f = document.currentScript.previousElementSibling;
  var portal = new URL(f.src).origin;
  window.addEventListener("message", function (e) {
    if (e.source !== f.contentWindow || e.origin !== portal) return;
    if (e.data && e.data.type === "trellum:height") f.style.height = e.data.height + "px";
  });
})();
</script>
```

The `src` is the ordinary share URL. It opens in a plain browser tab too —
the same report, without its header.

The row also has a **Preview** button. It reveals the link's own URL in a
small frame inside the panel, so you can see what a host page will get —
theme, hidden filter bar and all — before you hand the link over. Clicking
it again collapses the frame, and opening one preview closes any other.

The script is what keeps the frame the right size: an embedded report shows
no scrollbars of its own and announces its height instead, so without a
listener a tall report is cut off at the `min-height` with nothing to
scroll. It sizes the iframe immediately before it, so you can paste the
block more than once on the same page and each embed follows its own
report. Paste both parts, and keep them next to each other.

## The trust model

An ordinary share link is protected by what it carries: an expiry, maybe a
password. An embed link is protected by where it may appear. Each link
stores the origins allowed to frame it, and the portal enforces that list
in the browser with a `Content-Security-Policy: frame-ancestors` header on
every response the link serves. A page on any other origin gets an empty
frame, not the report. The portal's own pages are on that list too — that is
what the share panel's **Preview** frames — but no site you did not list is.
Ordinary share links, and every other portal page, stay un-frameable.

So an embed link has no expiry and no password, and the org's **Require a
password** and **Maximum link lifetime** policies skip it. To stop one,
revoke it, or turn **Allow embed links** off for the whole organization.
Either way the framed report goes blank on the next load; opened directly,
the URL shows the same "no longer active" page a revoked share link does.

An origin is `scheme://host[:port]`, exactly as it appears in the host
page's address bar — no path, and `http://localhost:8070` and
`http://127.0.0.1:8070` are two different origins. `*` is fine for a demo;
for anything sensitive, list the real sites.

The limits of share links apply unchanged. Anyone holding the URL can open
it in a plain tab — the origin list restricts framing, not fetching — and
**Allow export** off hides the export buttons but not the data underneath.
Treat anything behind an embed link as visible to whoever holds the URL.

## What the framed report looks like

An embedded report renders chromeless: the report header — title, theme
picker, export and share menus — is hidden, so the host page owns the title
and navigation around it. Everything below the header renders as it does
anywhere else, in the theme the link carries and without the filter bar if
the link asked for that. The frame never shows scrollbars of its own, so size the
iframe from the `trellum:height` message (as the snippet's host code does)
rather than giving it a fixed height. The host page can also push its own
palette into the report with a `trellum:theme` message, so the report's
backgrounds, text and accents match the console it sits in — see
[Talking to the frame](#talking-to-the-frame).

Embedding is available in every installation. The `/system` page lists the
effective security and storage settings.

## Always current

An embed link serves the report's latest build on every load, exactly as a
share link does. While the page stays open, the embedded report also checks
the report's build metadata once a minute and reloads itself when a new
build has landed — a dashboard left on a wall picks up the next scheduled
run without anyone refreshing it. A reload resets any filters the visitor
had set.

## Talking to the frame

The embedded report and the page around it talk through the browser's
`postMessage` API. Every message is a plain object with a `type` key.

From the report to the host page:

| Message | When |
|---|---|
| `{ "type": "trellum:ready" }` | The report has loaded and is listening. |
| `{ "type": "trellum:height", "height": 1234 }` | The content height changed, including on first load. Set `iframe.style.height` from it to avoid a scrollbar inside the frame. |

From the host page to the report:

| Message | Effect |
|---|---|
| `{ "type": "trellum:theme", "theme": "trellum dark", "vars": { … } }` | Switches the report's theme, and optionally overrides its CSS variables so it takes your own palette. `theme` is one of the report's registered theme names — the built-in ones include `trellum light`, `trellum dark`, `light` and `dark`; an unknown name is ignored. `vars` is an object of theme variables to set: hosts typically send `--bg-main` (page background), `--bg-card` and `--bg-card-hover` (panels), `--bg-header` (header accent), `--text-main`, `--text-secondary`, `--text-muted`, `--border-color`, `--accent-blue` (links) and `--shadow`. Names and values are validated, and anything unrecognised is ignored. Chart **series** colours come from the theme's own palette, so `vars` restyles surfaces, text and accents but leaves the series colours to the chosen `theme`. |
| `{ "type": "trellum:reload" }` | Reloads the report now, rather than waiting for the build watch. |

The report only acts on messages whose origin is on the link's allowed
origins. Address your own messages to the portal's origin —
`new URL(iframe.src).origin`, never `"*"` — and ignore any incoming message
whose `source` isn't the iframe's `contentWindow` or whose `origin` isn't
the portal, exactly as the copied snippet's listener above does. Height is
already handled there; a theme push looks like this:

```js
const iframe = document.querySelector('iframe');
const portal = new URL(iframe.src).origin;

iframe.contentWindow.postMessage({
  type: 'trellum:theme',
  theme: 'trellum dark',
  vars: {
    '--bg-main': '#0f1419',
    '--bg-card': '#161d25',
    '--bg-header': '#2fbf95',
    '--text-main': '#e6edf3',
    '--text-muted': '#93a1b0',
    '--border-color': '#27313d',
  },
}, portal);
```

## CDN deployments

Embed links inherit the share-link limitation on deployments that serve
report content through a CDN: they're disabled there, and a visitor gets an
unavailable message instead of the report. See
[Share links → CDN deployments](/docs/latest/portal/share-links/#cdn-deployments).

## A worked example

The repository ships a small host page under `examples/embed-host/`: a
fictional product page that frames an embed link, follows its height,
re-themes it from a light/dark toggle, and lights a "live" indicator on
`trellum:ready`. Its README explains how to run it against a local portal
on a second port, so the two are genuinely different origins.

## The `<trellum-report>` element

Everything above — the iframe, the height listener, the origin checks, the
theme message — is the same twenty lines on every host page. The portal
ships them as a custom element so you don't write them at all:

```html
<script src="https://portal.example.com/api/reports/embed-element.js"></script>
<trellum-report src="https://portal.example.com/share/<token>/"></trellum-report>
```

That is the whole integration. The element creates the iframe, follows
`trellum:height`, and only acts on messages that come from that frame and
from the portal's own origin. It's a plain script tag, not a module, and has
no dependencies; if your host page sets its own `Content-Security-Policy`,
allow the portal's origin in `script-src` and `frame-src`.

### Attributes

| Attribute | Default | What it does |
|---|---|---|
| `src` | — | **Required.** The embed link's URL, exactly as the **Copy embed code** button gives it. The portal's origin is derived from it. |
| `theme` | — | A registered theme name — `trellum light`, `trellum dark`, `light`, `dark`. Sent as a `trellum:theme` message once the report is ready. Omit it and the report keeps its own theme. |
| `min-height` | `480` | Height in pixels before the first `trellum:height` arrives, so the page doesn't jump on load. |
| `title` | `Embedded report` | The iframe's accessible name. Set it to the report's name. |

All four can be changed after the fact — set `theme` from a light/dark
toggle, or `src` to swap reports — and the element follows.

### Events and methods

The element emits DOM events, so the host page can react without knowing
the message protocol:

| Event | Detail |
|---|---|
| `trellum:ready` | — Fired when the report has loaded and is listening. |
| `trellum:height` | `{ height }` — Fired on every resize, after the element has already applied it. |

`reload()` reloads the report now, rather than waiting for the once-a-minute
build watch.

```js
const el = document.querySelector('trellum-report');
el.addEventListener('trellum:ready', () => console.log('live'));
document.querySelector('#refresh').onclick = () => el.reload();
```

Any number of elements can sit on one page; each tracks its own frame.

Reach for the element when the host page is ordinary HTML you control.
Reach for the plain `<iframe>` above when you need something the element
doesn't expose — a `vars` palette push, a sandbox attribute, or a framework
that wants to own the element itself.
