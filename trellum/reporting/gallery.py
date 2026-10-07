"""Build an index page for a directory of built reports.

A published output tree is a set of sibling directories with no front door --
you can reach a report only if you already know its slug. This writes an
``index.html`` beside them listing what is there.

Everything on the page comes from each report's ``_meta.json``, which the build
already writes, rather than a hand-maintained list. A list nobody updates is
worse than no list: it goes stale on the first report somebody adds, and the
gap is invisible until a visitor follows a link that isn't there.

Usage::

    python -m trellum.reporting.gallery output/
    python -m trellum.reporting.gallery output/ --title "Trellum demo reports"
    python -m trellum.reporting.gallery output/ \n        --cta-text "Built with X. Host your own." --cta-url https://example.com/
"""

from __future__ import annotations

import argparse
import html
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

from trellum.licensing import source_url, write_output_license
from trellum.themes import DEFAULT_THEME, DEFAULT_THEME_NAME

# The lattice mark, in its dark-surface colourway. Inlined rather than linked so
# the page has no asset it can fail to find -- the whole point of this tree is
# that it survives being copied somewhere unexpected.
_LOGO_SVG = """\
<svg class="mark" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 40 40" \
width="34" height="34" aria-hidden="true">
  <g stroke="#2E2E52" stroke-width="2.6">
    <line x1="8" y1="8" x2="8" y2="32"/>
    <line x1="20" y1="8" x2="20" y2="32"/>
    <line x1="32" y1="8" x2="32" y2="32"/>
  </g>
  <circle cx="8"  cy="32" r="4.4" fill="#2DD4BF"/>
  <circle cx="20" cy="20" r="4.4" fill="#9D8FF2"/>
  <circle cx="32" cy="8"  r="4.4" fill="#2DD4BF"/>
</svg>"""

_PHONE_SVG = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" \
width="15" height="15" fill="none" stroke="currentColor" stroke-width="2" \
stroke-linecap="round" aria-hidden="true">\
<rect x="7" y="2" width="10" height="20" rx="2.5"/>\
<line x1="11" y1="18.5" x2="13" y2="18.5"/></svg>"""

# The phone-preview styles: the per-card button and the overlay's phone
# chrome. Plain string (theme colours come through the CSS variables the
# stylesheet defines), kept out of _stylesheet so that function stays
# readable.
_PHONE_CSS = """\
  .cardwrap { position: relative; min-width: 0; }
  .cardwrap .card { height: 100%; box-sizing: border-box; }
  /* Reserve the pill's corner so a category label or a long first-line
     title never runs underneath it. */
  .cardwrap .cat, .cardwrap .card h2 { padding-right: 84px; }
  .phone-btn {
    position: absolute; top: 14px; right: 14px;
    display: inline-flex; align-items: center; gap: 5px;
    padding: 4px 10px; font: inherit; font-size: 11px; font-weight: 650;
    letter-spacing: .03em;
    border: 1px solid rgba(157, 143, 242, .45); border-radius: 20px;
    background: rgba(157, 143, 242, .10); color: var(--violet);
    cursor: pointer; transition: background .15s, border-color .15s;
  }
  .phone-btn:hover { background: rgba(157, 143, 242, .22); border-color: var(--violet); }
  .phone-btn:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
  .phone-overlay {
    position: fixed; inset: 0; z-index: 10;
    display: flex; align-items: center; justify-content: center;
    background: rgba(10, 10, 26, .72); backdrop-filter: blur(2px);
  }
  /* display:flex would otherwise override the hidden attribute's UA rule. */
  .phone-overlay[hidden] { display: none; }
  .phone-shell { display: flex; flex-direction: column; align-items: center; }
  .phone-bar {
    display: flex; align-items: center; gap: 14px;
    width: 100%; box-sizing: border-box; padding: 0 4px 10px;
    color: #fff; font-size: 13.5px;
  }
  .phone-title { font-weight: 650; flex: 1 1 auto; }
  .phone-full { color: var(--violet); text-decoration: none; white-space: nowrap; }
  .phone-full:hover { text-decoration: underline; }
  .phone-close {
    border: 0; background: none; color: #fff; font-size: 26px; line-height: 1;
    cursor: pointer; padding: 0 2px;
  }
  .phone-frame {
    width: 390px; height: min(760px, calc(100vh - 150px));
    box-sizing: content-box; padding: 14px 10px;
    background: #14142b; border: 1px solid #3a3a66; border-radius: 34px;
    box-shadow: 0 24px 70px rgba(0, 0, 0, .5);
  }
  .phone-frame iframe {
    width: 390px; height: 100%; border: 0; border-radius: 22px;
    background: var(--bg);
  }
  .phone-note {
    margin: 12px 0 0; max-width: 410px; text-align: center;
    color: rgba(255, 255, 255, .65); font-size: 12.5px;
  }
  /* On an actual phone the preview is the page itself. */
  @media (max-width: 700px) { .phone-btn { display: none; } }"""


