# README visual proposal — v0.10.1

Status: proposed for Apollo's approval. This document does not change either
README or add animated assets. All demonstrations below use released v0.10.1
behavior and synthetic data.

## Existing image inventory

Line numbers refer to the v0.10.1 README sources before any proposed edits.
These are all rendered image references, including the logo and badges.

| File and line | Section / position | Exact image source |
| --- | --- | --- |
| `README.md:2` | Header logo | `website/static/trellum-lattice.svg` |
| `README.md:22` | Header, Framework CI badge | `https://github.com/trellumhq/trellum/actions/workflows/framework.yml/badge.svg` |
| `README.md:23` | Header, Python badge | `https://img.shields.io/badge/Python-3.11%2B-3776AB?logo=python&logoColor=white` |
| `README.md:24` | Header, license badge | `https://img.shields.io/badge/license-AGPL--3.0--only-0F766E` |
| `README.md:44` | Explore reports on desktop and mobile | `docs/assets/store-health.png` |
| `README.md:45` | Explore reports on desktop and mobile | `docs/assets/report-mobile.png` |
| `README.md:97` | Portal introduction, after Present the decision as a slideshow | `docs/assets/portal-reports.png` |
| `README.md:235` | End of What a report looks like | `docs/assets/portal-overview.png` |
| `trellum/README.md:2` | Header logo | `https://raw.githubusercontent.com/trellumhq/trellum/main/website/static/trellum-lattice.svg` |
| `trellum/README.md:55` | Install, Store Health example | `https://raw.githubusercontent.com/trellumhq/trellum/main/docs/assets/store-health.png` |

There are eight rendered images in the top-level README and two in the
framework README. Neither README currently embeds a GIF. The framework README's
`image: evidence/conversion.png` presentation example (line 2028) and
`evidence/<name>.png` import explanation (line 2047) are authoring examples,
not rendered README images. The public demo and tour hyperlinks are links,
not additional image embeds.

## Earlier portal introduction

Add a short **A shared home for your team's reports** section immediately
after the introductory paragraphs and before **Explore reports on desktop and
mobile** (currently line 40). Move the existing portal-library image and its
tour link here, using the refreshed v0.10.1 image after approval. Suggested copy:

> Build portable reports with the Python framework, then give your team a
> shared home with the optional self-hosted portal. Browse reports, schedule
> builds, manage access, and share a report through an explicitly enabled
> public link. The portal also offers an optional AI assistant configured by
> your administrator. Explore the portal tour or try the synthetic Docker demo.

Link **portal tour** to `https://trellum.dev/tour/` and **synthetic Docker demo**
to `docs/customer/install/try-it.md#try-the-self-hosted-portal`. Preserve the
distinction between the public static report gallery and a running portal.
Keep deployment details in **Optional self-hosted collaboration**. Keep
`trellum/README.md` focused on the standalone framework; no portal GIF embeds
are proposed there.

## Five proposed short GIFs

The following names are proposed future asset names, not files added by this
change. Dimensions describe exported pixels; display at the README content
width. Byte sizes are ceilings to aim for during recording and optimization,
not measurements. Use Trellum Dark for reports and the console’s supported
neutral Dark mode for management pages, a tightly cropped 16:9 viewport, 8–12 fps,
and a readable first frame. Each animation gets descriptive alt text, a short
caption, and a link to the static portal tour so its content is also available
without animation. No numerical result should depend on reading a fleeting frame.

