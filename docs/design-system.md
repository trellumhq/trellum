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

Studio navigation includes Reports, Analyses, Deliveries, Metrics, Experiments,
Annotations and Alerts. Operations and Report Analytics retain their
developer/admin checks; limited studio visibility still restricts workspace links. The admin-only
Studio settings group contains Repository, Data sources, Members and Report
theme. Organization links follow as Org Workspace, Org People, Org Data &
reporting and Organization groups. The Organization group remains last and uses
the existing role checks. There is one navigation rail.

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

Studio Members uses a fluid page layout: the header, member list and Add a
member section share the content area's left edge and fill its available
width. Do not wrap these sections in `.ui-form-rail`. The Person and Role
fields share a responsive two-column row, stacking below 768px. Keep the
standard page gutters and section padding. This page-specific pattern does
not change the bounded layout of other settings forms.

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

Inline forms align single-line fields and buttons at the same height. Dropdowns
reserve space for their arrow; crowded rows wrap whole controls instead of
squeezing action labels. Keep the full-width field treatment for stacked forms.

Drawers and menus close with Escape, trap focus while modal, return focus to
their opener, and expose the relevant dialog/menu state. Forms provide visible
labels and inline errors. Entity names link to their records rather than
showing bare identifiers.

## Form behavior

Settings saves preserve the current page, scroll, focus and neighboring
drafts. Follow the shared contract and verification requirements in
[Forms and navigation](forms-and-navigation.md). Distinguish saving, saved,
validation failure and unconfirmed outcomes, and refresh affected regions.

## Deliberate exceptions

Terminal and log surfaces stay dark because their colors carry terminal
semantics rather than console theme. Report data tables may use a bounded
inner horizontal scroll area when preserving column relationships is more
useful than turning each row into a mobile card.


## Portal page and input patterns


| Pattern | Layout | Actions |
| --- | --- | --- |
| Create/edit/settings form | Maximum 760px form rail; align its title and sections to the same edge. Related fields may share a two-column row. | One primary create/save action at the bottom of its form; secondary test and cancel actions beside it. |
| List/index | Available content width, shared header, short description, local search/filter toolbar, table or mobile records. | One primary add action in the page header; explicit row actions and linked entity names. |
| Report/data workspace | Available width where the data needs it; contain local scrolling. | Keep workspace controls with their content; preserve the report host boundary. |
| Read-only record | Same information order, readable values and summaries. | No active editing controls or implied save permission. Explain the relevant role restriction. |

Use the existing neutral console shell, Inter typography, semantic tokens,
`.ui-*` controls, 8px control radius and 10px panel radius. Desktop gutters are
24px; mobile gutters are 16px. Do not introduce another UI framework or a second
palette. Keep violet restricted to studio scope indicators.

Use a scope/breadcrumb context, one page title and a concise task description.
Keep wording, labels and navigation targets consistent between list/add/edit
views. Related inputs belong in named sections, normally with 16px field gaps
and 24px section padding. Do not place each short field in its own full-width
section. A short number, date or time need not stretch across the entire rail.
Below 768px, related field rows become one column. Avoid nesting decorative
cards inside cards.

## Choosing a control

| User task | Shared control | Required behavior |
| --- | --- | --- |
| Enter arbitrary text | Text input or textarea | Persistent visible label; input type matches the value. Placeholder is supplementary. |
| Choose one of a short fixed list | Native single `<select>` | Clearly visible dropdown arrow and consistent field height. Use for frequency and short policy lists. |
| Choose one from a long list | Searchable single-choice dropdown | Bounded trigger showing current value/placeholder plus chevron; clicking opens options without typing. Search lives inside the open dropdown. |
| Choose several people, roles or groups | Searchable multi-choice dropdown with checkboxes and removable chips | Click to open before searching; choices remain visible after each selection; no Ctrl/Cmd gesture; each selected value removable individually. |
| Enter a calendar date | Native date input | Use `type="date"` with a calendar affordance; preserve the date-only meaning. |
| Enter a schedule time | Native time input | Use `type="time"`, a visible time-picker affordance and minute precision; convert to existing hour/minute fields without changing schedule semantics. |
| Enter a local date and time | Native datetime input | Use `type="datetime-local"` and explicitly state its timezone context; do not convert date-only values into timestamps. |
| Turn an independent option on/off | Checkbox with sentence label | State and effect are clear; do not use a dropdown for a boolean. |
| Review unavailable content | Explicit empty/loading/error state | Distinguish no choices, no matches, loading and load failure; preserve selections on failures. |

