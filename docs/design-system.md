# Trellum console design system

The authenticated console uses one neutral shell across the report catalog,
organization pages, studio management and console-mounted reports. Report
content keeps its own named theme and rendering contract.

## Shell

`templates/_shell.html` provides the fixed utility header and sidebar.
`templates/_console_nav.html` is the single source for studio links and their
role checks; `_org_nav.html` adds the organization groups wherever an
organization is active, including while working inside a studio.
Report display includes `_shell.html`, so it does not copy navigation or
permission logic.

The desktop sidebar is 240px and may collapse to 64px. The header is 56px.
At widths below 1024px the sidebar is a closed drawer. The shared
`static/console-shell.js` handles its expanded state, Escape, focus trapping,
focus return, `aria-expanded`, and `inert` content. Shell controls must retain
a visible `:focus-visible` outline. Theme and collapsed-sidebar preferences
are applied before styles load, and initialization does not animate from a
default state. Drawer motion takes 200ms; reduced-motion users get an
effectively immediate transition.

At 767px and below, the header is one 56px bar: menu, known page title, the
studio search button when present, and the account button at the right edge.
Search opens in a separate pane on demand. The server supplies the shared
title through `console_page_title`; routes without known metadata keep their
existing in-page heading.

The shared shell assets have a strict boundary: selectors and color variables
are scoped to `.tl-console-*`. They may be loaded into a report document
without resetting or recoloring report content. The shell root exposes:

- `data-console-css` and `data-console-script` asset URLs;
- `data-console-default-mode` for the organization default;
- `data-console-theme="light|dark"` after runtime resolution;
- `--tl-console-width` (240px) and `--tl-console-collapsed-width` (64px).

Collapsing applies `body.tl-console-collapsed` and root `.is-collapsed`, and
emits `tl-console-resize` with the effective width. `data-html2canvas-ignore`
keeps shell chrome out of report captures.

## Navigation

Studio navigation is Reports, Operations, Metrics, Experiments, Annotations,
Alerts and Report Analytics. Report Analytics retains its developer/admin check. The admin-only
Studio settings group contains Repository, Data sources, Members and Report
theme. Organization links follow as Org Workspace, Org People, Org Data &
reporting and Organization groups. The Organization group remains last and uses
the existing capability locks. There is one navigation rail.

Organization and studio selectors sit at the top of the sidebar. Breadcrumbs,
studio report search and the account menu sit in the utility header. Category,
studio filter, sorting, view and error-log controls remain local to the report
catalog. Their existing event IDs are stable.

## Console theme

The console supports Light, Dark and Auto through `localStorage['mgmt-theme']`.
With no personal value, `Organization.default_mode` is used; an explicit Auto
choice follows the operating-system preference. The shell resolves this mode
inside its own root when mounted in a report, independent of the report's
`data-theme` and theme picker. Dynamically mounted shells call the shared
`prepare()` hook before append and `init()` after append.

| Token | Light | Dark |
|---|---|---|
| background | `#F6F7F8` | `#111315` |
| card | `#FFFFFF` | `#181B1F` |
| hover | `#EEF1F3` | `#23272D` |
| text | `#172126` | `#F3F4F6` |
| secondary text | `#475569` | `#A7ADB8` |
| border | `#E2E6EA` | `#30363D` |
| primary | `#0F766E` | `#0F766E` |
| primary hover | `#115E59` | `#115E59` |
| accent ink | `#0F766E` | `#2DD4BF` |

Small violet indicators may identify studio scope. Broad violet backgrounds
and decorative accent strips are outside the console language. Status colors
carry semantic text as well as color.

The public website and documentation use this same neutral token set, with a
larger marketing type scale where needed. Links and active text use accent ink;
filled primary actions use primary and primary hover. Violet is limited to the
lattice mark and small studio-scope indicators. Named report-theme previews,
such as Blossom, keep their own palette inside the report-content boundary.

## Layout and type

Console content is fluid with no 1200px cap. Page gutters are 24px on desktop
and 16px on mobile. Single-column forms remain readable at a maximum of 760px;
tables and catalogs may use the available width. Below 768px, page panels use
the mobile card treatment, text-entry controls use at least 16px type, and
interactive targets are at least 44px high.

Inter is the console font. Body and controls are 14px, table content 13px,
metadata 12px, section headings 16px, and page titles 24px. Controls use an
8px radius and panels use 10px.

Built-in report components use the same Inter family and semantic type scale:
14px body and controls, 13px tables, 12px labels and metadata, 16px section
headings, and 24px report titles and KPI values. Reports may explicitly override
these theme tokens; nested sections and compact display modes retain their
documented hierarchy. Report CSS is baked into the HTML during generation, so
existing report outputs must be rebuilt to pick up component typography changes.
Updating the portal's shell assets alone does not refresh those styles.

## Components

Use the shared `.ui-*` components from `static/ui.css`. Primary actions use
`.ui-btn.primary`; neutral actions use `.ui-btn.ghost`; destructive actions
use `.ui-btn.danger`. Filter pills use `.ui-pill` and status uses `.ui-badge`.
Bare buttons are neutral. A form has at most one primary action.

Drawers and menus close with Escape, trap focus while modal, return focus to
their opener, and expose the relevant dialog/menu state. Forms provide visible
labels and inline errors. Entity names link to their records rather than
showing bare identifiers.

## Deliberate exceptions

Terminal and log surfaces stay dark because their colors carry terminal
semantics rather than console theme. Report data tables may use a bounded
inner horizontal scroll area when preserving column relationships is more
useful than turning each row into a mobile card.
