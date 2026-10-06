# Embed host demo

A single-file mock-up of a fictional product's operations console
("Northwind Fleet") that frames an embed link the way a customer's own site
would: the report sits flush on the console's content background as one of
its screens, its height follows the content, a light/dark toggle re-themes
both the console and the report — pushing the console's palette into it — and
a "live" pill lights up on `trellum:ready`. The report URL lives behind the
gear icon in the top bar, out of the way of the mock-up.
No build step, no dependencies, no requests beyond the iframe itself.

From the repository root, on a port other than the portal's so the page is
genuinely cross-origin:

    python -m http.server 8070 --directory examples/embed-host

Then open `http://localhost:8070/?src=http://localhost:8060/share/<token>/`
— the portal on 8060, the token from an embed link's **Copy embed code**.

The browser's origin is whatever URL you opened, so the embed link's allowed
origins must list exactly `http://localhost:8070`; add `http://127.0.0.1:8070`
too if you open it that way. Any other origin gets an empty frame.