Do not style a closed selector like a bare search input. Use the same trigger,
chevron, focus ring, option row, popup surface, error treatment and spacing on
every page. A long-list selector must offer valid options before a search;
typing filters choices rather than creating free text unless creation is an
explicit supported operation.

## Recipients

Use one **Recipients** field with People, Roles and Groups filters inside the
dropdown. This is the shared arrangement for Alerts and Deliveries. Keep the
underlying server fields and permission checks; do not offer parallel layout
preferences or implement separate selection widgets for each page.

- People show a recognizable name and secondary identity such as email. Roles
  and permission groups display their type, including in selected chips.
- All selected values remain visible below the trigger and can be removed
  individually. Removal announces the changed selection and gives focus to the
  next removal control or selector trigger without scrolling the document.
- Selection does not close the menu. A visible Done action closes it. Outside
  click and Escape also close it. Search and type filters never clear selections.
- Provide a bounded scrollable list with useful initial choices, a selected
  count, and search by name/identity. Support 100+ people without showing 100
  uncontrolled rows or requiring the user to know a name before opening it.
- For server-backed results, debounce/cancel stale searches; preserve selected
  identities outside the current result page. Loading/error states must not
  suggest the organization has no members.
- Retain authorized source data and server-side validation. Do not expose people
  or groups beyond the current scope. Resolve recipients using existing access
  rules and deduplicate overlapping recipients. Do not change membership or
  authorization through the selection UI.
- Provide a usable no-JavaScript checkbox/list fallback with clear labels.

## Timezone and schedule

Use one searchable timezone dropdown implementation in Alerts and Deliveries.
Its closed state is a visibly bounded choice control with an arrow and the saved
IANA timezone. Open a list on click; search is optional. Show friendly city
spacing while retaining the exact IANA value. Avoid static UTC offsets as the
only identity because daylight saving may change them.

For a new, unbound schedule, default to the valid detected browser timezone
(for example, Europe/Amsterdam). State that default and allow an explicit
override. If detection is unavailable or invalid, use the documented application
fallback and identify it; never silently invent a timezone. Preserve saved
timezones when editing and submitted values after validation failure. A changed
browser location must not rewrite an existing schedule.

Frequency uses a native short-list dropdown. Group frequency and local time;
show weekday/month-day fields only when relevant. Hidden inputs must not create
surprise validation errors. Preserve existing scheduling/cooldown semantics and
supported values; UI choices must match the production scheduling schema.


## Access, keyboard and devices


Keep the existing route/view authorization authoritative. Developer/admin edit
rights vary by feature; do not infer universal editing rights from another feature.
The studio Data sources page is admin-only. Viewers cannot edit alerts
or delivery schedules. Where an existing route allows read access, show values
and scope context rather than an apparently editable form with a broken Save
button. Hide or disable actions with an understandable reason without exposing
restricted data.

