# Forms and navigation

Ordinary settings saves update the current page in place. Saving preserves
scroll, focus, and drafts in other forms. Use the shared handler instead of
adding a separate submit implementation to each page.

## Settings forms

1. Render a normal POST form with `{% csrf_token %}`, visible labels, field
   errors and an explicit submit button. Keep a working native response.
2. Add `data-settings-form` for dirty tracking and `data-settings-save` for
   background saving through `static/settings-forms.js`. Use stable IDs when
   other controls or page listeners reference a form.
3. In the authorized view, use `is_settings_request(request)` from
   `apps.core.form_responses`. Preserve the same validation, permissions, CSRF,
   persistence and audit behavior for native and enhanced requests.
4. Return `settings_success(request, message, **data)` only after persistence.
   Use `settings_error(request, message, form=form)` for validation errors,
   or an explicit `errors` mapping when there is no Django form. Keep existing
   render/redirect responses for native requests.
5. Use shared saving, saved and error feedback. Prevent duplicate submissions
   and retain failed drafts. A timed-out write has an unconfirmed outcome:
   never automatically retry it or claim it succeeded.

Enhanced requests send `X-Trellum-Form: 1` and `Accept: application/json`.
Success JSON contains `ok: true` and `message`. Error JSON contains `ok: false`,
`message`, and `errors`, mapping field names to arrays of strings; `__all__`
denotes form-level errors. Validation errors use an unsuccessful HTTP status.
Headers and response helpers do not grant authorization.

Construct form data before disabling controls and include the clicked
submitter's name/value. Preserve originally disabled controls. Confirmation
dialogs run before the save handler; canceling sends no request. Do not use
`form.submit()` to bypass validation and confirmation.

If a user edits while a save is pending, acknowledge the submitted snapshot;
later edits stay dirty. Server-normalized `values` must not overwrite a newer
draft. After confirmation, the handler emits `settings:saved` followed by
`settings:success`, whose `event.detail` contains the response payload.
On failure, `settings:error` carries a plain `message` after controls are
restored. Page listeners can release temporary action locks without retrying
the write.

## Dependent content

Use explicit `data-settings-text` targets for plain-text summaries returned
in the response's `updates` mapping. Use `refresh` keys for known
`data-settings-refresh` regions needing fresh server-rendered content. Fetch
the current page without the browser cache and replace only named regions.
Never execute scripts from a refresh response or replace the document just to
show a save result.

Preserve dirty, saving or focused regions until updating is safe. Initialize
new forms with `TrellumSettingsForms.init(root)`; initialization is idempotent.
Controls inside replaceable regions use delegated events or reinitialize on
`settings:updated`. Keep neighboring drafts outside the refresh boundary.

Intentional navigation, such as opening a newly created entity, uses a verified
same-origin redirect. Creation, deletion, transfer, authentication and personal
immediate display preferences retain their action semantics. Do not opt them
into settings saving without implementing the response contract.

## Slow work and background updates

- Saved configuration and successful connection testing are separate states.
  Confirm persistence promptly, then show testing/job progress. Never label
  untested configuration healthy.
- Bind test results to the configuration and declaration tested. Discard
  obsolete results and do not enqueue builds from them. Do not hold a database
  lock over external connection work.
- Keep one status poll in flight. Invalidate responses after newer actions,
  renders or navigation. Dependent registry/history reads belong to that same
  lifecycle; serializing only the first request is insufficient.
- Update affected values in place. Preserve expanded panels, log position,
  focused controls and other drafts. Stop obsolete work on departure and resume
  safely after browser history restoration.
- Render ready primary content without waiting for secondary favorites or
  subscriptions. Distinguish loading, empty and failed states, especially when
  the selected filter depends on secondary data.
- Repository completion and uploads refresh their affected regions. Do not use
  `location.reload()` as the normal success path.

## Scroll and focus

Save keeps position. Back and Forward restore the history entry's position
after its content is ready. A new destination normally starts at the top; a
fragment link targets its section. Never restore saved coordinates before
delayed content makes those coordinates possible.

Keep the sidebar's independent scroll state. Preserve the console report
host's origin/token checks, iframe lifecycle and filter/history behavior.

Errors appear beside controls with `aria-invalid` and accessible descriptions.
Status messages use a live region. Avoid unexpected focus/scroll changes after
saving. When a control disappears, use a meaningful nearby focus fallback with
`preventScroll`.

## Required verification