def _read_meta(report_dir: Path) -> dict | None:
    """Return a report's metadata, or None if this isn't a built report.

    A readable ``index.html`` is what makes something a report -- that is the
    thing being linked to. ``_meta.json`` only adds detail, so a missing or
    malformed one degrades the card rather than hiding the report: something
    that built and can be opened should be reachable, and a report whose
    metadata is broken is exactly when you most want to click into it.

    An output directory legitimately holds things that are not reports --
    ``_vendor`` and ``.query_cache`` among them -- and those have no
    ``index.html``.
    """
    if not (report_dir / "index.html").is_file():
        return None

    meta: dict = {}
    meta_path = report_dir / "_meta.json"
    if meta_path.is_file():
        try:
            loaded = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                meta = loaded
        except (json.JSONDecodeError, OSError):
            meta = {}

    meta["_slug"] = meta.get("slug") or report_dir.name
    return meta


def _display_priority(meta: dict) -> int:
    """The author's declared ordering (``display.priority`` in report.yaml,
    carried through _meta.json), or 99 -- unranked sorts after ranked."""
    try:
        return int((meta.get("display") or {}).get("priority"))
    except (TypeError, ValueError):
        return 99


def collect_reports(output_dir: Path) -> list[dict]:
    """Every built report under ``output_dir``, sorted by the author's
    declared display priority, then category, then name.

    Priority first so a published gallery can lead with its flagship instead
    of whatever the alphabet picks; reports that declare nothing keep the
    category-then-name order this page has always had.
    """
    reports = [
        meta
        for child in sorted(output_dir.iterdir())
        if child.is_dir() and (meta := _read_meta(child)) is not None
    ]
    reports.sort(key=lambda m: (_display_priority(m),
                                (m.get("category") or "~").lower(),
                                (m.get("name") or m["_slug"]).lower()))
    return reports


def _format_when(raw: str | None) -> str:
    """A human-readable 'last built' string, or '' if there isn't a usable one."""
    if not raw:
        return ""
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%d %b %Y")


def _card(meta: dict) -> str:
    slug = meta["_slug"]
    name = html.escape(meta.get("name") or slug)
    desc = html.escape(meta.get("description") or "")
    category = html.escape(meta.get("category") or "")
    kind = "Analysis" if meta.get("kind") == "analysis" else "Report"
    category = f"{kind} · {category}" if category else kind
    when = _format_when(meta.get("last_run"))

    tags = "".join(
        f'<span class="tag">{html.escape(str(t))}</span>'
        for t in (meta.get("tags") or [])[:4]
    )

    # Chart count is a better signal of substance than anything else in the
    # metadata, and it is already computed at build time.
    totals = (meta.get("details") or {}).get("totals") or {}
    charts = totals.get("chart_count")
    rows = totals.get("total_rows")
    facts = []
    if charts:
        facts.append(f"{charts} chart{'s' if charts != 1 else ''}")
    if rows:
        facts.append(f"{rows:,} rows")
    if when:
        facts.append(when)
    footer = " · ".join(facts)

    # A report that did not build cleanly is still listed and still openable --
    # locally that is the one you want to click. Nothing is shown for a success,
    # so a published gallery of healthy reports carries no badge at all.
    status = (meta.get("last_status") or "").lower()
    failed = '<span class="bad">did not build</span>' if (
        status and status != "success") else ""

    # The phone button sits beside the anchor, not inside it (a button inside
    # a link is invalid HTML and double-activates); the wrapper keeps the two
    # as one grid cell.
    return f"""      <div class="cardwrap">
      <a class="card" href="{html.escape(slug)}/index.html">
        {f'<div class="cat">{category}</div>' if category else ''}
        <h2>{name}{failed}</h2>
        {f'<p>{desc}</p>' if desc else ''}
        <div class="tags">{tags}</div>
        {f'<div class="foot">{html.escape(footer)}</div>' if footer else ''}
      </a>
      <button class="phone-btn" data-href="{html.escape(slug)}/index.html"
              data-name="{name}" title="Preview at phone width"
              aria-label="Preview {name} at phone width">{_PHONE_SVG}<span>Mobile</span></button>
      </div>"""