Use visible labels and focus rings. Selector triggers expose expanded state and
their popup relationship. Implement one coherent, tested accessibility pattern:
an accessible nonmodal picker dialog with native checkboxes/buttons, or a full
combobox/listbox pattern; do not mix incomplete ARIA patterns. Support opening
with Enter/Space, arrow traversal, checkbox toggling with Space, Tab traversal,
Escape and focus return. Do not trap focus in a nonmodal popup; do trap focus in
modal dialogs and return it to the opener. Announce selection and status changes.
Implementation references: the W3C APG [combobox pattern](https://www.w3.org/WAI/ARIA/apg/patterns/combobox/)
and [listbox pattern](https://www.w3.org/WAI/ARIA/apg/patterns/listbox/).
Interactive checkboxes are not nested inside listbox options in the proposed
checkbox-dialog pattern. These references do not constitute a compliance claim.

On phones, controls and actions have at least 44px touch targets and text inputs
at least 16px type. Dropdowns must fit the viewport, scroll their own options and
remain usable with the software keyboard. Use the shared modal/drawer pattern
when the popup cannot fit. Verify 200% zoom, reduced motion, long names, dark
mode and keyboard-only use. Do not create page-level horizontal overflow;
tabular data can use an explicitly bounded inner scroller when necessary.

## UI review checks

1. The changed page uses the correct page pattern, existing shell and tokens;
   forms are bounded and related fields share rows where space permits.
2. Every selector opens choices on click, without typing first. Short lists use
   native selects; long lists use the shared searchable dropdown.
3. Recipient selection works with 100+ people, multiple types, overlapping
   choices, long names, no matches, unavailable groups and chip removal.
4. Alerts and Deliveries have the same timezone control. New defaults, saved
   values, manual overrides and validation rerenders are verified separately.
5. Required shared-form checks pass: mid-page success/failure; retained draft,
   focus and scroll; duplicate submits; canceled confirmation; field/form
   errors; normalization; edits during saves; native fallback and permission/
   CSRF failures. Delayed/out-of-order tests must not display stale outcomes.
6. Keyboard, screen-reader semantics, 390px phone, desktop, touch targets, zoom
   and light/dark presentation are checked. No unexpected overflow or clipped
   menu. Role restrictions remain unchanged.
7. Data-source save and connection status stay separate. Lists distinguish
   loading, zero records, no filter matches and failed requests.

## Shared picker markup and initialization

`templates/base.html` loads `choice-picker.css/js` and `recipient-picker.css/js`.
Other hosts, including report widgets and the catalog, load those assets in that
order using their resolved static URLs. These styles scope themselves to picker
roots and do not require `ui.css` or reset report content.

Add `data-choice-picker` to a native single select, or to its wrapper containing
one select. Keep its `name`, options, `required`, labels and server errors. The
enhancer retains that select as the submitted value, including blank/inherit
options, disabled options and disabled/hidden optgroups. Optional
`data-picker-label`, `data-picker-placeholder`, `data-picker-search-placeholder`
and `data-picker-empty-state` customize its text. Timezones use this same
enhancer; the page supplies validated IANA choices and guards browser defaults
for new, unbound schedules. Native date/time controls remain native inputs.

Use one `data-recipient-picker` wrapper with a visible label. Put the existing
checkbox lists inside children marked `data-recipient-type="people"`, `"roles"`
or `"groups"`; leave checkbox names and values unchanged. Optional checkbox
`data-recipient-name` and `data-recipient-identity` supply the person's display
name and secondary email. `data-picker-label="Recipients"` explicitly names the
control, and `data-picker-empty-state` supplies its empty message. Existing
single-list wrappers also work. Preserve each group's errors; they remain
visible when the options close. No JavaScript means ordinary selects and
checkbox lists, without changing the server contract.

`TrellumChoices.init(root)` and `TrellumRecipients.init(root)` return initialized
instances, safely initialize repeated roots, and run on initial DOM and
`settings:updated`. Their `.refresh()` synchronizes programmatic values; instances
are also available as `select._trellumChoice` and `wrapper._trellumRecipients`.
After setting a native value, dispatch bubbling `input`/`change` when it is a user
change; use `.refresh()` for silent normalization. Form reset and shared saved
events also refresh captions. Replace a recipient wrapper when its available
checkbox catalog changes, then initialize the new root; selections remain native
checkbox state. Fetch/loading errors belong to the host and must not replace an
existing selection with an empty catalog.

Picker popups are nonmodal dialogs with buttons or native checkboxes, internal
search and ordinary Tab traversal. The native popover layer avoids clipping
inside drawers while retaining form ownership. A picker handles Escape before
an enclosing drawer, returns focus after selection/removal, and bounds its list
to the viewport, opening above when necessary. Internal search is not a form
draft change. Single-choice native validity focuses the visible trigger and
shows a persistent field error; server `aria-invalid`/`aria-describedby` values
are mirrored to the trigger.

Use `.ui-form-rail` as the centered 760px page wrapper for its header and editing
sections. `.ui-form-section` supplies neutral borders and 24px padding (16px on
mobile); `.ui-form-row` is a two-column row collapsing below 768px, with
`.ui-field-wide` spanning it. Keep operational lists and tables outside the rail.
