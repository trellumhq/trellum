# Portal UX implementation guide — draft

**Status: proposed; awaiting user agreement. Not binding agent policy.**

This draft describes reusable implementation rules for console pages. It extends
[the console design system](design-system.md) and preserves the mandatory
[forms and navigation contract](forms-and-navigation.md). Existing authorization,
save semantics and report-theme boundaries remain authoritative. The local
interactive review artifact is `.lavish/portal-ux-agreement.html`; its examples
contain sample data and make no live requests.

## Page patterns

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
| Turn an independent option on/off | Checkbox with sentence label | State and effect are clear; do not use a dropdown for a boolean. |
| Review unavailable content | Explicit empty/loading/error state | Distinguish no choices, no matches, loading and load failure; preserve selections on failures. |

Do not style a closed selector like a bare search input. Use the same trigger,
chevron, focus ring, option row, popup surface, error treatment and spacing on
every page. A long-list selector must offer valid options before a search;
typing filters choices rather than creating free text unless creation is an
explicit supported operation.

## Recipients

The proposed arrangement is one **Recipients** field with People, Roles and
Groups filters inside the dropdown. A three-field arrangement is an unresolved
review option; either arrangement must reuse the same underlying controls.
Do not implement both arrangements as a user preference without a separate need.

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
(Europe/Amsterdam in the sample). State that default and allow an explicit
override. If detection is unavailable or invalid, use the documented application
fallback and identify it; never silently invent a timezone. Preserve saved
timezones when editing and submitted values after validation failure. A changed
browser location must not rewrite an existing schedule.

Frequency uses a native short-list dropdown. Group frequency and local time;
show weekday/month-day fields only when relevant. Hidden inputs must not create
surprise validation errors. Preserve existing scheduling/cooldown semantics and
supported values; the prototype's abbreviated sample options do not redefine
the production schema.

## Saves, tests and errors

Follow `docs/forms-and-navigation.md`; this draft does not create another save
contract. Ordinary edits use the shared settings form handler and response
helpers, real POST forms, CSRF and equivalent native responses. Use precise
action labels: Create alert, Save delivery, Save source, Test draft and Test
connection. Cancel leaving a dirty form requires a meaningful keep/discard
choice before any request.

- Save feedback distinguishes unsaved, saving, persisted, validation failure and
  unconfirmed outcome. Show feedback near the action and in an accessible live
  region. Do not claim success until persistence is confirmed.
- Preserve page, scroll, focus, other forms and failed drafts. Prevent duplicate
  submits. Never automatically retry an ambiguous write.
- Edits during a pending save stay dirty; acknowledgment applies to the submitted
  snapshot. Normalized server values must not overwrite newer edits.
- Errors sit by their controls with `aria-invalid` and associated descriptions;
  form errors provide a useful next step. Do not rely on red alone.
- Testing and saving are distinct operations. Label alert testing as preview
  only and preserve its production cost disclosure. A data-source save is not
  a successful connection test. Loading/results refer to the exact tested
  configuration and obsolete results are discarded.
- Do not reload the document for ordinary saves, uploads, or completion events.
  Creation/navigation retains its established intentional action semantics.
  In a simple list/add/edit flow, successful creation returns to the list with
  the new record and confirmation visible. Editing uses the shared in-place save
  contract and a clear Back to list action. Preserve filters/position on return.

## Access, keyboard and devices

Keep the existing route/view authorization authoritative. Developer/admin edit
rights vary by feature; do not infer universal editing rights from a prototype.
The current studio Data sources page is admin-only. Viewers cannot edit alerts
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

## Review checks before portal implementation is complete

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

## Draft integration point

After explicit agreement, merge the layout/control rules into `docs/design-system.md`
and any approved save/navigation clarifications into `docs/forms-and-navigation.md`.
This draft is an extension of those guides, not a competing permanent standard.
Add a short mandatory-read reference beside **Portal forms, saves and
navigation** in the canonical agent context, and in any applicable repo agent
entry point. Do not leave competing draft and final rule sets active. The
agreement and rollout planning belong in the workspace vault. Agent instruction
changes and the portal-wide affected-screen audit have not been performed by
this design draft.

## Source basis and prototype limits

Inspected: `static/ui.css`, `static/console-shell.css`, `static/recipient-picker.css`,
`templates/base.html`, `templates/_shell.html`, `templates/_console_nav.html`,
`templates/alerts/alert_form.html`, `templates/reports/delivery_form.html`,
`templates/datasources/_form.html`, `templates/datasources/settings.html`,
`templates/datasources/edit.html`, `docs/design-system.md` and
`docs/forms-and-navigation.md`.

The prototype copies the existing token stylesheet and bundled Inter fonts into
its ignored local assets. It abbreviates navigation and uses synthetic recipients,
sample schedules and an in-memory state demonstration. It is not a complete
portal, does not persist configuration, does not perform connection tests and
does not demonstrate native server fallbacks or enforce production permissions.
Its design decision controls queue feedback only through the Lavish review API.