def _studio_label(studio: str) -> str:
    """A slug like ``nova-play`` shown as ``Nova Play``."""
    return studio.replace("-", " ").replace("_", " ").title()


def _grouped_cards(reports: list[dict]) -> str:
    """Cards grouped per studio when the tree serves more than one business.

    A studio is a business, and "which business is this" outranks "which
    shelf of that business's reports" -- the whole pitch of a multi-tenant
    demo is the two companies side by side. A tree with one studio (or none
    recorded) keeps the flat grid this page has always had.
    """
    by_studio: dict[str, list[dict]] = {}
    for m in reports:
        by_studio.setdefault(str(m.get("studio") or ""), []).append(m)

    if len(by_studio) <= 1:
        return ('    <div class="grid">\n'
                + "\n".join(_card(m) for m in reports)
                + "\n    </div>")

    blocks = []
    # Biggest business first (ties alphabetical): the flagship studio leads
    # the page rather than whatever the alphabet happens to pick.
    for studio in sorted(by_studio, key=lambda s: (-len(by_studio[s]), s)):
        label = _studio_label(studio) if studio else "Other"
        cards = "\n".join(_card(m) for m in by_studio[studio])
        blocks.append(
            f'    <h2 class="studio">{html.escape(label)}</h2>\n'
            f'    <div class="grid">\n{cards}\n    </div>')
    return "\n".join(blocks)


def _render_cta(text: str, url: str) -> str:
    """The optional banner above the cards: one sentence, one destination.

    Deliberately generic and deliberately opt-in. A published output tree
    usually belongs to somebody -- a team publishing to colleagues, a vendor
    publishing to customers -- and the page is the only place that context can
    live once the reports have left the machine that built them. Whose page it
    is, and what it wants to say, is the caller's business: nothing about this
    function knows or names any particular product, and with no arguments it
    renders nothing at all.

    The host is shown rather than a bare arrow so a reader can see where the
    banner leads before clicking it.
    """
    if not (text and url):
        return ""
    host = urlsplit(url).netloc or url
    return (
        f'    <a class="cta" href="{html.escape(url, quote=True)}">\n'
        f'      <span>{html.escape(text)}</span>\n'
        f'      <span class="cta-go">{html.escape(host)} &rarr;</span>\n'
        f"    </a>\n"
    )


