# Access control

## Roles

At the organization level a person is a **member** or an **admin**. Inside a
studio the roles are, in increasing order:

| Role | Can |
|---|---|
| `viewer` | Read accessible reports and analyses, use their filters and exports, ask the AI assistant questions, and read shared report deliveries |
| `developer` | Viewer capabilities plus shared delivery schedule management, Operations, build history and logs, trigger builds, upload data-source files, test connections, fetch and publish from git |
| `admin` | Everything, including the studio settings pages — repository, data-source credentials — and studio membership |

An organization admin is implicitly an admin of every studio in that
organization.

Operations and its status, history and run-statistics endpoints require a
developer or administrator. Viewers still see basic report availability and
freshness in the report catalog, but no operational navigation or controls.
Developers can manage alert rules and report sharing under the organization's
policy; data-source credentials and membership remain administrator settings.

The AI assistant uses these same roles. Enabling its actions allows proposals;
it does not promote a viewer or developer. The server checks current access
when a tool is dispatched and immediately before a change executes. Shared
organization credentials require organization admin access. A role change can
block a pending action; it cannot undo a change already executed.

## How effective access is computed

Access is the **highest** role granted by any of these paths:

1. Direct studio membership
2. A permission group that grants a role on that studio
3. A permission group's *default studio role*, which applies to every current
   and future studio in the organization

The Members page shows each person's effective access with its provenance, so
you can see which of the three paths produced it.

After upgrading from a release that stored personal themes on studio
memberships, review direct memberships against your approval records. The
upgrade preserves those memberships and roles because it cannot reliably
distinguish an approved grant from a row created while saving a theme; it
never removes or downgrades access automatically.

## Permission groups

Reusable, organization-scoped bundles of studio grants. Assign a group to
someone directly, or map it from an SSO group so membership is maintained by
your identity provider. Groups are the right tool whenever more than a couple
of people should share the same access.

Group details have four sections: **Overview**, **Members**, **Access**, and
**Effective access**. Organization administrators manage the group and its
members. Effective access explains the combined permissions of a selected
person, including direct memberships and other groups.

### Studio audience, private items, and selected reports

A report or analysis is either in the **Studio audience** or **Private**. The
Studio audience includes signed-in Viewers with access to the whole studio; it does not
make content available on the internet. Public share links and embeds are a
separate, explicit publication.

Studio administrators set independent defaults for **New reports** and **New
analyses** in **Studio settings → Repository → Default audiences**. The
defaults apply when an item is first discovered, including the first import
from Git. They are saved even when no repository is configured. Changing a
default affects future items only. Rebuilds and later Git changes keep an
item's current audience.

Administrators can change an individual item's **Signed-in audience** from
**Options → Access**. The control is labelled **Report audience** or
**Analysis audience**. A Private item is hidden from ordinary Viewers,
including Viewers whose studio access comes from a full-studio grant or the
organization's default Viewer group. Developers and studio or organization
administrators can still access it. Administrators can also explicitly assign
a permission group to a Private item; this can grant access to a Viewer group
without making the item visible to other studio Viewers.

A group's Viewer grant for a studio can also cover **All reports** or
**Selected reports**. Existing full-studio grants cover every item in the
Studio audience. A Private item remains private unless the group is explicitly
assigned to it. Developer and Admin roles always cover all items.

Selected grants combine: membership in two groups gives access to both groups'
selections. A full-studio grant from any source still gives access to every
item in the Studio audience; a selected grant cannot narrow it. New reports
are not automatically added to selected grants.

To give someone a selected set of reports:

1. Invite them as an organization member, without a direct studio role.
2. Add them to a permission group.
3. Under the group's **Access**, add a Viewer studio grant and choose
   **Selected reports**.
4. Select the reports and save.
5. Check **Effective access** for that person to identify any broader grants.

The person can enter that studio, view their reports, ask the report-specific
AI assistant and read shared deliveries. Developers and admins configure
shared schedules for accessible reports. Studio-wide operations, metrics,
annotations, experiments, analytics and settings are unavailable to selected
viewers.

When a studio contains Private items, ordinary Viewers use these same scoped
surfaces: their report and analysis lists contain only accessible items, and
their AI assistant works within one accessible item at a time. Studio-wide
summaries remain available to Developers and administrators.

Organization and studio administrators can open **Options → Access** on a
report or analysis to change its audience and assign existing groups. A studio
administrator can create an item-specific Viewer grant when the group has no
grant in that studio, but cannot change an existing broader studio grant or
the group's membership.

Public share and embed links are separate, explicit publication. Changing an
item's internal audience or group assignments does not revoke those links.
The Access page shows active public links separately. Review or revoke those
links when an item should no longer be available through them.

Permission groups, SSO, Security settings, and Audit Log are available in every
installation. Access remains controlled by the organization and studio roles
described above.

## Invites

An invitation (Organization settings → Invites) carries the org role,
permission groups, and per-studio roles, applied the moment it's accepted.
Invites always show a copyable link, and are also emailed once mail delivery
is configured. **Resend email** on a pending invitation can be used once a
minute. Password reset works the same way — from the login page, once mail is
configured; without it, send a fresh invite instead. The reset page always
answers the same way, whether or not the address has an account, and sends at
most one email per address every two minutes (and a limited number of requests
per client address); see `PASSWORD_RESET_*` in
[Configuration](/docs/latest/install/configuration/).

## Audit log

Every mutating action within an organization is recorded automatically and
visible to org admins at **Organization settings → Audit log**. See
[Logs & monitoring](/docs/latest/operations/logs-and-monitoring/) for what it
covers and how client addresses are attributed.

## No grant means invisible

Requesting something you have no grant on returns **404, not 403**. The portal
does not confirm that a studio or report exists to someone who cannot see it,
so URLs cannot be used to enumerate an organization's structure.

!!! note
    This is why a colleague may report a "missing" report that works for you.
    Check their effective access on the Members page before assuming a bug.