| Proposed asset | Released feature and storyboard | Length | Target | README placement |
| --- | --- | --- | --- | --- |
| `portal-viewer-filters.gif` | Open a synthetic Store Health report from Reports; select one existing dimension filter; show its selected value and the resulting KPI/chart change; reset the filter. Demonstrates interactive built-report filtering inside the authenticated portal, without rebuilding the report. | 8–10 s | 960 × 540, ≤ 2 MiB | In the proposed early portal section, after its library still and introduction. |
| `portal-scheduled-builds.gif` | Open Operations as a developer; show an existing scheduled report and its next run; open its successful build history and log/validation detail. Use an already completed synthetic build so the loop has no artificial waiting. Demonstrates schedule visibility and build inspection; it does not imply that editing a schedule is a portal control. | 10–12 s | 960 × 540, ≤ 2.5 MiB | In Optional self-hosted collaboration, immediately after its feature list. |
| `portal-sharing.gif` | With public sharing already enabled on the local demo and filesystem output storage, open a report's sharing panel; show an expiring link's settings; open the synthetic report through that link; return and revoke it. Demonstrates explicit, revocable public publication of one report. Crop or mask the URL/token and any one-time password throughout the recording. | 12–15 s | 960 × 540, ≤ 3 MiB | After the scheduled-build loop in Optional self-hosted collaboration, with a link to the share-links guide. |
| `portal-access-control.gif` | As an administrator, open Options → Access on a synthetic report; change Signed-in audience to Private and save; assign an existing synthetic Viewer permission group and save; show the resulting audience/group state. Demonstrates item audience and explicit group access, distinct from public sharing. Use an instance configured for private/selected-report access. | 12–15 s | 960 × 540, ≤ 3 MiB | After sharing, with a caption distinguishing signed-in audience from public links. |
| `portal-ai-assistant.gif` | From an accessible synthetic report, open AI assistant; resume a real conversation asking a narrowly scoped question about its built output; show the answer and its source/evidence context. Demonstrates report-bound questions using the viewer's existing access, rather than report authoring. Do not present a staged response as live model output. | 10–12 s | 960 × 540, ≤ 2.5 MiB | Last in Optional self-hosted collaboration, beside a short explanation that an administrator must configure a model provider. |

Keep the total five-GIF payload at or below about 13 MiB. If legibility requires
exceeding these limits, shorten the sequence or retain a still and link to the
tour rather than reducing text to unreadable pixels. The README should not
duplicate the entire tour.

## Release evidence and recording prerequisites

These claims are supported by the checked-in v0.10.1 documentation and
implementation:

- **Viewer filters:** [framework FilterBar contract](../../trellum/README.md#datasource-and-filterbar)
  and the runtime in `trellum/static/`. Show only filters the chosen report
  actually defines.
- **Scheduled builds:** [product tour](../product-tour.md), Operations in
  `apps/reports/views.py` (`operations`, `api_system_status`, `_next_cron_fire`),
  and the report model's schedule fields in `apps/reports/models.py`.
  Schedules originate in report definitions; the proposed loop inspects their
  released Operations presentation.
- **Sharing:** [share-links guide](../customer/portal/share-links.md) and
  `apps/reports/views.py` (`api_share_links`, `api_share_link_revoke`,
  `share_entry`). Public sharing requires organization opt-in. Anonymous share
  links are unavailable with S3 output storage. Internal audience changes do
  not revoke existing public links.
- **Access control:** [access-control guide](../customer/portal/access-control.md),
  `apps/core/report_access.py`, and `apps/orgs/groups/views.py`. Private items
  remain accessible to Developers and administrators; an explicitly assigned
  Viewer group can access its selected item. Do not caption this as hiding a
  report from administrators.
- **Assistant:** [AI assistant guide](../customer/portal/ai-assistant.md),
  `apps/assistant/views.py`, `apps/assistant/tools.py`, and
  `apps/assistant/answer_review.py`. The assistant reads accessible built report
  data; it does not edit repository source. Provider configuration is optional,
  and model explanations can be mistaken. Public share pages and exported
  reports do not include the authenticated assistant.

The assistant loop needs a configured provider and a genuine synthetic
conversation. No provider calls or credential changes are needed to review
this proposal. If that recording cannot be made within an approved
demo setup, keep its tour still and mark the animation pending rather than
inventing an answer. The other four loops need only local synthetic data;
sharing needs no external recipient and access control needs no real account.

## Approval and deliverables

Apollo approves the placement and five sequences before README embeds are
changed. A later visual-only change can add optimized GIFs and captions,
verify every relative path, and review the rendered README at desktop and
mobile widths. Draft recordings, if made for that review, belong under
`docs/proposals/readme-visuals/` until approved. No draft GIFs are included
with this proposal.