For new or changed forms, verify mid-page success and failure; preserved draft,
focus and scroll; duplicate submits; canceled confirmations; field/form errors;
normalization; edits during saving; native response parity; and permission/CSRF
failures without changing access rules.

For refresh/navigation changes, delay responses and deliver older ones last.
Verify secondary-request failure, completion without document navigation,
dirty neighbors, repeated initialization, and Back/Forward with delayed
content. Start from browser tests under `apps/core/tests/`,
`apps/studios/tests/` and `apps/reports/tests/`. Run database suites sequentially
unless each process has its own test database.


## Consistent form interactions

Use the shared page and input patterns in [the design system](design-system.md).
Date, time and local-date-time values use the appropriate native controls;
durations such as alert cooldowns remain numbers with explicit units. Long
choice lists open on click before any typing. Search filters a dropdown, while
selected values remain visible and valid outside the current result page.

Use precise action labels such as Create alert, Save delivery, Save source,
Test draft and Test connection. A confirmed creation returns to the relevant
list or opens the new record as its explicit flow requires. Ordinary edits
preserve the current page and offer a clear Back link; preserve intentional
CRUD navigation where explicitly required by the page's contract. Canceling a
dirty form offers a keep/discard choice before any request or draft loss.

Use `data-settings-form` to track a creation form without intercepting its
native submit. Add `data-settings-leave` to Cancel/Back links inside that form,
or set its value to the form's ID for a header link outside the form. These
links ask before discarding a dirty draft or leaving a save in progress;
clean forms leave immediately. Links opening another tab do not discard the
current draft. Keep the dedicated creation response and navigation semantics.

Testing and saving are different operations. Label alert tests as previews,
retain the cost disclosure, and keep them free of email delivery side effects.
A data-source save does not establish that the connection works. Test results
belong to the tested configuration and become outdated after a relevant edit.
Test and Save are available in the same editor.

A failed form submission must preserve recipient selections, timezone and local
time values. Inline errors remain visible even if dropdown options are closed
or filtered. Newly suggested browser timezones must not replace stored values
on edits or submitted values on validation rerenders.

## Console navigation

Console pages are ordinary documents. The shell gives them loading feedback,
and one route pair, Metrics Overview and Comparison, also swaps in place.

**Loading feedback is opt-in.** Mark a link or a GET form that opens another
console page with `data-console-nav`, or put the attribute on a container that
holds only such links, like the sidebar, the account menu, a pager or a list
toolbar. `data-console-no-feedback` opts one element out of a marked container.
The shell, `_pagination.html`, `_list_toolbar.html` and the operator tabs
are already marked. Never mark a download, an export, an API call, a report
page or an external link: their response does not replace the page, so the
loading state would never end. Unmarked links keep the browser's own navigation,
and a new route needs no change in `static/console-shell.js`. Modifier clicks,
new tabs, `download`, same-page hashes and other origins are skipped even when
marked.

A marked link shows the destination heading and a "Loading <page>…" state, with
**Cancel loading**; the document URL commits with the browser's response.
Cancel loading or Escape stops the request and restores the old page, focus and
scroll. A slow page is not a failed page: after 15 seconds the text becomes
"Still loading <page>…" and the request keeps going. Nothing stops it except
that explicit cancel, so users never have to retry a page that was about to open.

**Metrics Overview and Comparison** of one studio update the URL, heading,
sidebar selection and loading state in place, after the shared leave guard, and
reject aborted or superseded replies. The view in
`apps/reports/metrics_catalog.py` answers a request that carries
`X-Trellum-Navigation: 1` with JSON `{title, url, html}`, where `html` is the
same template's content block. Responses vary on that header and fragments use
private, no-store caching. The view's own authorization applies; the header
grants nothing. `static/console-metrics-controller.js` mounts the page's
scripts, and its lifecycle is the contract for any future page that does the
same: register listeners with the abort signal and, on departure, disconnect
observers, cancel timers, stop frames and remove overlays. Primary layout
decides when history scroll restoration may run; optional secondary requests
never block it. Fetched script elements, inline event handlers and JavaScript
URLs are removed, so their code is never evaluated. New destinations focus
their heading; Back and Forward restore position after the page is ready.
Canceling a dirty Back traversal reverses that traversal without discarding
Forward history. Report hosts keep their existing history owner. A failed
in-place request offers direct entry and does not silently execute a login
document or claim content is empty.

Other pages stay native. This is not an enhancement of the catalog, settings or
report hosts. Authentication retains native semantics.

