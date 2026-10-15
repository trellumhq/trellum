# Report Analytics

**Report Analytics** gives studio developers and administrators an aggregate
view of report readership. Choose a 7, 30, or 90 day window to see views,
unique viewers, share views, and each report's last view. Reports with no
views remain in the table, and a recently built report with no views in the
last 30 days is marked **unseen**.

View counting has clear limits:

- A member's repeat visits to the same report within the same clock-hour bucket
  count once.
- Anonymous visits are counted by share link. “Unique viewers” combines
  distinct member accounts and distinct share links; it cannot identify
  individual people who use the same anonymous link.
- The portal records page views, not time spent or active reading time.
- Daily view totals are retained as rollups. The raw events used for unique
  member and share-link counts are retained for up to 90 days by default;
  the installation's `RETENTION_REPORT_VIEW_EVENT_DAYS` setting can shorten
  that window. See [Data retention](/docs/latest/operations/data-retention/).

The page is restricted to studio developers and administrators. It shows
counts and timestamps, not viewer names or IP addresses. View events are
stored by the self-hosted portal; Trellum does not send them to an external
analytics service. Authenticated report views may also appear as hourly
deduplicated `report.view` events in the portal's audit history.

## Idle reports stop auto-refreshing

Scheduled reports that nobody opens still cost warehouse time. To avoid that,
Trellum pauses the scheduled refresh of a report once it has gone unviewed for
a set number of days.

Organization admins set the threshold under **Org settings → Auto-refresh**.
The default is 60 days, and `0` means reports are never paused.

- Views from the portal and from share links both count as activity. A new
  report gets a full window before it can be paused.
- A report with an email delivery or a scheduled alert is exempt, because the
  people who receive those never open the portal.
- A paused report shows an **Auto-refresh paused** badge. Its schedule in
  `report.yaml` is unchanged, but it no longer fires.
- Opening a paused report does not restart it, and neither does **Run now**,
  which builds the report once. Only a developer pressing **Resume** restarts
  the schedule. Resume also queues an immediate rebuild.