def _stylesheet(t) -> str:
    """The page's whole stylesheet, themed from ``t``.

    Split out of :func:`render_gallery` because it is the bulk of it and
    none of its shape: that function is about what the page contains, and
    a hundred lines of CSS in the middle of it buried the structure.
    """
    return f"""<style>
  :root {{
    --bg: {t.bg_main};
    --card: {t.bg_card};
    --card-hover: {t.bg_card_hover};
    --text: {t.text_main};
    --text2: {t.text_secondary};
    --muted: {t.text_muted};
    --border: {t.border_color};
    --accent: {t.bg_header};
    --violet: #9D8FF2;
    --radius: {t.border_radius};
    --shadow: {t.shadow};
  }}
  * {{ box-sizing: border-box; }}
  body {{
    margin: 0;
    background: var(--bg);
    color: var(--text);
    font: 15px/1.6 'Inter', -apple-system, 'Segoe UI', Roboto, sans-serif;
    -webkit-font-smoothing: antialiased;
  }}
  .wrap {{ max-width: 1080px; margin: 0 auto; padding: 0 20px 80px; }}
  header {{
    display: flex; align-items: center; gap: 14px;
    padding: 40px 0 8px;
  }}
  .mark {{ flex: 0 0 auto; }}
  h1 {{
    margin: 0; font-size: 26px; font-weight: 650; letter-spacing: -0.02em;
  }}
  .sub {{ color: var(--muted); font-size: 13.5px; margin: 2px 0 0; }}
  .rule {{
    height: 2px; margin: 22px 0 34px; border-radius: 2px;
    background: linear-gradient(90deg, var(--accent), var(--violet) 55%, transparent);
  }}
  .grid {{
    display: grid; gap: 16px;
    grid-template-columns: repeat(auto-fill, minmax(min(300px, 100%), 1fr));
  }}
  h2.studio {{
    margin: 34px 0 14px; font-size: 15px; font-weight: 650;
    letter-spacing: .01em; color: var(--text2);
    padding-bottom: 8px; border-bottom: 1px solid var(--border);
  }}
  h2.studio:first-of-type {{ margin-top: 0; }}
  .card {{
    display: block; text-decoration: none; color: inherit; min-width: 0;
    background: var(--card); border: 1px solid var(--border);
    border-radius: var(--radius); padding: 18px 20px 16px;
    transition: background .15s, border-color .15s, transform .15s;
  }}
  .card:hover {{
    background: var(--card-hover); border-color: var(--accent);
    transform: translateY(-2px); box-shadow: var(--shadow);
  }}
  .card:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
  .cat {{
    font-size: 10.5px; letter-spacing: .12em; text-transform: uppercase;
    color: var(--accent); font-weight: 650; margin-bottom: 7px;
  }}
  .card h2 {{
    margin: 0 0 6px; font-size: 17px; font-weight: 620; letter-spacing: -0.01em;
  }}
  .card p {{
    margin: 0 0 12px; font-size: 13.5px; color: var(--text2);
    display: -webkit-box; -webkit-line-clamp: 3; -webkit-box-orient: vertical;
    overflow: hidden;
  }}
  .tags {{ display: flex; flex-wrap: wrap; gap: 6px; }}
  .tag {{
    font-size: 11px; padding: 2px 8px; border-radius: 20px;
    background: rgba(157,143,242,.12); color: var(--violet);
  }}
  .foot {{
    margin-top: 12px; padding-top: 10px; border-top: 1px solid var(--border);
    font-size: 11.5px; color: var(--muted);
  }}
  .bad {{
    display: inline-block; margin-left: 8px; vertical-align: middle;
    font-size: 10.5px; font-weight: 650; letter-spacing: .06em;
    text-transform: uppercase; padding: 2px 7px; border-radius: 20px;
    background: rgba(248,113,113,.14); color: {t.accent_red};
  }}
  .cta {{
    display: flex; align-items: center; justify-content: space-between;
    gap: 16px; flex-wrap: wrap;
    margin: 0 0 30px; padding: 14px 18px;
    border: 1px solid var(--accent); border-radius: var(--radius);
    background: linear-gradient(90deg,
      rgba(157,143,242,.10), rgba(157,143,242,.03));
    color: var(--text); text-decoration: none; font-size: 14px;
    transition: border-color .15s, background .15s;
  }}
  .cta:hover {{ background: rgba(157,143,242,.16); }}
  .cta:focus-visible {{ outline: 2px solid var(--accent); outline-offset: 2px; }}
  .cta-go {{
    flex: 0 0 auto; font-weight: 650; color: var(--accent);
    white-space: nowrap;
  }}
  .empty {{ color: var(--muted); }}
{_PHONE_CSS}
  footer {{
    margin-top: 48px; padding-top: 18px; border-top: 1px solid var(--border);
    font-size: 12.5px; color: var(--muted);
  }}
  footer a {{ color: var(--accent); }}
</style>"""