The shared leave guard inspects every tracked dirty or saving form, including
sidebar departures. Cancel retains drafts. Accepted departure invalidates
summary reads and detached save callbacks; it does not replay or pretend to
cancel a server write. A pending write becomes an unknown outcome with usable
controls if the old document returns from browser cache. Check persistence
before retrying. Native tab closing/reloading also uses the browser's draft
warning when supported. An accepted native submit guards neighboring drafts,
excludes its own submitted draft from discard confirmation and suppresses the
redundant unload warning; canceled form confirmations still send no request.

## Opt-in action observations

`apps.core.actions.action_payload` builds a version 1 `action` observation with
an immutable operation `id`, `state` (`accepted`, `running`, `succeeded`,
`failed`, or `unconfirmed`), sanitized `message`, optional tested `revision`,
and optional root-relative `status_url`. It does not admit work or authorize
status lookup. Each operation retains its existing worker, permissions and
persistence. Ordinary settings saves keep their existing response contract.
Provider acceptance is a separate delivery outcome; it does not prove delivery.
Browser cancellation or timeout does not cancel server mutations or replay them.

`static/actions.js` exposes `TrellumActions.statusURL(value)` to validate
same-origin lookup URLs and `createPoller({read, onStatus, onError, isTerminal,
isCurrent, signal, intervalMs, maxIntervalMs, timeoutMs})`. The returned poller
has `start`, `refresh`, `pause`, `resume` and permanent `stop` methods. It is
opt-in; loading it binds no forms, page events or visibility listeners. Callers
pause hidden surfaces, stop on departure, and resume with current operation
identity after history restoration. An external abort permanently stops it.

The `read({signal, isCurrent, statusURL})` callback must await response bodies
and all dependent reads, reject malformed or unsuccessful responses, and
perform no DOM commits. `onStatus` receives only a current observation.
For Operations, await authorized Git status and any dependent publish history
and registry reads in that same lifecycle; preserve their existing shapes.
Repository flags alone do not establish success. The caller supplies
`isTerminal(result)` using authoritative domain evidence. Without it, polling
continues. A refresh during a read coalesces one follow-up. Read failures use
bounded backoff; timeout leaves the outcome unconfirmed and permits explicit
refresh while ignoring late results. These retries apply only to reads.

Validate status URLs before fetching them and check the current actor's access
on every server lookup, including revocation. Keep credentials and provider
exception text out of observations. Verify delayed bodies, dependent reads,
obsolete responses, timeout recovery, cleanup and native fallback before
adopting the helper in a route. Helper protocol tests alone do not verify an
operation's persistence, worker restart or portal performance.

Propagate the supplied signal to every dependent fetch. Timeout and pause may
detach a reader that ignores cancellation: the helper guarantees one current
observation and guarded commit, not physical cancellation or bounded underlying
server concurrency for such readers. A newer action or configuration requires
explicit invalidation through pause/resume or replacing the poller, together
with the current identity predicate; `refresh` alone coalesces reads. Lifecycle
callback exceptions stop polling safely; read failures retain bounded backoff.

## Long choice lists

Pickers never show pages. Render every choice inline, up to
`INLINE_CHOICE_LIMIT` (500, `apps/core/choice_requests.py`), and the browser
filters them instantly. Use `inline_choices(rows)`: it returns the rows and a
`searchable` flag. Only when more than 500 choices exist does the page render a
short preview and set a same-origin source (`data-choice-source` on a single
select, `data-recipient-source` on the checkbox picker, `data-scope-source` on
the shell switchers). Typing then asks that source for one bounded set of
matches; the popup says "Keep typing to narrow the list" when more exist. A
failed search retries once without a button, then shows a plain message; it
never clears a selection.

For the checkbox picker, `data-recipient-mode="choices"` accepts one collection
with `data-recipient-name` set to the checkbox field name. The GET sends `q`,
`page=1` and repeated `selected` values and returns `results` (at most 25
`{value, label}` objects), `selected` (every authorized selected object) and
`pagination`; only `has_next` is read, to show the hint. Preserve unavailable
selections with `data-unavailable-label`.

When the encoded choice URL exceeds 3,000 bytes, the shared picker uses a
read-only JSON POST to the same endpoint, with `X-CSRFToken` from its native
form. Endpoints accept only known parameters and enforce their usual
permissions, CSRF and body limits. Choice reads do not save, send or audit
changes.

Forms and filters that mark `data-auto-filter` (`static/auto-filter.js`) apply
immediately: selects and pickers on change, text boxes after a 400 ms pause or
Enter. They carry no Apply, Go or Search button.
