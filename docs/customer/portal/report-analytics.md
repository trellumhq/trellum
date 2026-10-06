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
