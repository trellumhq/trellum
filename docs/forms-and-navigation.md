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
