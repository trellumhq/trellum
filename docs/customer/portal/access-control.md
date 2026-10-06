# Access control

## Roles

At the organization level a person is a **member** or an **admin**. Inside a
studio the roles are, in increasing order:

| Role | Can |
|---|---|
| `viewer` | Read reports |
| `developer` | Trigger builds, upload data-source files, test connections, fetch and publish from git |
| `admin` | Everything, including the studio settings pages — repository, data-source credentials — and studio membership |

An organization admin is implicitly an admin of every studio in that
organization.

## How effective access is computed

Access is the **highest** role granted by any of these paths:

1. Direct studio membership
2. A permission group that grants a role on that studio
3. A permission group's *default studio role*, which applies to every current
   and future studio in the organization

The Members page shows each person's effective access with its provenance, so
you can see which of the three paths produced it.

## Permission groups

Reusable, organization-scoped bundles of studio grants. Assign a group to
someone directly, or map it from an SSO group so membership is maintained by
your identity provider. Groups are the right tool whenever more than a couple
of people should share the same access.

Group details have four sections: **Overview**, **Members**, **Access**, and
**Effective access**. Organization administrators manage the group and its
members. Effective access explains the combined permissions of a selected
person, including direct memberships and other groups.

### All reports or selected reports

A group's Viewer grant for a studio can cover **All reports** or **Selected
reports**. Existing studio memberships and grants continue to cover all
reports. Developer and Admin roles always cover all reports.

Selected grants combine: membership in two groups gives access to both groups'
selections. A full-studio grant from any source still gives access to every
report; a selected grant cannot narrow it. New reports are not automatically
added to selected grants.

To give someone a selected set of reports:

1. Invite them as an organization member, without a direct studio role.
2. Add them to a permission group.
3. Under the group's **Access**, add a Viewer studio grant and choose
   **Selected reports**.
4. Select the reports and save.
5. Check **Effective access** for that person to identify any broader grants.

The person can enter that studio, view their reports, ask the report-specific
AI assistant and configure deliveries. Studio-wide operations, metrics,
annotations, experiments, analytics and settings are unavailable to selected
viewers.

Organization and studio administrators can also open **Options → Access** on
a report to assign existing groups. A studio administrator can create a
selected Viewer grant when the group has none in that studio, but cannot
change an existing broader grant or the group's membership.

Public share and embed links are separate, explicit publication. Changing
internal group assignments does not revoke those links. The Access page
distinguishes internal permissions from public sharing.

Permission groups, SSO, Security settings, and Audit Log are available in every
installation. Access remains controlled by the organization and studio roles
described above.

## Invites

An invitation (Organization settings → Invites) carries the org role,
permission groups, and per-studio roles, applied the moment it's accepted.
Invites always show a copyable link, and are also emailed once mail delivery
is configured. Password reset works the same way — from the login page, once
mail is configured; without it, send a fresh invite instead.

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
