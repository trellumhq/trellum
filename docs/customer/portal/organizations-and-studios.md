# Organizations & studios

{{BRAND}} has three levels: **organization → studio → report**.

## Organization

The billing, identity and administration boundary. An organization owns its
members, its permission groups, its SSO configuration, and its audit log.
Organization admins have full access to everything inside it.

## Studio

The sharing boundary, and the unit a git repository connects to. A studio has
its own members, its own repository, and its own data sources. Most teams run
one studio per team or per domain — marketing, finance, product.

Studio slugs are immutable: they key directory paths on disk, so renaming
would move every built report. Choose the slug deliberately; the display name
can change freely.

## Report

A directory in your repository — `report.yaml`, a Python generator, SQL — that
builds into a served dashboard. Reports are grouped by the category and tags
declared in `report.yaml`, not by a folder structure in the UI.

## What lives where

| Concern | Level |
|---|---|
| Members, invitations, SSO, audit log | Organization |
| Permission groups | Organization (granted per studio) |
| Git repository, schedules, reports | Studio |
| Data sources | Either — a studio source shadows an organization source of the same name |

Defining data sources at the organization level and letting studios inherit
them is the usual pattern; studios override only when they genuinely need a
different connection.

Organization sources are marked **shared** and are the right home for a file
that several studios read — upload it once and every studio in the
organization can query it, subject to the organization's storage limit. A
studio-level source with the same name shadows the shared one, so a team can
point at its own copy without renaming anything in the reports.

## Saving settings

Organization and studio configuration changes stay pending until you press
**Save changes**. An **Unsaved changes** indicator appears when a form has edits;
changing its fields back to their original values clears the indicator.
Report sharing saves its related settings together. If saving fails, your edits
remain available to correct or retry.

Buttons that perform another action keep that action in their label, such as
**Create invitation**, **Revoke**, or **Save and test** for data-source credentials.
Personal display preferences, such as theme and list/grid view, apply immediately.

## Configuring a data source

A studio's sources are declared in its repository; the portal supplies the
credentials. A source the repository declares shows its type and connection
details read-only and asks only for the secrets. **Organization settings →
Data sources** takes the same credentials once for every studio that declares
that name, and its own form shows the full set of fields relevant to the type
you pick, since nothing else declares them.

Credentials are stored encrypted and injected only into the report builds that
resolve to that source, never anywhere else, and every source has a **Test**
button that does a real connection check.

Full detail:
[Connecting your data sources](/docs/latest/portal/connecting-your-data-sources/).

## Next

- [Access control](/docs/latest/portal/access-control/)
- [Connecting a repository](/docs/latest/portal/connecting-a-repository/)
- [Connecting your data sources](/docs/latest/portal/connecting-your-data-sources/)