def render_gallery(
    reports: list[dict],
    title: str,
    subtitle: str = "",
    cta_text: str = "",
    cta_url: str = "",
) -> str:
    """The full index page, self-contained: no external CSS, JS, or images.

    ``cta_text``/``cta_url`` add an optional banner above the cards — see
    :func:`_render_cta`. Omit them and the page is exactly as it was.
    """
    t = DEFAULT_THEME
    cards = _grouped_cards(reports)
    if not reports:
        cards = ('      <p class="empty">No built reports found in this '
                 'directory.</p>')

    count = len(reports)
    default_sub = f"{count} report{'s' if count != 1 else ''}"
    sub = html.escape(subtitle or default_sub)
    cta = _render_cta(cta_text, cta_url)
    runtime_source = source_url()
    for report in reports:
        candidate = report.get("framework_source_url")
        if not candidate:
            continue
        try:
            runtime_source = source_url(override=str(candidate))
            break
        except ValueError:
            continue
    runtime_source = html.escape(runtime_source, quote=True)

    return f"""<!DOCTYPE html>
<html lang="en" data-theme="{DEFAULT_THEME_NAME}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
{_stylesheet(t)}
</head>
<body>
  <div class="wrap">
    <header>
      {_LOGO_SVG}
      <div>
        <h1>{html.escape(title)}</h1>
        <p class="sub">{sub}</p>
      </div>
    </header>
    <div class="rule"></div>
{cta}{cards}
    <footer>
      Every report here is a self-contained HTML file built from a generator in
      a repository — no server, no database behind this page. The data is
      fabricated. <a href="{runtime_source}" rel="noopener">Trellum runtime source</a>.
    </footer>
  </div>
  <div class="phone-overlay" id="phone-overlay" hidden>
    <div class="phone-shell" role="dialog" aria-modal="true" aria-label="Phone preview">
      <div class="phone-bar">
        <span class="phone-title"></span>
        <a class="phone-full" target="_blank" rel="noopener">Open full size &rarr;</a>
        <button class="phone-close" aria-label="Close preview">&times;</button>
      </div>
      <div class="phone-frame"><iframe title="Report at phone width"></iframe></div>
      <p class="phone-note">The same page at 390px — the report's own
        responsive layout, not a screenshot. Filters and charts stay live.</p>
    </div>
  </div>
  <script>
  (function () {{
    var overlay = document.getElementById('phone-overlay');
    var iframe = overlay.querySelector('iframe');
    var title = overlay.querySelector('.phone-title');
    var full = overlay.querySelector('.phone-full');
    // A desktop scrollbar would eat ~15px of the frame's 390, which is
    // enough to push a filter bar over the edge and raise a horizontal
    // scrollbar the same page never shows on a real phone. Phones use
    // overlay scrollbars; hiding these restores the true 390px of layout
    // width. Same-origin (a sibling report), so this is readable -- and
    // wrapped anyway, because a preview is not worth an exception.
    iframe.addEventListener('load', function () {{
      try {{
        var doc = iframe.contentDocument;
        if (!doc || !doc.head) return;
        var style = doc.createElement('style');
        style.textContent = 'html{{scrollbar-width:none}}' +
          'html::-webkit-scrollbar,body::-webkit-scrollbar{{width:0;height:0}}';
        doc.head.appendChild(style);
      }} catch (e) {{ /* preview still works, just with its scrollbars */ }}
    }});
    function close() {{
      overlay.hidden = true;
      iframe.src = 'about:blank';  // stop the framed page's timers
    }}
    document.addEventListener('click', function (e) {{
      var btn = e.target.closest && e.target.closest('.phone-btn');
      if (btn) {{
        iframe.src = btn.getAttribute('data-href');
        title.textContent = btn.getAttribute('data-name');
        full.href = btn.getAttribute('data-href');
        overlay.hidden = false;
        return;
      }}
      if (e.target === overlay || e.target.closest('.phone-close')) close();
    }});
    document.addEventListener('keydown', function (e) {{
      if (e.key === 'Escape' && !overlay.hidden) close();
    }});
  }})();
  </script>
</body>
</html>
"""


def build_gallery(output_dir, title: str = "Trellum reports",
                  subtitle: str = "", nojekyll: bool = False,
                  cta_text: str = "", cta_url: str = "") -> Path:
    """Write ``index.html`` into ``output_dir``. Returns the path written.

    ``nojekyll`` additionally writes an empty ``.nojekyll`` file. That matters
    on GitHub Pages, which runs Jekyll over a site by default and silently
    drops every path beginning with an underscore -- including ``_vendor``,
    where a portable build puts the entire client runtime. The symptom is a
    page that loads with no charts and no error, which is the same failure
    portable builds exist to prevent, arriving by a different route.
    """
    out = Path(output_dir)
    if not out.is_dir():
        raise NotADirectoryError(f"Not a directory: {out}")

    reports = collect_reports(out)
    index = out / "index.html"
    index.write_text(
        render_gallery(reports, title, subtitle, cta_text, cta_url),
        encoding="utf-8",
    )
    write_output_license(out)

    if nojekyll:
        (out / ".nojekyll").write_text("", encoding="utf-8")

    return index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build an index page for a directory of built reports")
    parser.add_argument("output_dir",
                        help="Directory containing built report directories")
    parser.add_argument("--title", default="Trellum reports")
    parser.add_argument("--subtitle", default="",
                        help="Defaults to a count of the reports found")
    parser.add_argument(
        "--cta-text", default="",
        help="One sentence for a banner above the cards. Rendered only when "
             "--cta-url is given too; omit both and the page is unchanged.")
    parser.add_argument(
        "--cta-url", default="",
        help="Where the banner links. Its host is shown, so a reader can see "
             "where it leads before clicking.")
    parser.add_argument("--nojekyll", action="store_true",
                        help="Also write .nojekyll, required on GitHub Pages or "
                             "Jekyll silently drops _vendor/ and the charts "
                             "disappear")
    args = parser.parse_args(argv)

    try:
        index = build_gallery(args.output_dir, args.title, args.subtitle,
                              nojekyll=args.nojekyll,
                              cta_text=args.cta_text, cta_url=args.cta_url)
    except NotADirectoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    count = len(collect_reports(Path(args.output_dir)))
    print(f"Wrote {index} ({count} report{'s' if count != 1 else ''})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
